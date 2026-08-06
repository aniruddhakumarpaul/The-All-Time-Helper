import copy
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BrowserShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AssertionError("Python Playwright is required for the served-shell browser test")

        cls.playwright = sync_playwright().start()
        cls.port = cls._free_port()
        env = copy.copy(os.environ)
        env["PORT"] = str(cls.port)
        env["HELPER_RELOAD"] = "0"
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise AssertionError(f"FastAPI shell exited during startup:\n{output[-4000:]}")
            try:
                with urllib.request.urlopen(cls.base_url + "/", timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.25)
        else:
            cls._stop_server()
            raise AssertionError("FastAPI shell did not become ready within 45 seconds")

        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as error:
            cls._stop_server()
            cls.playwright.stop()
            raise AssertionError(f"Chromium is not available: {error}") from error

    @staticmethod
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    @classmethod
    def _stop_server(cls):
        server = getattr(cls, "server", None)
        if not server or server.poll() is not None:
            return
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "browser", None):
            cls.browser.close()
        if getattr(cls, "playwright", None):
            cls.playwright.stop()
        cls._stop_server()

    def test_served_shell_is_responsive_and_contains_single_interaction_surfaces(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
        try:
            # CDN assets are not part of the served-shell contract; abort them so offline browser runs reach the local DOM.
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(self.base_url + "/", wait_until="commit", timeout=10000)
            page.wait_for_selector("#settings-modal", state="attached", timeout=10000)
            self.assertEqual(page.locator("#image-modal").count(), 1)
            self.assertEqual(page.locator(".pill-bar-container").count(), 1)

            for width, height in ((1280, 900), (1024, 768), (412, 915), (390, 844), (360, 800), (390, 560)):
                page.set_viewport_size({"width": width, "height": height})
                metrics = page.evaluate("""() => {
                    const modal = document.querySelector('#settings-modal .modal-card');
                    return {
                        documentWidth: document.documentElement.scrollWidth,
                        viewportWidth: window.innerWidth,
                        modalOverflow: modal ? getComputedStyle(modal).overflowY : '',
                        modalPosition: modal ? getComputedStyle(modal).position : '',
                    };
                }""")
                self.assertLessEqual(metrics["documentWidth"], metrics["viewportWidth"] + 1)
                page.locator("#settings-modal").evaluate("element => { element.style.display = 'flex'; }")
                bounds = page.locator("#settings-modal .modal-card").bounding_box()
                self.assertIsNotNone(bounds)
                self.assertGreater(bounds["width"], 0)
                self.assertLessEqual(bounds["x"] + bounds["width"], width + 1)
                page.locator("#settings-modal").evaluate("element => { element.style.display = 'none'; }")

            # Native image drag is exercised by the browser email workflow; this served-shell test verifies the responsive surface.
            page.locator("#prompt").fill("responsive shell")
            self.assertEqual(page.locator("#prompt").input_value(), "responsive shell")
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), page.evaluate("() => window.innerWidth") + 1)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()