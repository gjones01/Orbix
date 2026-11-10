#!/usr/bin/env python
"""Browser verification for the Orbix constellation globe (optional dev tool).

Python tests can prove that positions are right; they cannot prove that WebGL drew
them, that clicking selects the object under the cursor, that the filter changes what
is drawn, or that playback advances the simulation. This script starts the app,
drives headless Chrome (software WebGL, so it works without a GPU) and asserts all of
that - plus it reports frame rate and per-frame update cost.

Requirements (not needed to run the app itself)::

    pip install selenium     # Selenium Manager fetches the browser driver

Usage
-----
    python scripts/smoke_test_ui.py
    python scripts/smoke_test_ui.py --screenshot docs/screenshots/step-e-constellation.png
    python scripts/smoke_test_ui.py --groups starlink --port 8520

Exit code 0 means every browser assertion passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Browser test for the globe.")
    parser.add_argument("--port", type=int, default=8512)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--settle", type=float, default=14.0,
                        help="seconds to let the scene settle before assertions")
    parser.add_argument("--screenshot", type=str, default=None)
    parser.add_argument(
        "--analysis-screenshot", type=str, default=None,
        help="capture the Analysis tab too (globe screenshot is --screenshot)",
    )
    parser.add_argument("--keep-server", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Use saved snapshots without API requests")
    parser.add_argument(
        "--starlink",
        action="store_true",
        help="enable the Starlink group through the sidebar first (large-constellation "
             "benchmark, ~12k satellites)",
    )
    return parser


def wait_for_health(url: str, timeout: float) -> None:
    import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{url}/_stcore/health", timeout=2).status_code == 200:
                return
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(1)
    raise RuntimeError(f"Streamlit did not become healthy at {url} within {timeout}s")


PROBE = """
const o = window.__orbix;
if (!o) return { ready: false };
const counts = o.counts;
const state = o.state;
return {
  ready: true,
  total: counts.total,
  drawn: counts.drawn,
  keyframes: counts.keyframes,
  fps: o.perf.fps,
  updateMs: o.perf.updateMs,
  groups: state.groupSpecs.map(g => ({ id: g.id, count: g.count, ok: g.ok })),
  enabled: Array.from(state.enabledGroups),
  selection: state.selection,
  selectedId: state.selectedId,
  playing: state.playing,
  simTime: o.simTime ? o.simTime.toISOString() : null,
  drawnCalls: o.state.globe ? o.state.globe.renderer().info.render.calls : null,
  vertex0: o.layer ? Array.from(o.layer.positions.slice(0, 3)) : null,
  paths: o.state.globe ? o.state.globe.pathsData().length : 0,
  pathPoints: (o.state.globe && o.state.globe.pathsData()[0])
      ? o.state.globe.pathsData()[0].points.length : 0,
  tNorm: o.state.tNorm,
  clock: (document.getElementById('sim-clock') || {}).textContent,
  rangeLabel: (document.getElementById('window-range') || {}).textContent,
  resourceCount: performance.getEntriesByType('resource').length,
  selectedName: (document.getElementById('selected-name') || {}).textContent,
  statusHidden: (document.getElementById('status') || {}).classList
      ? document.getElementById('status').classList.contains('hidden') : null,
};
"""


def _snapshot_state(directory: Path) -> dict[str, int]:
    """name -> size for the committed CelesTrak snapshots."""
    if not directory.exists():
        return {}
    return {p.name: p.stat().st_size for p in sorted(directory.glob("*.json"))}


def run_browser_checks(url: str, timeout: float, settle: float, headful: bool,
                       screenshot: str | None, starlink: bool = False,
                       analysis_screenshot: str | None = None
                       ) -> tuple[dict, list[str]]:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1600,1000")
    options.add_argument("--use-gl=angle")
    options.add_argument("--use-angle=swiftshader")
    options.add_argument("--enable-unsafe-swiftshader")
    options.add_argument("--no-sandbox")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    report: dict = {}
    failures: list[str] = []
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        wait = WebDriverWait(driver, timeout)
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid='stApp']"))

        def component_frame(d):
            for frame in d.find_elements(By.TAG_NAME, "iframe"):
                if "orbix_globe" in (frame.get_attribute("src") or ""):
                    return frame
            return False

        frame = wait.until(component_frame)

        if starlink:
            # Enable Starlink through the real sidebar control, so the large case also
            # exercises the Streamlit rerun + payload resend path.
            driver.switch_to.default_content()
            labels = driver.find_elements(
                By.XPATH, "//label[contains(., 'Starlink')]")
            if not labels:
                failures.append("could not find the Starlink control")
            else:
                # Streamlit styles the real input, so click through JavaScript rather
                # than fighting Selenium's interactability check.
                driver.execute_script("arguments[0].click();", labels[0])
                time.sleep(1.5)
            frame = wait.until(component_frame)

        driver.switch_to.frame(frame)
        wait.until(lambda d: d.find_elements(By.TAG_NAME, "canvas"))

        deadline = time.time() + timeout
        while time.time() < deadline:
            probe = driver.execute_script(PROBE)
            if probe and probe.get("ready") and probe.get("total", 0) > 0:
                break
            time.sleep(0.5)
        else:
            failures.append("scene never became ready")
            return {}, failures

        time.sleep(settle)
        initial = driver.execute_script(PROBE)
        report["initial"] = initial

        if not initial["total"]:
            failures.append("no satellites in the catalogue")
        if initial["drawn"] <= 0:
            failures.append("no satellites drawn")
        if initial["statusHidden"] is not True:
            failures.append("loading overlay never cleared")
        if initial["drawnCalls"] and initial["drawnCalls"] > 6:
            failures.append(f"too many draw calls ({initial['drawnCalls']})")

        report, failures = _interaction_checks(driver, initial, report, failures,
                                               screenshot)
        driver.switch_to.default_content()
        _analysis_tab_checks(driver, report, failures, analysis_screenshot, settle)
        return report, failures
    finally:
        driver.quit()


def _analysis_tab_checks(driver, report: dict, failures: list[str],
                         screenshot: str | None, settle: float) -> None:
    """Open the Analysis tab and assert that its sections and charts really render."""
    from selenium.webdriver.common.by import By

    tabs = driver.find_elements(By.CSS_SELECTOR, "[data-testid='stTabs'] button[role='tab']")
    labels = [tab.text.strip() for tab in tabs]
    report["tabs"] = labels
    target = next((tab for tab in tabs if tab.text.strip().lower() == "analysis"), None)
    if target is None:
        failures.append(f"no Analysis tab found (tabs: {labels})")
        return
    target.click()
    time.sleep(max(2.0, settle * 0.35))

    body = driver.find_element(By.TAG_NAME, "body").text
    sections = {
        "overview": "A closer look at orbit",
        "groups": "Explore satellite groups",
        "altitudes": "How high do satellites orbit?",
        "discoveries": "Interesting discoveries",
        "analyst": "Ask Orbix",
    }
    found = {key: header in body for key, header in sections.items()}
    report["analysisSections"] = found
    for technical in ("Data quality & methodology", "One CelesTrak request per group", "AI configuration & data use"):
        if technical in body:
            failures.append(f"technical reference leaked into Analysis: {technical}")
    for key, present in found.items():
        if not present:
            failures.append(f"analysis section missing: {key}")

    charts = driver.find_elements(By.CSS_SELECTOR, ".js-plotly-plot")
    report["analysisCharts"] = len(charts)
    if len(charts) < 2:
        failures.append(f"expected at least 2 Plotly charts, found {len(charts)}")

    # Tables are Streamlit dataframes; at least the regime crosstab must be present.
    if not driver.find_elements(By.CSS_SELECTOR, "[data-testid='stDataFrame']"):
        failures.append("no data tables rendered in the Analysis tab")

    # Exercise a real dashboard action through Python and back into the WebGL renderer.
    from selenium.webdriver.support.ui import WebDriverWait
    wait = WebDriverWait(driver, 30)
    try:
        highlight_button = wait.until(lambda d: d.find_element(By.XPATH, "//button[normalize-space(.)='Highlight group']"))
    except Exception:
        print(driver.find_element(By.TAG_NAME, "body").text)
        raise
    driver.execute_script("arguments[0].click();", highlight_button)
    def highlighted(d):
        d.switch_to.default_content()
        frame = next(f for f in d.find_elements(By.TAG_NAME, "iframe")
                     if "orbix_globe" in (f.get_attribute("src") or ""))
        d.switch_to.frame(frame)
        count = d.execute_script("return window.__orbix ? window.__orbix.highlightCount : 0;")
        d.switch_to.default_content()
        return count
    count = wait.until(highlighted)
    report["analysisGroupHighlight"] = count
    clear = wait.until(lambda d: d.find_element(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']"))
    driver.execute_script("arguments[0].click();", clear)
    wait.until(lambda d: highlighted(d) == 0)
    wait.until(lambda d: not d.find_elements(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']"))

    # Analysis must use the globe's actual selection (panel + trail), not just
    # the independent multi-object highlight channel. Exercise the retained old
    # component event, then a new click on that same object at the same sim time.
    def globe_action(script, *args):
        driver.switch_to.default_content()
        frame = next(f for f in driver.find_elements(By.TAG_NAME, "iframe")
                     if "orbix_globe" in (f.get_attribute("src") or ""))
        driver.switch_to.frame(frame)
        result = driver.execute_script(script, *args)
        driver.switch_to.default_content()
        return result

    baseline = globe_action("""
        const o=window.__orbix; o.setTime(.2); o.setPlaying(false);
        o.select(o.metadata.findIndex(m=>m.id===o.state.selectedId));
        return {id:o.state.selectedId, scene:o.state.sceneId, camera:o.state.globe.pointOfView()};
    """)
    time.sleep(.7)
    farthest = driver.find_element(By.XPATH, "//button[normalize-space(.)='Explore satellite on Globe']")
    driver.execute_script("arguments[0].click();", farthest)
    def analysis_selection(d):
        import re
        notices = d.find_elements(By.CSS_SELECTOR, "[data-testid='stAlert']")
        for notice in notices:
            found = re.search(r"NORAD (\d+)", notice.text)
            if found and "is selected" in notice.text:
                return int(found.group(1))
        return False
    target_id = wait.until(analysis_selection)
    wait.until(lambda d: globe_action("return window.__orbix.state.selectedId;") == target_id)
    selected = globe_action("""
        const o=window.__orbix;
        return {id:o.state.selectedId, scene:o.state.sceneId, time:o.state.tNorm,
                camera:o.state.globe.pointOfView(), panel:document.getElementById('selected-name').textContent,
                path:o.state.globe.pathsData()[0].points.length};
    """)
    if selected["scene"] != baseline["scene"] or abs(selected["time"] - .2) > .001:
        failures.append("Analysis selection reset the globe scene or playback time")
    # Explicit Analysis selections now focus their target once; keeping the old
    # camera could leave a GEO satellite completely outside the visible viewport.
    if not globe_action("return !document.getElementById('selection-marker').hidden;"):
        failures.append("Analysis selection did not reveal the labelled marker")
    if selected["path"] < 100 or not selected["panel"]:
        failures.append("Analysis selection did not open the globe panel and full trail")
    globe_action("window.__orbix.select(window.__orbix.metadata.findIndex(m=>m.id===arguments[0]));", baseline["id"])
    # The Python panel (outside the iframe) must agree, proving the click crossed
    # the bridge instead of just changing local JavaScript state.
    old_name = globe_action("return window.__orbix.metadata.find(m=>m.id===arguments[0]).name;", baseline["id"])
    wait.until(lambda d: any(old_name in e.get_attribute("textContent")
                            for e in d.find_elements(By.CSS_SELECTOR, ".ox-card .name")))
    globe_action("window.__orbix.setPlaying(true);")
    time.sleep(.6)
    after = globe_action("return {time:window.__orbix.state.tNorm, playing:window.__orbix.state.playing};")
    globe_action("window.__orbix.setPlaying(false);")
    if not after["playing"] or after["time"] <= .2:
        failures.append("playback did not continue after Analysis selection")
    report["analysisSharedSelection"] = {"selected_id": target_id, "panel": selected["panel"],
        "trail_points": selected["path"], "returned_to_globe_id": baseline["id"],
        "playback_continued": after["playing"] and after["time"] > .2}

    investigate = driver.find_element(By.XPATH, "//button[normalize-space(.)='Investigate unusual satellites']")
    driver.execute_script("arguments[0].click();", investigate)
    wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".st-key-analysis_unusual_view button").is_displayed())
    import re
    choice_text = driver.find_element(By.CSS_SELECTOR, ".st-key-analysis_unusual_satellite").text
    unusual_id = int(re.search(r"NORAD (\d+)", choice_text).group(1))
    unusual = driver.find_element(By.CSS_SELECTOR, ".st-key-analysis_unusual_view button")
    driver.execute_script("arguments[0].click();", unusual)
    wait.until(lambda d: globe_action("return window.__orbix.state.selectedId;") == unusual_id)
    report["analysisUnusualSelection"] = unusual_id
    if screenshot:
        for width, height in [(1600, 1000), (1366, 900)]:
            driver.set_window_size(width, height)
            explorer = driver.find_element(By.XPATH, "//summary[contains(., 'Explore unusual satellites')]")
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", explorer)
            time.sleep(.4)
            driver.save_screenshot(str(Path(screenshot).with_name(f"analysis-unusual-{width}.png")))
    # Leave the snapshot of the overview clean after verifying the explorer.
    globe_action("window.__orbix.select(window.__orbix.metadata.findIndex(m=>m.id===arguments[0]));", baseline["id"])
    wait.until(lambda d: any(old_name in e.get_attribute("textContent")
                            for e in d.find_elements(By.CSS_SELECTOR, ".ox-card .name")))
    clear = driver.find_element(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']")
    driver.execute_script("arguments[0].click();", clear)
    wait.until(lambda d: highlighted(d) == 0)
    wait.until(lambda d: not d.find_elements(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']"))

    # Disclosed diagnostics must be reachable, including the original scatter selection.
    guide_tab = driver.find_element(By.XPATH, "//button[@role='tab' and normalize-space(.)='User guide']")
    driver.execute_script("arguments[0].click();", guide_tab)
    wait.until(lambda d: "Data quality & methodology" in d.find_element(By.TAG_NAME, "body").text)
    expected = {
        "Data freshness, missing values & source details": "Freshness and missing data",
        "Descriptive statistics & distributions": "All descriptive statistics",
        "Outlier diagnostics & thresholds": "Outlier diagnostics",
        "Correlation analysis": "Correlations: pooled vs within constellation",
        "Advanced orbit map & linked selection": "Orbital regimes",
        "Satellite data & column methodology": "semi_major_axis_km",
    }
    report["analysisDiagnostics"] = {}
    for label, content in expected.items():
        element = driver.find_element(By.XPATH, f"//summary[contains(., '{label}')]")
        driver.execute_script("arguments[0].click();", element)
        # Streamlit tables use canvas; their presence is checked separately above.
        if label == "Satellite data & column methodology":
            ok = bool(driver.find_elements(By.CSS_SELECTOR, "details[open] [data-testid='stDataFrame']"))
        else:
            try:
                wait.until(lambda d: content in d.find_element(By.TAG_NAME, "body").text)
                ok = True
            except Exception:
                ok = False
        report["analysisDiagnostics"][label] = ok
        if not ok:
            failures.append(f"cannot access diagnostic: {label}")
        if label == "Advanced orbit map & linked selection":
            # Send the same Plotly event produced by box/lasso selection through
            # Streamlit's real event handler, then inspect the globe result.
            selected_id = driver.execute_script("""
                const plot = [...document.querySelectorAll('.js-plotly-plot')].find(
                    p => p.layout && p.layout.xaxis &&
                         (p.layout.xaxis.title.text || '').startsWith('Inclination'));
                const t = plot.data[0];
                plot.emit('plotly_selected', {points: [{curveNumber: 0, pointNumber: 0,
                    pointIndex: 0, data: t, x: t.x[0], y: t.y[0], customdata: t.customdata[0]}]});
                return Number(t.customdata[0][0]);
            """)
            wait.until(lambda d: highlighted(d) == 1)
            time.sleep(.5)
            report["analysisScatterSelection"] = {"norad_id": selected_id, "highlighted": highlighted(driver)}
            clear = driver.find_element(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']")
            driver.execute_script("arguments[0].click();", clear)
            wait.until(lambda d: highlighted(d) == 0)
            wait.until(lambda d: not d.find_elements(By.XPATH, "//button[normalize-space(.)='Clear globe highlights']"))
            element = driver.find_element(By.XPATH, f"//summary[contains(., '{label}')]")
        driver.execute_script("arguments[0].click();", element)

    exceptions = driver.find_elements(By.CSS_SELECTOR, "[data-testid='stException']")
    if exceptions:
        failures.append("Streamlit exception in Analysis: " + exceptions[0].text)
    severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
    if severe:
        failures.append(f"{len(severe)} severe console errors during Analysis interactions")

    driver.execute_script("arguments[0].click();", driver.find_element(By.XPATH, "//button[@role='tab' and normalize-space(.)='Analysis']"))
    if screenshot:
        Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
        # The analysis page is long: a taller viewport captures more of it in one image.
        driver.set_window_size(1600, 2400)
        time.sleep(1.5)
        driver.save_screenshot(str(screenshot))
        # Second capture of the lower half (outliers and correlations). Streamlit scrolls
        # an inner container, not the window.
        driver.execute_script(
            "var main = document.querySelector('[data-testid=\"stMain\"]')"
            " || document.scrollingElement; main.scrollTop = main.scrollHeight;"
        )
        time.sleep(1.5)
        lower = Path(screenshot).with_name(
            Path(screenshot).stem + "-lower" + Path(screenshot).suffix
        )
        driver.save_screenshot(str(lower))
        for width, height in [(1600, 1000), (1366, 900)]:
            driver.set_window_size(width, height)
            driver.execute_script("document.querySelector('[data-testid=stMain]').scrollTop = 0;")
            time.sleep(.6)
            driver.save_screenshot(str(Path(screenshot).with_name(f"analysis-{width}.png")))
            overflow = driver.execute_script(
                "const el=document.querySelector('[data-testid=stMain]'); return el.scrollWidth > el.clientWidth+2;")
            if overflow:
                failures.append(f"Analysis overflows horizontally at {width}px")
        driver.set_window_size(1600, 1000)


def _interaction_checks(driver, initial: dict, report: dict, failures: list[str],
                        screenshot: str | None) -> tuple[dict, list[str]]:
    """Filtering, selection, playback and performance assertions."""
    # --- filtering ------------------------------------------------------- #
    group_ids = [g["id"] for g in initial["groups"] if g["ok"]]
    if len(group_ids) >= 2:
        driver.execute_script("window.__orbix.toggleGroup(arguments[0], false);", group_ids[0])
        time.sleep(0.4)
        filtered = driver.execute_script(PROBE)
        report["filtered"] = {"drawn": filtered["drawn"], "group": group_ids[0]}
        if filtered["drawn"] >= initial["drawn"]:
            failures.append(
                f"filtering {group_ids[0]} did not reduce the drawn count "
                f"({initial['drawn']} -> {filtered['drawn']})")
        driver.execute_script("window.__orbix.toggleGroup(arguments[0], true);", group_ids[0])
        time.sleep(0.4)
        restored = driver.execute_script(PROBE)
        if restored["drawn"] != initial["drawn"]:
            failures.append("restoring the filter did not restore the drawn count")

    # --- selection -------------------------------------------------------- #
    driver.execute_script("window.__orbix.select(5);")
    # The trail arrives with the payload on the Streamlit rerun that the click
    # triggers, so poll for it rather than assuming it is instantaneous.
    deadline = time.time() + 20
    selected = driver.execute_script(PROBE)
    while time.time() < deadline and not selected.get("paths"):
        time.sleep(0.5)
        selected = driver.execute_script(PROBE)
    report["selected"] = {
        "index": selected["selection"],
        "norad_id": selected["selectedId"],
        "panel": selected["selectedName"],
        "paths": selected["paths"],
        "path_points": selected["pathPoints"],
    }
    if selected["selection"] != 5:
        failures.append("selecting index 5 did not update the layer selection")
    if selected["selectedName"] in (None, "", "click a satellite"):
        failures.append("the selection panel was not updated")
    # The selected satellite must get its full orbital-period trail (~93 min -> 121 pts).
    if selected["paths"] < 1:
        failures.append("no orbit trail was drawn for the selected satellite")
    elif selected["pathPoints"] < 100:
        failures.append(
            f"selected trail looks truncated ({selected['pathPoints']} points)")

    # --- playback --------------------------------------------------------- #
    snapshots_before = _snapshot_state(REPO_ROOT / "data" / "snapshots")
    before = selected["vertex0"]
    sim_before = selected["simTime"]
    resources_before = selected["resourceCount"]

    driver.execute_script("window.__orbix.setPlaying(true);")
    time.sleep(2.5)
    playing = driver.execute_script(PROBE)
    driver.execute_script("window.__orbix.setPlaying(false);")
    report["playing"] = {
        "sim_before": sim_before,
        "sim_after": playing["simTime"],
        "moved": playing["vertex0"] != before,
    }
    if not playing["playing"]:
        failures.append("playback did not start")
    if playing["simTime"] == sim_before:
        failures.append("the simulation clock did not advance")
    if playing["vertex0"] == before:
        failures.append("satellite positions did not move during playback")

    # Playback must not reach the network or trigger a Streamlit rerun: no new
    # resources loaded in the page, and no new CelesTrak snapshot written.
    if playing["resourceCount"] != resources_before:
        failures.append(
            f"playback loaded {playing['resourceCount'] - resources_before} new "
            "resource(s); it should be purely client-side")
    snapshots_after = _snapshot_state(REPO_ROOT / "data" / "snapshots")
    new_snapshots = sorted(set(snapshots_after) - set(snapshots_before))
    report["api_quiet"] = {
        "new_resources": playing["resourceCount"] - resources_before,
        "new_snapshots": new_snapshots,
    }
    if new_snapshots:
        failures.append(f"playback wrote new snapshots: {new_snapshots}")

    # --- reset ------------------------------------------------------------ #
    driver.execute_script("window.__orbix.setTime(0.6);")
    time.sleep(0.3)
    advanced = driver.execute_script(PROBE)
    driver.execute_script("document.getElementById('reset-button').click();")
    time.sleep(0.4)
    reset = driver.execute_script(PROBE)
    report["reset"] = {
        "tNorm_before": advanced["tNorm"],
        "tNorm_after": reset["tNorm"],
        "back_to_start": reset["vertex0"] == before,
        "clock": reset["clock"],
    }
    if advanced["tNorm"] <= 0.0:
        failures.append("scrubbing the slider did not move the simulation")
    if reset["tNorm"] != 0.0:
        failures.append(f"reset left tNorm at {reset['tNorm']}")
    if reset["vertex0"] != before:
        failures.append("reset did not restore the initial positions")

    # --- window labelling (forward-only) ---------------------------------- #
    report["window"] = {
        "range_label": reset["rangeLabel"],
        "clock_at_start": reset["clock"],
        "sim_at_start": reset["simTime"],
    }
    if not reset["rangeLabel"]:
        failures.append("the playback range label is empty")
    if reset["rangeLabel"] and "+" not in reset["rangeLabel"]:
        failures.append(f"range label does not show a forward range: {reset['rangeLabel']}")


    # --- performance ------------------------------------------------------ #
    time.sleep(0.6)
    perf = driver.execute_script(PROBE)
    report["perf"] = {
        "fps": perf["fps"],
        "updateMs": perf["updateMs"],
        "drawn": perf["drawn"],
        "drawCalls": perf["drawnCalls"],
    }

    # --- analysis highlight bridge ---------------------------------------- #
    # The chart -> globe path has two halves: Python must ship highlight_ids in the
    # payload (covered by a unit test) and the renderer must emphasise those points.
    # This exercises the second half through the same function the payload uses.
    sample_ids = driver.execute_script(
        "return window.__orbix.metadata.slice(0, 3).map(function (m) { return m.id; });"
    )
    driver.execute_script("window.__orbix.setHighlights(arguments[0]);", sample_ids)
    time.sleep(0.3)
    highlighted = driver.execute_script("return window.__orbix.highlightCount;")
    report["highlight"] = {"requested": len(sample_ids), "applied": highlighted}
    if highlighted != len(sample_ids):
        failures.append(
            f"highlight bridge applied {highlighted} of {len(sample_ids)} ids")
    driver.execute_script("window.__orbix.setHighlights([]);")
    time.sleep(0.2)
    if driver.execute_script("return window.__orbix.highlightCount;") != 0:
        failures.append("highlight could not be cleared")

    if screenshot:
        driver.switch_to.default_content()
        Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot))

    severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
    report["severeErrors"] = [e["message"][:160] for e in severe[:5]]
    if severe:
        failures.append(f"{len(severe)} severe console error(s)")
    return report, failures


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = f"http://localhost:{args.port}"
    temporary = tempfile.TemporaryDirectory(prefix="orbix-smoke-")
    entry = REPO_ROOT / "app.py"
    if args.offline:
        entry = Path(temporary.name) / "offline_app.py"
        entry.write_text(
            f"import sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "import app\nfrom src.ui.loading import load_constellation_cached\n"
            "app.load_constellation_cached = lambda groups, window, offline, refresh, when: "
            "load_constellation_cached(groups, window, True, False, when)\napp.main()\n",
            encoding="utf-8",
        )
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(entry),
         "--server.port", str(args.port), "--server.headless", "true"],
        cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_health(url, args.timeout)
        print(f"server healthy on {url}")
        report, failures = run_browser_checks(
            url, args.timeout, args.settle, args.headful, args.screenshot,
            starlink=args.starlink, analysis_screenshot=args.analysis_screenshot)
        initial = report.get("initial", {})
        summary = {
            "satellites_loaded": initial.get("total"),
            "satellites_drawn": initial.get("drawn"),
            "keyframes": initial.get("keyframes"),
            "groups": initial.get("groups"),
            "filtered": report.get("filtered"),
            "selected": report.get("selected"),
            "playing": report.get("playing"),
            "api_quiet": report.get("api_quiet"),
            "reset": report.get("reset"),
            "window": report.get("window"),
            "highlight": report.get("highlight"),
            "tabs": report.get("tabs"),
            "analysisSections": report.get("analysisSections"),
            "analysisCharts": report.get("analysisCharts"),
            "analysisGroupHighlight": report.get("analysisGroupHighlight"),
            "analysisDiagnostics": report.get("analysisDiagnostics"),
            "analysisScatterSelection": report.get("analysisScatterSelection"),
            "analysisSharedSelection": report.get("analysisSharedSelection"),
            "analysisUnusualSelection": report.get("analysisUnusualSelection"),
            "perf": report.get("perf"),
            "severeErrors": report.get("severeErrors"),
        }
        print(json.dumps(summary, indent=2))
        if failures:
            for failure in failures:
                print(f"FAILED: {failure}", file=sys.stderr)
            return 1
        print("OK: constellation globe verified in a headless browser")
        return 0
    finally:
        if not args.keep_server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
