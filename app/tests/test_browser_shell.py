import copy
from datetime import datetime, timedelta, timezone
import jwt
from dotenv import load_dotenv
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
        load_dotenv(ROOT / ".env", override=True)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AssertionError("Python Playwright is required for the served-shell browser test")

        cls.playwright = sync_playwright().start()
        cls.port = cls._free_port()
        env = copy.copy(os.environ)
        env["PORT"] = str(cls.port)
        env["HELPER_RELOAD"] = "0"
        env["SECRET_KEY"] = env.get("SECRET_KEY") or "browser-test-secret"
        env["CHAT_JOB_TEST_DELAY_SECONDS"] = "2"
        env["CHAT_JOB_DB_FILE"] = str(Path("C:/tmp") / ("tah-browser-chat-jobs-" + str(cls.port) + ".db"))
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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

    def test_server_owned_job_survives_page_reload(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        email = "browser-job@example.com"
        try:
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(self.base_url + "/", wait_until="commit", timeout=10000)
            page.wait_for_selector("#prompt", state="attached", timeout=10000)
            page.evaluate("""({ email, token, chats }) => {
                localStorage.setItem('helper_token_v2', token);
                localStorage.setItem('helper_user_v2', JSON.stringify({ email, name: 'Browser Job' }));
                localStorage.setItem('helper_chats_v2_' + email, JSON.stringify(chats));
                localStorage.setItem('helper_active_chat_v2', 'refresh-chat');
            }""", {
                "email": email,
                "token": self._test_token(email),
                "chats": [{"id": "refresh-chat", "title": "Refresh test", "ms": [{"r": "u", "c": "continue"}], "updated_at": int(time.time() * 1000)}],
            })
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_selector("#prompt", state="attached", timeout=10000)
            created = page.evaluate("""async ({ token }) => {
                const response = await fetch('/chat/jobs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
                    body: JSON.stringify({ prompt: '__test_delay__', history: [], model: 'helper-auto', attachments: [], name: 'Browser Job', sys: {} })
                });
                return await response.json();
            }""", {"token": self._test_token(email)})
            self.assertTrue(created.get("success"), created)
            self.assertTrue(created.get("job_id"))
            page.evaluate("""({ email, jobId }) => {
                localStorage.setItem('helper_active_chat_jobs_v2', JSON.stringify([{
                    id: jobId, chatId: 'refresh-chat', email, model: 'Helper Auto', after: 0
                }]));
            }""", {"email": email, "jobId": created["job_id"]})
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_selector("#prompt", state="attached", timeout=10000)
            page.wait_for_function("() => document.body?.innerText.includes('server-owned delayed response')", timeout=12000)
            self.assertIn("server-owned delayed response", page.locator("body").inner_text())
        finally:
            page.close()

    def test_real_composer_send_reconnects_once_after_refresh(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        email = "browser-real-refresh@example.com"
        token = self._test_token(email)
        try:
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(self.base_url + "/", wait_until="commit", timeout=10000)
            page.wait_for_selector("#prompt", state="attached", timeout=10000)
            page.evaluate("""({ email, token }) => {
                localStorage.setItem('helper_token_v2', token);
                localStorage.setItem('helper_user_v2', JSON.stringify({ email, name: 'Browser Real' }));
                localStorage.setItem('helper_chats_v2_' + email, JSON.stringify([
                    { id: 'real-refresh-chat', title: 'Real refresh', ms: [], updated_at: Date.now() }
                ]));
                localStorage.setItem('helper_active_chat_v2', 'real-refresh-chat');
            }""", {"email": email, "token": token})
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_function("() => window.__helperAppBridgeReady === true", timeout=10000)
            page.locator("#prompt").fill("__test_delay__")
            page.locator("#main-send-btn").click()
            page.wait_for_function("""() => Object.keys(localStorage).some(key => key.startsWith('helper_active_chat_job_v3:'))""", timeout=10000)
            page.wait_for_function("""(email) => {
                const chats = JSON.parse(localStorage.getItem('helper_chats_v2_' + email) || '[]');
                return chats.some(chat => chat.ms?.some(message => message.job_id));
            }""", arg=email, timeout=10000)
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_function("() => window.__helperAppBridgeReady === true", timeout=10000)
            page.wait_for_selector("body", state="attached", timeout=10000)
            page.wait_for_function("() => document.querySelector('body')?.innerText.includes('server-owned delayed response')", timeout=12000)
            self.assertEqual(page.locator("body").inner_text().count("server-owned delayed response"), 1)
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_selector("body", state="attached", timeout=10000)
            page.wait_for_function("() => document.querySelector('body')?.innerText.includes('server-owned delayed response')", timeout=10000)
            self.assertEqual(page.locator("body").inner_text().count("server-owned delayed response"), 1)
        finally:
            page.close()

    def test_stop_after_refresh_cancels_the_recovered_job(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        email = "browser-stop-refresh@example.com"
        token = self._test_token(email)
        try:
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(self.base_url + "/", wait_until="commit", timeout=10000)
            page.wait_for_selector("#prompt", state="attached", timeout=10000)
            page.evaluate("""({ email, token }) => {
                localStorage.setItem('helper_token_v2', token);
                localStorage.setItem('helper_user_v2', JSON.stringify({ email, name: 'Browser Stop' }));
                localStorage.setItem('helper_chats_v2_' + email, JSON.stringify([
                    { id: 'stop-refresh-chat', title: 'Stop refresh', ms: [], updated_at: Date.now() }
                ]));
                localStorage.setItem('helper_active_chat_v2', 'stop-refresh-chat');
            }""", {"email": email, "token": token})
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_function("() => window.__helperAppBridgeReady === true", timeout=10000)
            page.locator("#prompt").fill("__test_delay__")
            page.locator("#main-send-btn").click()
            page.wait_for_function("""() => Object.keys(localStorage).some(key => key.startsWith('helper_active_chat_job_v3:'))""", timeout=10000)
            job_id = page.evaluate("""() => {
                const key = Object.keys(localStorage).find(item => item.startsWith('helper_active_chat_job_v3:'));
                return JSON.parse(localStorage.getItem(key)).id;
            }""")
            page.reload(wait_until="commit", timeout=10000)
            page.wait_for_selector("body", state="attached", timeout=10000)
            page.wait_for_function("() => document.querySelector('body')?.innerText.includes('Reconnecting to your response...')", timeout=10000)
            page.locator("#stop-btn").click()
            page.wait_for_function("""({ token, jobId }) => fetch('/chat/jobs/' + jobId, {
                headers: { 'Authorization': 'Bearer ' + token }
            }).then(response => response.json()).then(snapshot => snapshot.status === 'cancelled')""", arg={"token": token, "jobId": job_id}, timeout=10000)
        finally:
            page.close()

    @staticmethod
    def _test_token(email):
        secret = os.environ.get("SECRET_KEY") or "browser-test-secret"
        payload = {"sub": email, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}
        return jwt.encode(payload, secret, algorithm=os.environ.get("ALGORITHM", "HS256"))
    def test_served_shell_is_responsive_and_contains_single_interaction_surfaces(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
        try:
            # CDN assets are not part of the served-shell contract; abort them so offline browser runs reach the local DOM.
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(self.base_url + "/", wait_until="commit", timeout=10000)
            page.wait_for_selector("#settings-modal", state="attached", timeout=10000)
            page.wait_for_function("() => window.__helperAppBridgeReady === true", timeout=10000)
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
            prompt_value = page.locator("#prompt").evaluate("element => { element.value = 'responsive shell'; element.dispatchEvent(new Event('input', { bubbles: true })); return element.value; }")
            self.assertEqual(prompt_value, "responsive shell")
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), page.evaluate("() => window.innerWidth") + 1)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
