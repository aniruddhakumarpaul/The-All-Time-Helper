import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BrowserEmailWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            cls.sync_playwright = None
            return
        cls.sync_playwright = staticmethod(sync_playwright)

    def setUp(self):
        if self.sync_playwright is None:
            self.skipTest("Python Playwright is not installed")
        self.playwright = self.sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
        except Exception as error:
            self.playwright.stop()
            self.skipTest(f"Chromium is not available: {error}")
        self.page = self.browser.new_page()
        self.page.set_content("""
            <!doctype html>
            <html><head></head><body>
              <main id="chat-area"></main>
              <section id="prompt-shell"><div id="prompt-context-tray"></div><textarea id="prompt"></textarea></section>
            </body></html>
        """)

    def tearDown(self):
        if getattr(self, "browser", None):
            self.browser.close()
        if getattr(self, "playwright", None):
            self.playwright.stop()

    def add_script(self, relative_path, *, module=False, replacements=None):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for old, new in replacements or []:
            self.assertEqual(source.count(old), 1, f"Unexpected script contract in {relative_path}")
            source = source.replace(old, new)
        self.page.add_script_tag(content=source, type="module" if module else None)

    def load_state_and_email_surface(self):
        self.add_script("static/js/email_draft_contract.js")
        self.add_script("static/js/state.js", module=True)
        self.page.wait_for_function("() => window.__helperState")
        self.add_script("static/js/email_draft.js")
        self.page.wait_for_function("() => window.parseEmailDraftContext && window.hydrateEmailDraftCards")

    def test_draft_card_renders_and_editing_preserves_metadata(self):
        self.load_state_and_email_surface()
        draft = {
            "schema_version": 1,
            "recipient": "person@example.com",
            "subject": "A safe draft",
            "body": "First line\nSecond line",
            "attachments": [{
                "id": "upload-1",
                "filename": "notes.pdf",
                "mime_type": "application/pdf",
                "size": 42,
                "content": "transient-secret",
            }],
        }
        self.page.evaluate("""draft => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify(draft));
            window.hydrateEmailDraftCards(root);
        }""", draft)

        self.assertEqual(self.page.locator(".email-draft-card").count(), 1)
        self.assertEqual(self.page.locator(".email-draft-recipient").input_value(), "person@example.com")
        self.assertEqual(self.page.locator(".email-draft-attachment-label").inner_text(), "notes.pdf")
        self.assertNotIn("transient-secret", self.page.locator("#chat-area").inner_text())

        self.page.locator(".email-draft-body-input").fill("Edited body")
        self.page.locator(".email-draft-body-input").dispatch_event("change")
        payload = self.page.evaluate("""() => {
            const card = document.querySelector('.email-draft-card');
            return window.syncEmailDraftFromCard(card);
        }""")
        self.assertEqual(payload["body"], "Edited body")
        self.assertEqual(payload["attachments"][0]["filename"], "notes.pdf")
        self.assertEqual(payload["attachments"][0]["content"], "transient-secret")

    def test_prompt_context_redacts_content_and_can_be_removed(self):
        self.load_state_and_email_surface()
        self.add_script(
            "static/js/email_context_prompt.js",
            replacements=[("import { state } from './state.js?v=210';", "const state = window.__helperState;")],
        )
        self.page.wait_for_function("() => window.attachEmailDraftToPrompt")
        result = self.page.evaluate("""() => {
            window.attachEmailDraftToPrompt({
                recipient: 'person@example.com',
                subject: 'Context draft',
                body: 'Use the metadata only.',
                attachment_content: 'transient-secret',
                attachments: [{ filename: 'notes.pdf', mime_type: 'application/pdf', content: 'transient-secret' }]
            });
            return {
                contexts: window.__helperState.attachedContexts,
                trayDisplay: document.getElementById('prompt-context-tray').style.display,
                chipCount: document.querySelectorAll('.email-draft-context-chip').length,
                visibleText: document.getElementById('prompt-context-tray').innerText,
            };
        }""")
        serialized = str(result["contexts"])
        self.assertNotIn("transient-secret", serialized)
        self.assertEqual(result["trayDisplay"], "flex")
        self.assertEqual(result["chipCount"], 1)
        self.assertIn("Context draft", result["visibleText"])

        self.page.locator(".email-draft-context-chip button").click()
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 0)
        self.assertEqual(self.page.locator("#prompt-context-tray").get_attribute("style"), "display: none;")


if __name__ == "__main__":
    unittest.main()
