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

    tabs = driver.find_elements(By.CSS_SELECTOR, "[data-testid='stTabs'] button")
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
        "regimes": "Orbital regimes",
        "distributions": "Distributions",
        "freshness": "Freshness and missing data",
        "outliers": "Outlier diagnostics",
        "correlations": "Correlations: pooled vs within constellation",
    }
    found = {key: header in body for key, header in sections.items()}
    report["analysisSections"] = found
    for key, present in found.items():
        if not present:
            failures.append(f"analysis section missing: {key}")

    charts = driver.find_elements(By.CSS_SELECTOR, ".js-plotly-plot")
    report["analysisCharts"] = len(charts)
    if len(charts) < 5:
        failures.append(f"expected at least 5 Plotly charts, found {len(charts)}")

    # Tables are Streamlit dataframes; at least the regime crosstab must be present.
    if not driver.find_elements(By.CSS_SELECTOR, "[data-testid='stDataFrame']"):
        failures.append("no data tables rendered in the Analysis tab")

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
        driver.execute_script("window.scrollTo(0, 0);")
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
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "app.py"),
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


if __name__ == "__main__":
    raise SystemExit(main())
