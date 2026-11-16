"""Offline browser verification of optional satellite cameras; no provider calls."""
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

def main():
    with tempfile.TemporaryDirectory(prefix='orbix-tracking-') as directory:
        entry = Path(directory) / 'offline.py'
        entry.write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
            "from src.ui.loading import load_constellation_cached\n"
            "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)\n"
            "app.main()\n", encoding='utf-8')
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(entry),
                                   '--server.port', '8532', '--server.headless', 'true'],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health('http://localhost:8532', 45)
            options = Options()
            for flag in ('--headless=new', '--window-size=1600,1000', '--use-gl=angle',
                         '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'):
                options.add_argument(flag)
            options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
            driver = webdriver.Chrome(options=options)
            driver.get('http://localhost:8532')
            wait = WebDriverWait(driver, 60)
            frame = wait.until(lambda d: next((f for f in d.find_elements(By.TAG_NAME, 'iframe')
                if 'orbix_globe' in (f.get_attribute('src') or '')), None))
            driver.switch_to.frame(frame)
            wait.until(lambda d: d.execute_script("return window.__orbix && document.getElementById('stage').classList.contains('ready')"))
            def js(code): return driver.execute_script('const o=window.__orbix,c=o.state.globe.camera(); '+code)
            def pause(seconds): driver.execute_async_script('setTimeout(arguments[0],'+str(int(seconds*1000))+')')
            def mode(value): wait.until(lambda d: js('return o.tracking.mode') == value)
            assert js("return o.tracking.mode==='earth' && !o.tracking.start('follow')")
            js('o.setPlaying(false);o.setTime(.2);o.select(o.metadata.findIndex(m=>m.id===25544))')
            wait.until(lambda d: js('return o.state.selectedId===25544 && o.state.globe.pathsData().length>0'))
            pause(1)
            js('window.savedCamera=c.position.clone();window.savedUp=c.up.clone();window.savedTarget=o.state.globe.controls().target.clone()')
            baseline=[]
            for _ in range(3):
                pause(1.2); baseline.append(js('return o.perf'))
            driver.find_element(By.ID,'follow-satellite').click()
            mode('follow');pause(4)
            assert js('return c.position.length()>o.state.radius && c.position.distanceTo(window.savedCamera)>1')
            assert js('return !o.state.globe.controls().enabled')
            js('window.still=c.position.clone()');pause(1)
            assert js('return c.position.distanceTo(window.still)<.2')
            follow=[]
            for _ in range(3):
                pause(1.2);follow.append(js('return o.perf'))
            assert js('const p=c.position.clone().fromArray(o.layer.positions,o.state.selection*3).project(c);return Math.abs(p.x)<.2 && Math.abs(p.y)<.2 && p.z<1'), 'tracked point not visible'
            driver.save_screenshot(str(ROOT/'docs/screenshots/tracking-follow.png'))
            # Playback, speed and a long scrub must keep the position source intact.
            js('o.state.speed=300;o.setPlaying(true)');pause(1)
            assert js('return o.state.tNorm!==.2')
            assert js('o.setPlaying(false);const before=c.position.clone();o.setTime(.85);return c.position.distanceTo(before)<1e-9')
            pause(3)
            assert js('return c.position.length()>o.state.radius && Number.isFinite(c.quaternion.w)')
            driver.find_element(By.ID,'satellite-perspective').click();pause(4)
            mode('perspective')
            from selenium.webdriver.common.action_chains import ActionChains
            canvas=driver.find_element(By.TAG_NAME,'canvas')
            q=js('return c.quaternion.toArray()')
            ActionChains(driver).move_to_element_with_offset(canvas,0,0).click_and_hold().move_by_offset(70,30).release().perform()
            pause(.5)
            assert js('return c.quaternion.toArray()') != q
            assert js('return o.state.selectedId===25544')
            driver.save_screenshot(str(ROOT/'docs/screenshots/tracking-perspective.png'))
            # Changing target keeps tracking; hiding it safely returns to Earth.
            js('o.select(o.metadata.findIndex(m=>m.id!==25544))');pause(4)
            mode('perspective')
            group=js('return o.state.groupSpecs[o.metadata[o.state.selection].g].id')
            js('o.toggleGroup('+repr(group)+',false)');mode('earth')
            assert js('return o.state.globe.controls().enabled && c.position.distanceTo(window.savedCamera)<.001 && c.up.distanceTo(window.savedUp)<.001')
            js('o.toggleGroup('+repr(group)+',true)')
            # Cancel in acquisition, then invalid position during follow.
            driver.find_element(By.ID,'follow-satellite').click()
            driver.find_element(By.ID,'return-earth').click();mode('earth')
            js("o.tracking.start('follow');o.layer.positions[o.state.selection*3]=NaN")
            mode('earth')
            assert js("return document.getElementById('tracking-note').textContent.includes('unavailable')")
            js('o.setTime(.4)')
            driver.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
            driver.find_element(By.ID,'follow-satellite').click();pause(.2)
            assert js('return o.tracking.mode==="follow" && c.position.length()>o.state.radius')
            driver.find_element(By.ID,'return-earth').click();mode('earth')
            # High-orbit framing retains Earth; repeated switches allocate no geometry.
            js("let best=0,r=0;for(let i=0;i<o.metadata.length;i++){const v=c.position.clone().fromArray(o.layer.positions,i*3);if(v.length()>r){r=v.length();best=i;}}o.select(best)")
            pause(1)
            wait.until(lambda d:js('const paths=o.state.globe.pathsData();return paths.length>0 && paths[0].points[0].alt>1'))
            # Frustum-culled trails are uploaded lazily, only once they are visible.
            # Warm the high-orbit view before measuring repeated mode switches.
            js("o.tracking.start('follow')");pause(.5)
            js('o.tracking.returnEarth()');mode('earth')
            memory=js('return o.state.globe.renderer().info.memory.geometries')
            for camera_mode in ('follow','perspective','follow','perspective'):
                js('o.tracking.start('+repr(camera_mode)+')');pause(.2)
                assert js('const v=c.position.clone().set(0,0,0).project(c);return Math.abs(v.x)<1 && Math.abs(v.y)<1 && v.z<1'), 'Earth absent from high-orbit view'
                js('o.tracking.returnEarth()');mode('earth')
            assert js('return o.state.globe.renderer().info.memory.geometries') == memory, (memory,js('return o.state.globe.renderer().info.memory.geometries'))
            # Tracking survives an ordinary tab switch without losing its target.
            js("o.tracking.start('follow')")
            driver.switch_to.default_content()
            driver.find_element(By.XPATH,"//button[@role='tab' and normalize-space(.)='Analysis']").click()
            pause(.5)
            driver.find_element(By.XPATH,"//button[@role='tab' and normalize-space(.)='Globe']").click()
            driver.switch_to.frame(driver.find_element(By.CSS_SELECTOR,'iframe[src*="orbix_globe"]'))
            mode('follow')
            # An explicit Analysis action still restores Earth and focuses its target.
            js("o.tracking.start('follow')")
            driver.switch_to.default_content()
            driver.find_element(By.XPATH,"//button[@role='tab' and normalize-space(.)='Analysis']").click()
            button=wait.until(lambda d:d.find_element(By.CSS_SELECTOR,'.st-key-analysis_farthest_view button'))
            driver.execute_script('arguments[0].click()',button)
            pause(2)
            driver.find_element(By.XPATH,"//button[@role='tab' and normalize-space(.)='Globe']").click()
            frame=driver.find_element(By.CSS_SELECTOR,'iframe[src*="orbix_globe"]');driver.switch_to.frame(frame)
            mode('earth')
            driver.set_window_size(1200,900);pause(.5)
            assert js('return Number.isFinite(c.aspect) && o.state.globe.controls().enabled')
            errors=[e for e in driver.get_log('browser') if e['level']=='SEVERE']
            assert not errors,errors
            import json
            result={'satellites':js('return o.counts.total'),'earth':baseline,'follow':follow,'console_errors':len(errors),'renderer':'headless Chrome SwiftShader; paused matched satellite, three 1.2s samples'}
            (ROOT/'docs/tracking-performance.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(result))
            print('PASS: optional modes, stable pause, playback/scrub, look-around, target change, hidden/invalid recovery, cancel/restore, reduced motion, Analysis navigation, resize')
        finally:
            if driver: driver.quit()
            server.terminate();server.wait(timeout=10)

if __name__ == '__main__':
    main()
