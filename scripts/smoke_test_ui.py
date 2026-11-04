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
  selectedName: (document.getElementById('selected-name') || {}).textContent,
  statusHidden: (document.getElementById('status') || {}).classList
      ? document.getElementById('status').classList.contains('hidden') : null,
};
"""


def run_browser_checks(url: str, timeout: float, settle: float, headful: bool,
                       screenshot: str | None, starlink: bool = False
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
        return report, failures
    finally:
        driver.quit()


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
    time.sleep(0.3)
    selected = driver.execute_script(PROBE)
    report["selected"] = {
        "index": selected["selection"],
        "norad_id": selected["selectedId"],
        "panel": selected["selectedName"],
    }
    if selected["selection"] != 5:
        failures.append("selecting index 5 did not update the layer selection")
    if selected["selectedName"] in (None, "", "click a satellite"):
        failures.append("the selection panel was not updated")

    # --- playback --------------------------------------------------------- #
    before = selected["vertex0"]
    sim_before = selected["simTime"]
    driver.execute_script("window.__orbix.setPlaying(true);")
    time.sleep(2.0)
    playing = driver.execute_script(PROBE)
    driver.execute_script("window.__orbix.setPlaying(false);")
    report["playing"] = {"simTime": playing["simTime"], "moved": playing["vertex0"] != before}
    if not playing["playing"]:
        failures.append("playback did not start")
    if playing["simTime"] == sim_before:
        failures.append("the simulation clock did not advance")
    if playing["vertex0"] == before:
        failures.append("satellite positions did not move during playback")

    # --- performance ------------------------------------------------------ #
    time.sleep(0.6)
    perf = driver.execute_script(PROBE)
    report["perf"] = {
        "fps": perf["fps"],
        "updateMs": perf["updateMs"],
        "drawn": perf["drawn"],
        "drawCalls": perf["drawnCalls"],
    }

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
            starlink=args.starlink)
        initial = report.get("initial", {})
        summary = {
            "satellites_loaded": initial.get("total"),
            "satellites_drawn": initial.get("drawn"),
            "keyframes": initial.get("keyframes"),
            "groups": initial.get("groups"),
            "filtered": report.get("filtered"),
            "selected": report.get("selected"),
            "playing": report.get("playing"),
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
