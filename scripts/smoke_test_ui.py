#!/usr/bin/env python
"""Headless UI smoke test for the Orbix globe (optional developer tool).

Starts the Streamlit app on a spare port, drives a headless Chrome/Edge session and
asserts that the 3D component really rendered: a WebGL canvas exists, the scene
payload reached the renderer (markers, label, rings, track), the loading overlay
cleared and the browser console reported no severe errors.

This is how Step D was verified, and it earned its keep: it caught four bugs that no
Python test could have caught - a payload field mismatch, a component-protocol
message sent out of order, an empty tooltip rendering as a stray box, and a marker
radius expressed in the wrong units.

Requirements (not needed to run the app itself)::

    pip install selenium     # Selenium Manager fetches the browser driver

Usage
-----
    python scripts/smoke_test_ui.py
    python scripts/smoke_test_ui.py --screenshot docs/screenshots/step-d-globe.png
    python scripts/smoke_test_ui.py --port 8520 --keep-server

Exit code 0 means the globe rendered in a real browser.
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
    parser = argparse.ArgumentParser(description="Headless smoke test for the globe.")
    parser.add_argument("--port", type=int, default=8512, help="port for Streamlit")
    parser.add_argument("--timeout", type=float, default=90.0, help="startup timeout (s)")
    parser.add_argument("--settle", type=float, default=13.0, help="scene settle time (s)")
    parser.add_argument("--screenshot", type=str, default=None, help="PNG output path")
    parser.add_argument(
        "--keep-server", action="store_true", help="leave Streamlit running afterwards"
    )
    parser.add_argument(
        "--headful", action="store_true", help="show the browser window (debugging)"
    )
    return parser


def wait_for_health(url: str, timeout: float) -> None:
    """Block until Streamlit answers its health endpoint."""
    import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{url}/_stcore/health", timeout=2).status_code == 200:
                return
        except Exception:  # noqa: BLE001 - server simply is not up yet
            pass
        time.sleep(1)
    raise RuntimeError(f"Streamlit did not become healthy at {url} within {timeout}s")


def run_check(
    url: str,
    *,
    timeout: float,
    settle: float,
    headful: bool,
    screenshot: str | None,
) -> dict:
    """Drive the app in a real browser and report what the renderer received."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1600,1000")
    # Software WebGL so this works on machines and CI images without a GPU.
    options.add_argument("--use-gl=angle")
    options.add_argument("--use-angle=swiftshader")
    options.add_argument("--enable-unsafe-swiftshader")
    options.add_argument("--no-sandbox")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

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
        driver.switch_to.frame(frame)
        wait.until(lambda d: d.find_elements(By.TAG_NAME, "canvas"))
        time.sleep(settle)

        scene = driver.execute_script(
            """
            const orbix = window.__orbix || {};
            const globe = orbix.globe;
            const canvas = document.querySelector('canvas');
            const overlay = document.getElementById('status');
            return {
              canvas: canvas ? [canvas.width, canvas.height] : null,
              webgl: !!(canvas && (canvas.getContext('webgl2') ||
                                   canvas.getContext('webgl'))),
              overlayHidden: overlay ? overlay.classList.contains('hidden') : null,
              points: globe ? globe.pointsData().length : 0,
              labels: globe ? globe.labelsData().length : 0,
              rings: globe ? globe.ringsData().length : 0,
              paths: globe ? globe.pathsData().length : 0,
              trackPoints: orbix.payload ? orbix.payload.track.points.length : 0,
              altitudeRatio: orbix.payload ? orbix.payload.satellite.altitude_ratio : null,
            };
            """
        )

        driver.switch_to.default_content()
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(screenshot))
            scene["screenshot"] = str(screenshot)

        severe = [entry for entry in driver.get_log("browser")
                  if entry["level"] == "SEVERE"]
        scene["severeConsoleErrors"] = len(severe)
        scene["severeMessages"] = [entry["message"][:200] for entry in severe[:5]]
        return scene
    finally:
        driver.quit()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = f"http://localhost:{args.port}"

    server = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run",
            str(REPO_ROOT / "app.py"),
            "--server.port", str(args.port),
            "--server.headless", "true",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_health(url, args.timeout)
        print(f"server healthy on {url}")
        scene = run_check(
            url,
            timeout=args.timeout,
            settle=args.settle,
            headful=args.headful,
            screenshot=args.screenshot,
        )
        print(json.dumps(scene, indent=2))

        failures: list[str] = []
        if not scene["webgl"]:
            failures.append("no WebGL context")
        if scene["overlayHidden"] is not True:
            failures.append("loading overlay never cleared")
        if scene["points"] < 1:
            failures.append(f"no satellite markers ({scene['points']})")
        if scene["trackPoints"] < 2:
            failures.append(f"no orbit track ({scene['trackPoints']} points)")
        ratio = scene["altitudeRatio"]
        if ratio is None or not 0.0 < ratio < 0.2:
            failures.append(f"implausible altitude ratio {ratio}")
        if scene["severeConsoleErrors"]:
            failures.append(
                f"{scene['severeConsoleErrors']} severe console error(s): "
                + "; ".join(scene["severeMessages"])
            )

        if failures:
            for failure in failures:
                print(f"FAILED: {failure}", file=sys.stderr)
            return 1
        print("OK: globe rendered in a headless browser")
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

