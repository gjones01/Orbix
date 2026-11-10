"""Comparable offline all-group motion benchmark; no CelesTrak/LLM requests."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from smoke_test_ui import wait_for_health

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="orbix-motion-") as directory:
        entry = Path(directory) / "offline.py"
        entry.write_text(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
                         "from datetime import datetime, timezone\n"
                         "from src.ui.loading import load_constellation_cached\n"
                         "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,datetime(2026,11,10,17,0,tzinfo=timezone.utc))\n"
                         "app.main()\n", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(entry), "--server.port", "8527", "--server.headless", "true"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health("http://localhost:8527", 45)
            opts = Options()
            for flag in ("--headless=new", "--window-size=1600,1000", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"):
                opts.add_argument(flag)
            opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
            driver = webdriver.Chrome(options=opts)
            driver.get("http://localhost:8527")
            wait = WebDriverWait(driver, 60)
            frame = wait.until(lambda d: next((f for f in d.find_elements(By.TAG_NAME, "iframe") if "orbix_globe" in (f.get_attribute("src") or "")), None))
            driver.switch_to.frame(frame)
            wait.until(lambda d: d.execute_script("return window.__orbix && window.__orbix.counts.total > 10000"))
            time.sleep(3)
            driver.execute_script("window.__orbix.setPlaying(true)")
            samples = []
            for _ in range(3):
                samples.append(driver.execute_async_script("""
                const done=arguments[arguments.length-1], start=performance.now(); let last=start, times=[];
                function step(now) {
                  times.push(now-last); last=now;
                  window.dispatchEvent(new PointerEvent('pointermove',{clientX:innerWidth*(.5+.3*Math.sin(now/600)),clientY:innerHeight*.4,pointerType:'mouse'}));
                  if(now-start<3000) requestAnimationFrame(step);
                  else {times.sort((a,b)=>a-b); done({fps:times.length*1000/(now-start),p95_ms:times[Math.floor(times.length*.95)], update_ms:window.__orbix.perf.updateMs,draw_calls:window.__orbix.state.globe.renderer().info.render.calls});}
                } requestAnimationFrame(step);
                """))
            report = {"satellites": driver.execute_script("return window.__orbix.counts.total"), "viewport": "1600x1000", "renderer": "Chrome headless / SwiftShader", "samples": samples}
            if driver.execute_script("return !!window.__orbix.motion"):
                driver.execute_script("window.__orbix.setPlaying(false)")
                before = driver.execute_script("return Array.from(window.__orbix.layer.positions)")
                driver.execute_script("window.dispatchEvent(new PointerEvent('pointermove',{clientX:innerWidth,clientY:innerHeight,pointerType:'mouse'}))")
                time.sleep(.5)
                assert driver.execute_script("return Array.from(window.__orbix.layer.positions)") == before
                assert driver.execute_script("return Math.abs(window.__orbix.motion.offset.x)>0")
                driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
                time.sleep(.2)
                assert driver.execute_script("return window.__orbix.motion.reduced && window.__orbix.motion.offset.x===0")
                report["reduced_motion"] = "passed"
                report["positions_unchanged_by_parallax"] = True
            errors = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
            assert not errors, errors
            Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        finally:
            if driver:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
