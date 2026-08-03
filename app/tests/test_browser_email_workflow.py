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


    def test_live_edits_become_metadata_only_followup_context(self):
        self.load_state_and_email_surface()
        draft = {
            "recipient": "person@example.com",
            "subject": "Original",
            "body": "Original body",
            "attachment_content": "https://images.example/private-token.png",
            "attachments": [{
                "filename": "reference.png",
                "mime_type": "image/png",
                "content": "https://images.example/private-token.png",
            }],
        }
        result = self.page.evaluate("""draft => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify(draft));
            window.hydrateEmailDraftCards(root);
            const subject = root.querySelector('.email-draft-subject');
            const body = root.querySelector('.email-draft-body-input');
            subject.value = 'Edited subject';
            body.value = 'Edited body';
            subject.dispatchEvent(new Event('input', { bubbles: true }));
            body.dispatchEvent(new Event('input', { bubbles: true }));
            return window.getActiveEmailDraftPromptContext('attach a reference image');
        }""", draft)

        self.assertTrue(result.startswith("EMAIL_DRAFT_CONTEXT:"))
        payload = result.split("EMAIL_DRAFT_CONTEXT:", 1)[1]
        context = __import__("json").loads(payload)
        self.assertEqual(context["subject"], "Edited subject")
        self.assertEqual(context["body"], "Edited body")
        self.assertNotIn("private-token", payload)
        self.assertNotIn("content", context["attachments"][0])

    def test_updated_draft_supersedes_previous_card(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => {
            const root = document.getElementById('chat-area');
            const first = document.createElement('section');
            first.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'First', body: 'First body'
            }));
            root.appendChild(first);
            window.hydrateEmailDraftCards(first);

            const second = document.createElement('section');
            second.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'Updated', body: 'Updated body'
            }));
            root.appendChild(second);
            window.hydrateEmailDraftCards(second);
            const cards = Array.from(root.querySelectorAll('.email-draft-card'));
            return {
                count: cards.length,
                hidden: cards.map(card => card.hidden),
                subjects: cards.map(card => card.querySelector('.email-draft-subject').value),
            };
        }""")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["hidden"], [True, False])
        self.assertEqual(result["subjects"], ["First", "Updated"])

    def test_empty_attachment_placeholder_renders_as_no_attachment(self):
        self.load_state_and_email_surface()
        self.page.evaluate("""() => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'No image', body: 'Draft body',
                attachment_content: null, attachment_filename: 'attachment.bin',
                attachment_type: 'application/octet-stream', attachments: []
            }));
            window.hydrateEmailDraftCards(root);
        }""")
        self.assertEqual(self.page.locator('.email-draft-attachment-label').inner_text(), 'None')

    def test_failed_image_response_keeps_existing_card_visible_without_marker(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => {
            const root = document.getElementById('chat-area');
            const existing = document.createElement('section');
            existing.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'Existing', body: 'Keep this draft'
            }));
            root.appendChild(existing);
            window.hydrateEmailDraftCards(existing);
            const failure = document.createElement('section');
            failure.innerHTML = window.renderMarkdown('I could not generate the image, so the existing email draft was not changed. Please retry.');
            root.appendChild(failure);
            window.hydrateEmailDraftCards(failure);
            return {
                cards: root.querySelectorAll('.email-draft-card').length,
                hidden: root.querySelector('.email-draft-card')?.hidden,
                text: root.innerText
            };
        }""")
        self.assertEqual(result['cards'], 1)
        self.assertFalse(result['hidden'])
        self.assertIn('draft was not changed', result['text'])
    def test_context_panel_draft_does_not_hide_chat_draft(self):
        self.load_state_and_email_surface()
        hidden = self.page.evaluate("""() => {
            const chat = document.getElementById('chat-area');
            chat.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'Chat draft', body: 'Chat body'
            }));
            window.hydrateEmailDraftCards(chat);

            const panel = document.createElement('aside');
            panel.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'Context copy', body: 'Context body'
            }));
            document.body.appendChild(panel);
            window.hydrateEmailDraftCards(panel);
            return chat.querySelector('.email-draft-card').hidden;
        }""")
        self.assertFalse(hidden)

    def test_masked_messages_are_redacted_before_persistence(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => window.helperSanitizeChatsForPersistence([{
            id: 'chat-1',
            ms: [{ r: 'u', c: 'raw-admin-key', apiPrompt: 'raw-admin-key', masked: true }]
        }])""")
        message = result[0]["ms"][0]
        self.assertEqual(message["c"], "[MASKED_SECRET]")
        self.assertNotIn("apiPrompt", message)
        self.assertNotIn("raw-admin-key", str(result))

    def test_visible_card_recovers_active_context_when_global_is_missing(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify({
                recipient: 'person@example.com', subject: 'Live subject', body: 'Live body'
            }));
            window.hydrateEmailDraftCards(root);
            window.__helperActiveEmailDraft = null;
            const card = root.querySelector('.email-draft-card');
            card.querySelector('.email-draft-subject').value = 'Edited live subject';
            card.querySelector('.email-draft-body-input').value = 'Edited live body';
            const context = window.getActiveEmailDraftPromptContext('generate an image and attach it to this email widget');
            return { context, resolved: window.resolveActiveEmailDraft() };
        }""")
        self.assertTrue(result["context"].startswith("EMAIL_DRAFT_CONTEXT:"))
        self.assertEqual(result["resolved"]["subject"], "Edited live subject")
        self.assertIn("Edited live body", result["context"])

    def test_missing_active_context_does_not_inject_stale_global(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => {
            window.__helperActiveEmailDraft = null;
            document.getElementById('chat-area').innerHTML = '';
            return window.getActiveEmailDraftPromptContext('generate an image and attach it to this email widget');
        }""")
        self.assertEqual(result, '')

if __name__ == "__main__":
    unittest.main()
