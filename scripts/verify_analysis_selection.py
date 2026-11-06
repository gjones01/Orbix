"""Real tab-switch regression for Analysis selections, using offline snapshots."""
from __future__ import annotations

import json
import re
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
    with tempfile.TemporaryDirectory(prefix="orbix-selection-") as directory:
        entry = Path(directory) / "offline.py"
        entry.write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
            "from src.ui.loading import load_constellation_cached\n"
            "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)\n"
            "app.main()\n", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(entry),
                                   "--server.port", "8524", "--server.headless", "true"],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health("http://localhost:8524", 45)
            options = Options()
            for option in ("--headless=new", "--window-size=1600,1000", "--use-gl=angle",
                           "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"):
                options.add_argument(option)
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, 45)
            driver.get("http://localhost:8524")

            def globe(script):
                driver.switch_to.default_content()
                frames = driver.find_elements(By.TAG_NAME, "iframe")
                target = next((f for f in frames if "orbix_globe" in (f.get_attribute("src") or "")), None)
                if target is None:
                    return None
                driver.switch_to.frame(target)
                result = driver.execute_script(script)
                driver.switch_to.default_content()
                return result

            wait.until(lambda d: globe("return window.__orbix && window.__orbix.counts.total > 0;"))
            driver.find_element(By.XPATH, "//label[contains(., 'Starlink')]").click()
            wait.until(lambda d: globe("return window.__orbix.counts.total > 10000;"))

            def click_element(element):
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                element.click()

            def tab(name):
                click_element(driver.find_element(By.XPATH, f"//button[@role='tab' and normalize-space(.)='{name}']"))

            def click(key):
                click_element(wait.until(lambda d: d.find_element(By.CSS_SELECTOR, f".st-key-{key} button")))

            def probe():
                return globe("""
                const o=window.__orbix, i=o.state.selection, p=o.layer.positions;
                const camera=o.state.globe.camera();
                const point=camera.position.clone().set(p[i*3],p[i*3+1],p[i*3+2]);
                const screen=point.clone().project(camera);
                const canvas=document.querySelector('canvas');
                return {id:o.state.selectedId, panel:document.getElementById('selected-name').textContent,
                    visible:o.layer.visible[i], position:point.toArray(), projected:screen.toArray(),
                    camera:o.state.globe.pointOfView(), width:canvas.clientWidth,height:canvas.clientHeight,
                    paths:o.state.globe.pathsData().length, scene:o.state.sceneId, time:o.state.tNorm,
                    action:o.state.lastAnalysisAction, playing:o.state.playing,
                    labelled:!document.getElementById('selection-marker').hidden,
                    label:document.getElementById('selection-marker').textContent};
                """)

            def select(key, expected=None):
                before = globe("return window.__orbix.state.lastAnalysisAction;") or 0
                click(key)
                wait.until(lambda d: (globe("return window.__orbix.state.lastAnalysisAction;") or 0) > before)
                tab("Globe")
                wait.until(lambda d: globe("return !document.getElementById('selection-marker').hidden;"))
                result = probe()
                assert result["visible"] == 1 and result["labelled"]
                assert abs(result["projected"][0]) < .8 and abs(result["projected"][1]) < .8
                assert -1 < result["projected"][2] < 1 and result["width"] > 500
                assert str(result["id"]) in result["label"] and result["paths"] == 2
                if expected is not None:
                    assert result["id"] == expected
                return result

            # Fresh session: no prior globe click to prime the bridge.
            tab("Analysis")
            report = {"farthest": select("analysis_farthest_view")}
            driver.save_screenshot(str(ROOT / "docs/screenshots/selection-globe.png"))
            driver.set_window_size(1366, 900)
            wait.until(lambda d: probe()["width"] < report["farthest"]["width"])
            assert probe()["labelled"] and abs(probe()["projected"][0]) < .8
            driver.save_screenshot(str(ROOT / "docs/screenshots/selection-globe-1366.png"))
            driver.set_window_size(1600, 1000)
            target = report["farthest"]["id"]

            # A hidden group and a repeat of the same satellite must still reveal it.
            globe("const o=window.__orbix; o.toggleGroup(o.state.groupSpecs[o.metadata[o.state.selection].g].id,false); o.setTime(.25);")
            tab("Analysis")
            report["hidden_group_reselected"] = select("analysis_farthest_view", target)
            assert report["hidden_group_reselected"]["time"] == .25

            # Ordinary reruns must not replay the one-shot focus after a user rotates.
            globe("window.__orbix.state.globe.pointOfView({lat:17,lng:30,altitude:3},0);")
            tab("Analysis")
            click_element(driver.find_element(By.XPATH, "//label[normalize-space(.)='mi']"))
            wait.until(lambda d: "mi" in d.find_element(By.CSS_SELECTOR, ".ox-analysis-metrics").text)
            tab("Globe")
            view = probe()["camera"]
            assert abs(view["lat"] - 17) < .01 and abs(view["lng"] - 30) < .01
            report["ordinary_rerun_preserves_camera"] = True

            # Real unusual-satellite and individual-satellite buttons, then real tabs.
            tab("Analysis")
            click("analysis_investigate_button")
            wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".st-key-analysis_unusual_view button").is_displayed())
            choice = driver.find_element(By.CSS_SELECTOR, ".st-key-analysis_unusual_satellite").text
            report["unusual"] = select("analysis_unusual_view", int(re.search(r"NORAD (\d+)", choice).group(1)))
            tab("Analysis")
            click_element(driver.find_element(By.XPATH, "//summary[contains(.,'Explore individual satellites')]"))
            wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".st-key-analysis_satellite_view button").is_displayed())
            choice = driver.find_element(By.CSS_SELECTOR, ".st-key-analysis_satellite").text
            report["individual"] = select("analysis_satellite_view", int(re.search(r"NORAD (\d+)", choice).group(1)))

            # An Analysis action during playback keeps the existing scene running.
            globe("window.__orbix.setPlaying(true);")
            tab("Analysis")
            report["during_playback"] = select("analysis_farthest_view", target)
            assert report["during_playback"]["playing"]
            assert report["during_playback"]["scene"] == report["farthest"]["scene"]
            globe("window.__orbix.setPlaying(false);")

            # Group-level discovery restores a locally hidden group too.
            globe("window.__orbix.toggleGroup('weather',false);")
            tab("Analysis")
            before = probe()["action"]
            click("analysis_discovery_group")
            wait.until(lambda d: globe("return window.__orbix.state.lastAnalysisAction;") > before)
            tab("Globe")
            group = globe("""
                const o=window.__orbix;
                return {enabled:[...o.state.enabledGroups], count:o.highlightCount,
                    shown:[...o.layer.highlighted].filter((x,i)=>x && o.layer.visible[i]).length};
            """)
            assert "weather" in group["enabled"] and group["count"] == group["shown"] > 0
            report["group_discovery"] = group
            print(json.dumps(report, indent=2))
            print("PASS: real tab switches, visible labels, hidden groups, repeat selections and playback")
        finally:
            if driver:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
