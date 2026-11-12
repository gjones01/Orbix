"""Cold-load scene reveal with delayed assets, normal and reduced motion."""
from pathlib import Path
import subprocess
import sys
import tempfile

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from smoke_test_ui import wait_for_health

ROOT = Path(__file__).resolve().parents[1]
TRACE = """
(() => {
window.startupFrames = [];
function sample() {
  const stage = document.getElementById('stage'), o = window.__orbix;
  if (stage && o && o.state.globe) {
    const opacity = Number(getComputedStyle(stage).opacity);
    const map = o.state.globe.globeMaterial().map;
    window.startupFrames.push({opacity, ready:stage.classList.contains('ready'),
      texture:!!(map && map.image && map.image.complete), count:o.counts.total});
    if (opacity === 1) return;
  }
  setTimeout(sample, 16);
}
setTimeout(sample, 16);
})();
"""


def main():
    with tempfile.TemporaryDirectory(prefix='orbix-startup-') as directory:
        entry = Path(directory) / 'offline.py'
        entry.write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
            "from src.ui.loading import load_constellation_cached\n"
            "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)\n"
            "app.main()\n", encoding='utf-8')
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(entry),
                                   '--server.port', '8528', '--server.headless', 'true'],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health('http://localhost:8528', 45)
            options = Options()
            for flag in ('--headless=new', '--window-size=1600,1000', '--use-gl=angle',
                         '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'):
                options.add_argument(flag)
            options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
            driver = webdriver.Chrome(options=options)
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': TRACE})
            driver.execute_cdp_cmd('Network.enable', {})
            driver.execute_cdp_cmd('Network.setCacheDisabled', {'cacheDisabled': True})
            driver.execute_cdp_cmd('Network.emulateNetworkConditions', {
                'offline': False, 'latency': 400, 'downloadThroughput': -1, 'uploadThroughput': -1})
            wait = WebDriverWait(driver, 45)
            for reduced in (False, True):
                driver.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features': [
                    {'name': 'prefers-reduced-motion', 'value': 'reduce' if reduced else 'no-preference'}]})
                driver.get('http://localhost:8528')
                frame = wait.until(lambda d: next((f for f in d.find_elements(By.TAG_NAME, 'iframe')
                    if 'orbix_globe' in (f.get_attribute('src') or '')), None))
                driver.switch_to.frame(frame)
                try:
                    wait.until(lambda d: d.execute_script(
                        'return window.startupFrames && window.startupFrames.some(f=>f.opacity===1)'))
                except Exception:
                    print(driver.execute_script("return {trace:window.startupFrames?.slice(-3),stage:document.getElementById('stage')?.outerHTML.slice(0,150),orbix:!!window.__orbix,body:document.body.innerText}"), flush=True)
                    print(driver.get_log('browser'), flush=True)
                    raise
                frames = driver.execute_script('return window.startupFrames')
                assert any(f['opacity'] == 0 for f in frames), frames
                assert all(f['texture'] and f['count'] > 10000 for f in frames if f['opacity'] > 0), frames
                fractional = any(0 < f['opacity'] < 1 for f in frames)
                assert fractional != reduced, frames
                assert driver.execute_script('return window.__orbix.state.playing')
                assert 'Pause' in driver.find_element(By.ID, 'play-button').text
                initial_time = driver.execute_script('return window.__orbix.state.tNorm')
                wait.until(lambda d: d.execute_script('return window.__orbix.state.tNorm') != initial_time)
                driver.find_element(By.ID, 'play-button').click()
                paused_time = driver.execute_script('return window.__orbix.state.tNorm')
                # Selection reruns must not replay the entrance.
                driver.execute_script('window.__orbix.select(0)')
                driver.execute_async_script('const done=arguments[0]; setTimeout(done,800)')
                assert driver.execute_script("return getComputedStyle(document.getElementById('stage')).opacity") == '1'
                assert not driver.execute_script('return window.__orbix.state.playing')
                assert driver.execute_script('return window.__orbix.state.tNorm') == paused_time
                print(f'PASS: delayed cold load, reduced_motion={reduced}, {len(frames)} frames; synchronized reveal, autoplay, pause survives selection rerun')
                driver.switch_to.default_content()
            errors = [e for e in driver.get_log('browser') if e['level'] == 'SEVERE']
            assert not errors, errors
        finally:
            if driver:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    main()
