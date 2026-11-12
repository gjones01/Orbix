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
                project_star = """
                const g=window.__orbix.state.globe, c=g.camera(); c.updateMatrixWorld();
                const s=g.scene().getObjectByName('orbix-starfield');
                return c.position.clone().fromArray(s.geometry.attributes.position.array)
                  .transformDirection(c.matrixWorldInverse).applyMatrix4(c.projectionMatrix).toArray();
                """
                initial = driver.execute_script(project_star)
                driver.execute_script("window.__orbix.state.globe.pointOfView({lat:25,lng:90,altitude:3},0)")
                time.sleep(.2)
                rotated = driver.execute_script(project_star)
                assert rotated != initial
                driver.execute_script("window.__orbix.state.globe.pointOfView({lat:25,lng:90,altitude:6},0)")
                time.sleep(.2)
                zoomed = driver.execute_script(project_star)
                assert all(abs(a-b)<1e-6 for a,b in zip(rotated, zoomed))
                assert driver.execute_script("return Array.from(window.__orbix.layer.positions)") == before
                assert driver.execute_script("return window.__orbix.state.globe.scene().getObjectByName('orbix-starfield').geometry.attributes.position.count") == 900
                driver.save_screenshot(str(ROOT / 'docs/screenshots/starfield-globe.png'))
                driver.switch_to.default_content()
                driver.find_element(By.XPATH, "//button[@role='tab' and normalize-space(.)='Analysis']").click()
                driver.switch_to.frame(frame)
                wait.until(lambda d: d.execute_script("return !window.__orbix.motion.active"))
                driver.switch_to.default_content()
                driver.find_element(By.XPATH, "//button[@role='tab' and normalize-space(.)='Globe']").click()
                driver.switch_to.frame(frame)
                wait.until(lambda d: d.execute_script("return window.__orbix.motion.active"))
                driver.execute_script("window.__orbix.select(0)")
                wait.until(lambda d: d.execute_script("return window.__orbix.layer.material.uniforms.reveal.value===1"))
                assert driver.execute_script("return window.__orbix.layer.material.uniforms.focus.value===1")
                driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
                time.sleep(.2)
                assert driver.execute_script("return window.__orbix.motion.reduced")
                driver.execute_script("window.__orbix.select(1)")
                assert driver.execute_script("return window.__orbix.layer.material.uniforms.reveal.value===1")
                report["reduced_motion"] = "passed"
                report["sky_rotation_and_zoom"] = "passed"
                report["satellite_positions_unchanged"] = True
                report["hidden_motion_pause"] = "passed"
                report["selection_uniforms"] = "passed"
            if driver.execute_script("return !!window.__orbix.state.globe.scene().getObjectByName('orbix-sun')"):
                solar = driver.execute_async_script("""
                  const done=arguments[0];
                  import('./sunlight.js').then(m => done([
                    m.solarPosition(new Date('2026-03-20T12:00:00Z')),
                    m.solarPosition(new Date('2026-06-21T12:00:00Z')),
                    m.solarPosition(new Date('2026-12-21T12:00:00Z'))
                  ])).catch(e=>done({error:String(e)}));
                """)
                assert abs(solar[0]['lat']) < .5 and abs(solar[0]['lng']) < 3, solar
                assert 23 < solar[1]['lat'] < 24 and -24 < solar[2]['lat'] < -23, solar
                lighting = driver.execute_script("""
                  const o=window.__orbix,g=o.state.globe; o.setTime(0);
                  const s=g.scene().getObjectByName('orbix-sun'), l=g.lights();
                  return {lights:l.length, aligned:l[0].position.clone().normalize().distanceTo(s.material.uniforms.direction.value),
                    atmosphere:g.showAtmosphere(), background:getComputedStyle(document.body).backgroundColor,
                    direction:s.material.uniforms.direction.value.toArray()};
                """)
                assert lighting['lights'] == 2 and lighting['aligned'] < 1e-10
                assert driver.execute_script("return window.__orbix.state.globe.lights().some(l=>l.name==='orbix-night-fill' && l.intensity>0 && l.intensity<0.5)")
                assert not lighting['atmosphere'] and lighting['background'] == 'rgb(0, 0, 0)'
                driver.execute_script('window.__orbix.setTime(1)')
                assert driver.execute_script("return window.__orbix.state.globe.scene().getObjectByName('orbix-sun').material.uniforms.direction.value.toArray()") != lighting['direction']
                driver.execute_script('window.__orbix.setTime(0)')
                assert driver.execute_script("return window.__orbix.state.globe.scene().getObjectByName('orbix-sun').material.uniforms.direction.value.toArray()") == lighting['direction']
                for name, angle in [('day-night', 90), ('sun-in-sky', 155)]:
                    driver.execute_script("""
                      const g=window.__orbix.state.globe;
                      const p=g.scene().getObjectByName('orbix-sun').userData.subsolar;
                      g.pointOfView({lat:arguments[0]===90?0:-p.lat,lng:p.lng+arguments[0],altitude:3},0);
                    """, angle)
                    time.sleep(.4)
                    driver.save_screenshot(str(ROOT / f'docs/screenshots/{name}.png'))
                    if name == 'sun-in-sky':
                        from io import BytesIO
                        from PIL import Image
                        point = driver.execute_script("""
                          const g=window.__orbix.state.globe,c=g.camera();c.updateMatrixWorld();
                          const d=g.scene().getObjectByName('orbix-sun').material.uniforms.direction.value;
                          const p=d.clone().transformDirection(c.matrixWorldInverse).applyMatrix4(c.projectionMatrix);
                          return [p.x,p.y];
                        """)
                        assert all(abs(v)<1 for v in point), point
                        pixels = Image.open(BytesIO(driver.find_element(By.TAG_NAME, 'canvas').screenshot_as_png)).convert('RGB')
                        x,y = int((point[0]+1)*pixels.width/2),int((1-point[1])*pixels.height/2)
                        patch = pixels.crop((x-5,y-5,x+6,y+6))
                        assert any(r>220 and g>200 and b>160 for r,g,b in patch.getdata()), 'Sun disc not visible at its light direction'
                report['sunlight_alignment_time_and_seasons'] = 'passed'
                if driver.execute_script("return !!window.__orbix.state.globe.scene().getObjectByName('orbix-lens-flare')"):
                    from PIL import ImageChops
                    for view, angle, expected in [('visible',155,True),('earth-blocked',180,False),('behind-camera',0,False)]:
                        driver.execute_script("""
                          const g=window.__orbix.state.globe,p=g.scene().getObjectByName('orbix-sun').userData.subsolar;
                          g.pointOfView({lat:arguments[0]===0?p.lat:-p.lat,lng:p.lng+arguments[0],altitude:3},0);
                          g.scene().getObjectByName('orbix-lens-flare').visible=true;
                        """,angle)
                        time.sleep(.3)
                        canvas = driver.find_element(By.TAG_NAME,'canvas')
                        with_flare = Image.open(BytesIO(canvas.screenshot_as_png)).convert('RGB')
                        driver.execute_script("window.__orbix.state.globe.scene().getObjectByName('orbix-lens-flare').visible=false")
                        time.sleep(.1)
                        without_flare = Image.open(BytesIO(canvas.screenshot_as_png)).convert('RGB')
                        difference = ImageChops.difference(with_flare,without_flare)
                        changed = sum(max(pixel)>8 for pixel in difference.getdata())
                        print(f'Flare pixel check {view}: {changed} pixels', flush=True)
                        if (changed>100) != expected:
                            difference.save(str(ROOT / f'docs/screenshots/flare-difference-{view}.png'))
                        assert (changed>100) == expected, f'Unexpected flare visibility: {view}'
                    driver.execute_script("window.__orbix.state.globe.scene().getObjectByName('orbix-lens-flare').visible=true")
                    report['lens_flare_visible_and_occluded_pixels'] = 'passed'
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
