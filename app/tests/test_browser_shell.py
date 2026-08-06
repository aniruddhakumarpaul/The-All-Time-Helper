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

            for width, height in ((1280, 900), (1024, 768), (412, 915), (390, 844), (360, 800)):
                page.set_viewport_size({"width": width, "height": height})
                page.locator("#settings-modal").evaluate("element => { element.style.display = 'flex'; }")
                metrics = page.evaluate("""() => {
                    const modal = document.querySelector('#settings-modal .modal-card');
                    const modalRect = modal.getBoundingClientRect();
                    const rect = element => {
                        const value = element.getBoundingClientRect();
                        return {
                            width: value.width,
                            height: value.height,
                            top: value.top,
                            bottom: value.bottom,
                            left: value.left,
                            right: value.right,
                            visible: value.width > 0 && value.height > 0,
                        };
                    };
                    const close = document.querySelector('#close-settings-btn');
                    const signout = document.querySelector('#signout-btn');
                    const credit = document.querySelector('#settings-modal .modal-credit');
                    const controls = [
                        document.querySelector('#theme-btn-settings'),
                        document.querySelector('#response-style-setting'),
                        document.querySelector('#t-pers'),
                        document.querySelector('#t-word'),
                        document.querySelector('#t-eng'),
                    ].map(rect);
                    const inside = value => value.visible
                        && value.left >= modalRect.left - 1
                        && value.right <= modalRect.right + 1
                        && value.top >= modalRect.top - 1
                        && value.bottom <= modalRect.bottom + 1;
                    return {
                        documentWidth: document.documentElement.scrollWidth,
                        viewportWidth: window.innerWidth,
                        modalWidth: modal.clientWidth,
                        modalHeight: modal.clientHeight,
                        modalScrollWidth: modal.scrollWidth,
                        modalScrollHeight: modal.scrollHeight,
                        overflowY: getComputedStyle(modal).overflowY,
                        close: rect(close),
                        signout: rect(signout),
                        account: rect(document.querySelector('#settings-modal .settings-account')),
                        credit: rect(credit),
                        controls,
                        modalRect: { top: modalRect.top, bottom: modalRect.bottom, left: modalRect.left, right: modalRect.right },
                        closeInside: inside(rect(close)),
                        signoutInside: inside(rect(signout)),
                        controlsInside: controls.map(inside),
                    };
                }""")
                self.assertLessEqual(metrics["documentWidth"], width + 1)
                self.assertLessEqual(metrics["modalScrollWidth"], metrics["modalWidth"] + 1)
                self.assertLessEqual(metrics["modalScrollHeight"], metrics["modalHeight"] + 1)
                self.assertTrue(metrics["close"]["visible"])
                self.assertTrue(metrics["closeInside"])
                self.assertTrue(metrics["signout"]["visible"])
                self.assertTrue(metrics["signoutInside"])
                self.assertTrue(all(metrics["controlsInside"]))
                self.assertLessEqual(abs(metrics["signout"]["width"] - metrics["account"]["width"]), 2)
                self.assertLess(metrics["credit"]["bottom"], metrics["signout"]["top"])
                page.locator("#settings-modal").evaluate("element => { element.style.display = 'none'; }")

            page.set_viewport_size({"width": 390, "height": 560})
            page.locator("#settings-modal").evaluate("element => { element.style.display = 'flex'; }")
            short_initial = page.evaluate("""() => {
                const modal = document.querySelector('#settings-modal .modal-card');
                const close = document.querySelector('#close-settings-btn').getBoundingClientRect();
                return {
                    overflowY: getComputedStyle(modal).overflowY,
                    closeVisible: close.width > 0 && close.height > 0 && close.top >= 0 && close.bottom <= window.innerHeight + 1,
                    modalClientHeight: modal.clientHeight,
                    modalScrollHeight: modal.scrollHeight,
                };
            }""")
            self.assertEqual(short_initial["overflowY"], "auto")
            self.assertTrue(short_initial["closeVisible"])
            page.locator("#settings-modal .modal-card").evaluate("element => { element.scrollTop = element.scrollHeight; }")
            short_bottom = page.evaluate("""() => {
                const modal = document.querySelector('#settings-modal .modal-card');
                const signout = document.querySelector('#signout-btn').getBoundingClientRect();
                return {
                    scrollTop: modal.scrollTop,
                    maxScrollTop: Math.max(0, modal.scrollHeight - modal.clientHeight),
                    signoutVisible: signout.width > 0 && signout.height > 0,
                    signoutInside: signout.top >= 0 && signout.bottom <= window.innerHeight + 1,
                    documentWidth: document.documentElement.scrollWidth,
                    viewportWidth: window.innerWidth,
                };
            }""")
            self.assertGreaterEqual(short_bottom["scrollTop"] + 1, short_bottom["maxScrollTop"])
            self.assertTrue(short_bottom["signoutVisible"])
            self.assertTrue(short_bottom["signoutInside"])
            self.assertLessEqual(short_bottom["documentWidth"], short_bottom["viewportWidth"] + 1)
            page.locator("#settings-modal").evaluate("element => { element.style.display = 'none'; }")

            # Native image drag is exercised by the browser email workflow; this served-shell test verifies the responsive surface.
            page.locator("#prompt").fill("responsive shell")
            self.assertEqual(page.locator("#prompt").input_value(), "responsive shell")
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), page.evaluate("() => window.innerWidth") + 1)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()