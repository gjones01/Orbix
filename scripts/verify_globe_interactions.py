"""Offline regression for occluded picks and drag-release selection."""
from pathlib import Path
from io import BytesIO
import subprocess
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from smoke_test_ui import wait_for_health
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='orbix-interactions-') as directory:
        entry = Path(directory) / 'offline.py'
        entry.write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
            "from src.ui.loading import load_constellation_cached\n"
            "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)\n"
            "app.main()\n", encoding='utf-8')
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(entry),
                                   '--server.port', '8531', '--server.headless', 'true'],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health('http://localhost:8531', 45)
            options = Options()
            for flag in ('--headless=new', '--window-size=1600,1000', '--use-gl=angle',
                         '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'):
                options.add_argument(flag)
            options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
            driver = webdriver.Chrome(options=options)
            driver.get('http://localhost:8531')
            wait = WebDriverWait(driver, 90)
            frame = wait.until(lambda d: next((f for f in d.find_elements(By.TAG_NAME, 'iframe')
                if 'orbix_globe' in (f.get_attribute('src') or '')), None))
            driver.switch_to.frame(frame)
            wait.until(lambda d: d.execute_script("return window.__orbix && document.getElementById('stage').classList.contains('ready')"))
            wait.until(lambda d: d.execute_script("return getComputedStyle(document.getElementById('stage')).opacity==='1'"))
            driver.execute_script("window.__orbix.setPlaying(false);window.__orbix.state.globe.pointOfView({lat:0,lng:0,altitude:3},0)")
            time.sleep(.2)
            result = driver.execute_script("""
              const o=window.__orbix,l=o.layer,c=o.state.globe.camera(),canvas=document.querySelector('canvas');
              c.updateMatrixWorld();
              const oldFlags=l.visible.slice(), oldPositions=l.positions.slice();
              l.visible.fill(0);l.visible[0]=1;
              function pick(x,y,z) {
                l.positions.set([x,y,z],0);l.geometry.computeBoundingSphere();
                const projected=c.position.clone().set(x,y,z).project(c);
                return l.pick(projected.x,projected.y,c,canvas.clientHeight);
              }
              const result={back:pick(0,0,-110),front:pick(0,0,110),limb:pick(120,0,0)};
              l.visible[0]=0; result.filtered=pick(0,0,110);
              l.visible.set(oldFlags);l.positions.set(oldPositions);l.geometry.computeBoundingSphere();
              return result;
            """)
            assert result == {'back': -1, 'front': 0, 'limb': 0, 'filtered': -1}, result

            # A zero-size GPU point may be clamped to one pixel by WebGL. Hidden
            # groups must render exactly like removing the marker draw call.
            driver.execute_script("const o=window.__orbix; o.state.globe.pauseAnimation(); window.savedVisibility=o.layer.visible.slice();o.layer.visible.fill(0);o.setTime(0);o.state.globe.renderer().render(o.state.globe.scene(),o.state.globe.camera())")
            time.sleep(.2)
            canvas = driver.find_element(By.TAG_NAME,'canvas')
            hidden_groups = Image.open(BytesIO(canvas.screenshot_as_png)).convert('RGB')
            driver.execute_script('const o=window.__orbix;o.layer.points.visible=false;o.state.globe.renderer().render(o.state.globe.scene(),o.state.globe.camera())')
            time.sleep(.1)
            removed_layer = Image.open(BytesIO(canvas.screenshot_as_png)).convert('RGB')
            difference = ImageChops.difference(hidden_groups,removed_layer)
            # Canvas element screenshots include overlapping HTML HUD counters.
            # Compare the globe region, excluding the independently updating FPS.
            difference = difference.crop((0,int(difference.height*.25),difference.width,int(difference.height*.80)))
            changed = sum(max(pixel)>8 for pixel in difference.getdata())
            assert changed < 10, f'Hidden satellite groups still render: {changed} pixels'
            driver.execute_script('window.__orbix.layer.points.visible=true;window.__orbix.layer.visible.set(window.savedVisibility);window.__orbix.setTime(0);window.__orbix.state.globe.resumeAnimation()')

            # Return a known target from picking so this tests gestures independent
            # of satellite motion and random screen density, using real mouse input.
            before = driver.execute_script("window.__orbix.layer.pick=()=>0; return window.__orbix.state.selectionEvent||0")
            canvas = driver.find_element(By.TAG_NAME, 'canvas')
            ActionChains(driver).move_to_element(canvas).click_and_hold().move_by_offset(80,0).release().perform()
            assert driver.execute_script('return window.__orbix.state.selectionEvent||0') == before
            ActionChains(driver).move_to_element(canvas).click().perform()
            wait.until(lambda d: d.execute_script('return window.__orbix.state.selectionEvent||0') == before+1)
            errors = [e for e in driver.get_log('browser') if e['level'] == 'SEVERE']
            assert not errors, errors
            print('PASS: Earth occludes picking; front/limb and filters work; drag does not select; click still selects')
        finally:
            if driver:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    main()
