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
                                   '--server.port', '8533', '--server.headless', 'true'],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            wait_for_health('http://localhost:8533', 45)
            options = Options()
            for flag in ('--headless=new', '--window-size=1600,1000', '--use-gl=angle',
                         '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'):
                options.add_argument(flag)
            options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
            driver = webdriver.Chrome(options=options)
            driver.get('http://localhost:8533')
            wait = WebDriverWait(driver, 60)
            frame = wait.until(lambda d: next((f for f in d.find_elements(By.TAG_NAME, 'iframe')
                if 'orbix_globe' in (f.get_attribute('src') or '')), None))
            driver.switch_to.frame(frame)
            wait.until(lambda d: d.execute_script("return window.__orbix && document.getElementById('stage').classList.contains('ready')"))

            import argparse,json,time
            parser=argparse.ArgumentParser();parser.add_argument('--stage',required=True);args=parser.parse_args()
            def js(code): return driver.execute_script('const o=window.__orbix,g=o.state.globe; '+code)
            def pause(n): time.sleep(n)
            js('o.setPlaying(false);o.setTime(.25)')
            wait.until(lambda d:js('return g.scene().getObjectByName("orbix-sun").userData.subsolar!=null'))
            sun=js('return g.scene().getObjectByName("orbix-sun").userData.subsolar')
            original=js('return Array.from(o.layer.positions.slice(0,30))')
            result={'stage':args.stage,'satellites':js('return o.counts.total'),'renderer':'Chrome SwiftShader software rendering','samples':{}}
            if args.stage=='final':
                wait.until(lambda d:js("return o.earthEffects.assets.night==='ready' && o.earthEffects.assets.water==='ready'"))
                js('o.select(o.metadata.findIndex(m=>m.id===25544))');pause(1)
            qualities=('cinematic','basic') if args.stage=='final' else ('cinematic',)
            for quality in qualities:
                if args.stage=='final':
                    from selenium.webdriver.support.ui import Select
                    Select(driver.find_element(By.ID,'lighting-quality')).select_by_value(quality)
                    assert js('return o.earthEffects.cinematic') == (quality=='cinematic')
                for view in (('day','night','terminator') if args.stage=='sunlight' else ('day','night','terminator','follow','perspective')):
                    if view in ('day','night','terminator'):
                        offset={'day':0,'night':180,'terminator':90}[view]
                        js('g.pointOfView({lat:'+str(-sun['lat'] if view=='night' else sun['lat'])+',lng:'+str(sun['lng']+offset)+',altitude:2.5},0)')
                    else:
                        if view=='follow':
                            js('o.select(o.metadata.findIndex(m=>m.id===25544))');pause(1)
                        js('o.tracking.start('+repr(view)+')')
                    pause(4 if view in ('follow','perspective') else 1)
                    measures=[]
                    for _ in range(2):
                        measures.append(driver.execute_async_script('const done=arguments[0],start=performance.now();let n=0;function sample(t){n++;if(t-start<1500)requestAnimationFrame(sample);else done({fps:n*1000/(t-start),calls:window.__orbix.state.globe.renderer().info.render.calls});}requestAnimationFrame(sample);'))
                    result['samples'][quality+'-'+view]=measures
                    driver.save_screenshot(str(ROOT/('docs/screenshots/lighting-'+args.stage+'-'+quality+'-'+view+'.png')))
                    assert js('return Array.from(o.layer.positions.slice(0,30))')==original
                js('o.tracking.returnEarth(true)')
            if args.stage!='baseline':
                assert js('return g.lights()[0].position.clone().normalize().dot(g.camera().position.clone().copy(g.getCoords('+str(sun['lat'])+','+str(sun['lng'])+')).normalize())>.9999')
            driver.set_window_size(1366,900);pause(1)
            driver.save_screenshot(str(ROOT/('docs/screenshots/lighting-'+args.stage+'-laptop.png')))
            if args.stage=='final':
                from io import BytesIO
                from PIL import Image,ImageStat
                Select(driver.find_element(By.ID,'lighting-quality')).select_by_value('cinematic')
                js('o.layer.points.visible=false;window.savedPaths=g.pathsData();g.pathsData([])')
                levels={}
                for name,lat,lon in [('day',sun['lat'],sun['lng']),('night',-sun['lat'],sun['lng']+180)]:
                    js('g.pointOfView({lat:'+str(lat)+',lng:'+str(lon)+',altitude:2.5},0)');pause(.5)
                    im=Image.open(BytesIO(driver.find_element(By.TAG_NAME,'canvas').screenshot_as_png)).convert('L')
                    w,h=im.size;levels[name]=ImageStat.Stat(im.crop((w//2-60,h//2-60,w//2+60,h//2+60))).mean[0]
                assert levels['day']>levels['night']*2,levels
                result['surface_luminance']=levels
                js('o.layer.points.visible=true;g.pathsData(window.savedPaths);o.setTime(0)')
                direction=js('return g.scene().getObjectByName("orbix-sun").material.uniforms.direction.value.toArray()')
                js('o.setTime(1)')
                assert js('return g.scene().getObjectByName("orbix-sun").material.uniforms.direction.value.toArray()')!=direction
                assert js('return o.earthEffects.atmosphere.material.uniforms.sunDirection.value===g.scene().getObjectByName("orbix-sun").material.uniforms.direction.value')
                js('o.setTime(.25);o.tracking.start("follow")');pause(4)
                result['laptop_follow_fps']=js('return o.perf.fps')
                driver.save_screenshot(str(ROOT/'docs/screenshots/lighting-final-laptop-follow.png'))
                js('o.tracking.returnEarth(true)')
                # Block only optional textures: base Earth must still initialise.
                driver.execute_cdp_cmd('Network.enable',{})
                driver.execute_cdp_cmd('Network.setBlockedURLs',{'urls':['*earth-night-2016.jpg','*earth-water-mask.png']})
                driver.execute_cdp_cmd('Network.setCacheDisabled',{'cacheDisabled':True})
                driver.switch_to.default_content();driver.refresh()
                frame=wait.until(lambda d:next((f for f in d.find_elements(By.TAG_NAME,'iframe') if 'orbix_globe' in (f.get_attribute('src') or '')),None))
                driver.switch_to.frame(frame)
                wait.until(lambda d:d.execute_script("return window.__orbix && window.__orbix.earthEffects && window.__orbix.earthEffects.assets.night==='unavailable' && window.__orbix.earthEffects.assets.water==='unavailable'"))
                wait.until(lambda d:js("return document.getElementById('stage').classList.contains('ready')"))
                assert js('return o.counts.total>10000')
                result['optional_texture_failure']='passed'
            errors=[e for e in driver.get_log('browser') if e['level']=='SEVERE' and 'ERR_BLOCKED_BY_CLIENT' not in e['message']]
            assert not errors,errors
            result['console_errors']=len(errors)
            (ROOT/('docs/lighting-'+args.stage+'-performance.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(result),flush=True)
        finally:
            if driver: driver.quit()
            server.terminate();server.wait(timeout=10)

if __name__=='__main__': main()
