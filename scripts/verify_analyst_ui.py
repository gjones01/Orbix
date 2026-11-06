"""Browser -> Streamlit -> real HTTP mock -> answer. No live LLM or paid calls.

Run from the repository root: python scripts/verify_analyst_ui.py
Requires Selenium/Chrome, as do the existing browser smoke scripts.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from smoke_test_ui import wait_for_health

ROOT = Path(__file__).resolve().parents[1]


def main():
    calls = []

    class MockProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/chat/completions"
            assert body["max_tokens"] == 1200 and "tools" not in body
            ctx = json.loads(body["messages"][-1]["content"])["current_snapshot_evidence"]
            calls.append(ctx)
            answer = {"findings": [{
                "title": "Browser verification: loaded population",
                "explanation": f"The current selection contains {ctx['total_satellites']} satellites, counted by Python.",
                "caveat": "This is a mock provider response, not a live LLM result.",
                "evidence_ids": ["population"],
            }]}
            payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}],
                                  "usage": {"total_tokens": 123}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    driver = server = None
    try:
        with tempfile.TemporaryDirectory(prefix="orbix-analyst-") as directory:
            entry = Path(directory) / "offline.py"
            entry.write_text(
                f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport app\n"
                "from src.ui.loading import load_constellation_cached\n"
                "app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)\n"
                "app.main()\n", encoding="utf-8")
            env = {**os.environ, "ORBIX_LLM_BASE_URL": f"http://127.0.0.1:{mock.server_port}/v1",
                   "ORBIX_LLM_API_KEY": "mock-only", "ORBIX_LLM_MODEL": "mock-model",
                   "ORBIX_LLM_TOKEN_PARAMETER": "max_tokens"}
            server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(entry),
                                       "--server.port", "8525", "--server.headless", "true"],
                                      cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for_health("http://localhost:8525", 45)
            options = Options()
            for flag in ("--headless=new", "--window-size=1600,1100", "--use-gl=angle",
                         "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"):
                options.add_argument(flag)
            options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
            driver = webdriver.Chrome(options=options)
            driver.get("http://localhost:8525")
            wait = WebDriverWait(driver, 45)

            def click(element):
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                element.click()

            def button(key):
                return wait.until(lambda d: d.find_element(By.CSS_SELECTOR, f".st-key-{key} button"))

            click(wait.until(lambda d: d.find_element(By.XPATH, "//button[@role='tab' and normalize-space(.)='Analysis']")))
            wait.until(lambda d: button("analyst_patterns").is_enabled())
            assert len(calls) == 0
            click(button("analyst_patterns"))
            wait.until(lambda d: "Browser verification: loaded population" in d.find_element(By.TAG_NAME, "body").text)
            assert len(calls) == 1
            assert calls[0]["total_satellites"] > 0
            assert sum(row["n"] for row in calls[0]["evidence"]["population"]) == calls[0]["total_satellites"]
            assert "altitude_flags" in calls[0]["evidence"]
            # A normal unrelated rerun must retain the answer and make no provider call.
            click(driver.find_element(By.XPATH, "//label[normalize-space(.)='mi']"))
            wait.until(lambda d: button("analyst_patterns").is_enabled())
            time.sleep(1)
            assert len(calls) == 1
            field = driver.find_element(By.CSS_SELECTOR, ".st-key-analyst_question input")
            field.send_keys("Explain NORAD 25544")
            click(driver.find_element(By.XPATH, "//button[normalize-space(.)='Ask Orbix']"))
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "[data-testid='stChatMessage']")) == 4)
            assert len(calls) == 2
            assert calls[-1]["evidence"]["satellites"][0]["norad_id"] == 25544
            # The existing globe selection still works after conversation reruns.
            click(button("analysis_farthest_view"))
            wait.until(lambda d: "is selected. Open the Globe" in d.find_element(By.TAG_NAME, "body").text)
            assert len(calls) == 2
            title = driver.find_element(By.XPATH, "//h3[normalize-space(.)='Ask Orbix']")
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", title)
            driver.save_screenshot(str(ROOT / "docs/screenshots/ask-orbix-mock.png"))
            driver.set_window_size(1366, 1000)
            driver.save_screenshot(str(ROOT / "docs/screenshots/ask-orbix-mock-1366.png"))
            click(button("analyst_clear"))
            wait.until(lambda d: not d.find_elements(By.CSS_SELECTOR, "[data-testid='stChatMessage']"))
            assert len(calls) == 2
            assert not driver.find_elements(By.CSS_SELECTOR, "[data-testid='stException']")
            severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
            assert not severe, severe
            print(json.dumps({"result": "passed", "provider": "local HTTP mock, not live LLM",
                              "requests": len(calls), "initial_satellites": calls[0]["total_satellites"],
                              "individual_norad": 25544, "console_errors": len(severe)}))
    finally:
        if driver:
            driver.quit()
        if server:
            server.terminate()
            server.wait(timeout=10)
        mock.shutdown()
        mock.server_close()


if __name__ == "__main__":
    main()
