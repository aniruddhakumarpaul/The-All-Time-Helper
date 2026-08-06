import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BrowserEmailWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AssertionError('Python Playwright is required for browser interaction tests')
        cls.sync_playwright = staticmethod(sync_playwright)

    def setUp(self):
        self.playwright = self.sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
        except Exception as error:
            self.playwright.stop()
            raise AssertionError(f"Chromium is not available: {error}") from error
        self.page = self.browser.new_page()
        self.page.set_content("""
            <!doctype html>
            <html><head></head><body>
              <main id="chat-area"></main>
              <section id="input-wrap">
                <div class="pill-bar-container">
                  <div class="pill-bar">
                    <button id="main-send-btn" type="button">Send</button>
                    <textarea id="prompt" aria-label="Message"></textarea>
                  </div>
                </div>
              </section>
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
        self.add_script("static/js/composer_context_tray.js")
        self.page.wait_for_function("() => window.addComposerContext && document.querySelector('#composer-context-tray')")
        self.page.add_style_tag(content=(ROOT / "static/css/email_draft.css").read_text(encoding="utf-8"))
        self.page.add_style_tag(content=(ROOT / "static/css/composer_context_tray.css").read_text(encoding="utf-8"))
        self.add_script("static/js/email_draft.js")
        self.add_script("static/js/email_context_prompt.js", replacements=[("import { state } from './state.js?v=210';", "const state = window.__helperState;")])
        self.page.wait_for_function("() => window.parseEmailDraftContext && window.hydrateEmailDraftCards && window.attachEmailDraftToPrompt")

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
        self.assertEqual(self.page.locator(".email-draft-attachment-chip strong").inner_text(), "notes.pdf")
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
                trayHasContext: window.__helperState.attachedContexts.length > 0,
                chipCount: document.querySelectorAll('.email-draft-context-chip').length,
                visibleText: document.getElementById('composer-context-tray').innerText,
            };
        }""")
        self.page.wait_for_function("() => document.querySelector('#composer-context-tray').classList.contains('has-context')")
        result["visibleText"] = self.page.locator("#composer-context-tray").inner_text()
        result["chipCount"] = self.page.locator(".email-draft-context-chip").count()
        serialized = str(result["contexts"])
        self.assertNotIn("transient-secret", serialized)
        self.assertTrue(result["trayHasContext"])
        self.assertEqual(result["chipCount"], 1)
        self.assertIn("Context draft", result["visibleText"])

        self.page.locator(".email-draft-context-chip button").click()
        self.page.wait_for_function("() => document.querySelectorAll('.email-draft-context-chip').length === 0")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 0)
        self.assertFalse(self.page.locator("#composer-context-tray").get_attribute("class").find("has-context") >= 0)


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
        self.assertEqual(self.page.locator('.email-draft-attachment-empty').inner_text(), 'No attachments')

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

    def _render_email_card(self, subject="Drag subject", body="Drag body"):
        draft = {
            "recipient": "person@example.com",
            "subject": subject,
            "body": body,
            "tone": "modern",
            "attachments": [{
                "filename": "notes.pdf",
                "mime_type": "application/pdf",
                "size": 42,
                "content": "do-not-transfer",
            }],
        }
        self.page.evaluate("""draft => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify(draft));
            window.hydrateEmailDraftCards(root);
        }""", draft)
        self.page.wait_for_selector(".email-draft-drag-handle")

    def _drag_handle_to(self, target_selector):
        self.page.locator(".email-draft-drag-handle").hover()
        self.page.evaluate("""targetSelector => {
            const handle = document.querySelector('.email-draft-drag-handle');
            const target = document.querySelector(targetSelector);
            const dataTransfer = new DataTransfer();
            const options = { bubbles: true, cancelable: true, dataTransfer };
            handle.dispatchEvent(new DragEvent('dragstart', options));
            target.dispatchEvent(new DragEvent('dragenter', options));
            target.dispatchEvent(new DragEvent('dragover', options));
            target.dispatchEvent(new DragEvent('drop', options));
            handle.dispatchEvent(new DragEvent('dragend', options));
        }""", target_selector)
    def _clear_composer_context(self):
        self.page.evaluate("() => window.clearComposerContextTray()")
        self.page.wait_for_function("() => !document.querySelector('#composer-context-tray').classList.contains('has-context')")

    def test_email_handle_is_only_drag_source_and_reaches_all_composer_surfaces(self):
        self.load_state_and_email_surface()
        self._render_email_card()
        card = self.page.locator(".email-draft-card")
        handle = self.page.locator(".email-draft-drag-handle")
        self.assertEqual(card.get_attribute("draggable"), "false")
        self.assertEqual(handle.get_attribute("draggable"), "true")
        self.assertEqual(handle.get_attribute("aria-label"), "Drag this email draft into the prompt")

        self._drag_handle_to("#prompt")
        self.page.wait_for_selector(".email-draft-context-chip")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)

        self._drag_handle_to(".pill-bar")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)

        self._drag_handle_to("#composer-context-tray")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)
        context = self.page.evaluate("() => window.__helperState.attachedContexts[0]")
        self.assertIn("person@example.com", context["text"])
        self.assertIn("notes.pdf", context["text"])
        self.assertNotIn("do-not-transfer", str(context))

        self.assertNotIn("composer-context-dragging", self.page.locator("body").get_attribute("class") or "")
        self.assertNotIn("composer-drop-active", self.page.locator("#prompt").get_attribute("class") or "")

    def test_duplicate_drops_pulse_and_changed_subject_updates_one_chip(self):
        self.load_state_and_email_surface()
        self._render_email_card()
        handle = self.page.locator(".email-draft-drag-handle")
        self._drag_handle_to("#prompt")
        self.page.wait_for_selector(".email-draft-context-chip")

        self._drag_handle_to("#prompt")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)

        subject = self.page.locator(".email-draft-subject")
        subject.fill("Changed subject")
        subject.dispatch_event("input")
        self._drag_handle_to("#prompt")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)
        context = self.page.evaluate("() => window.__helperState.attachedContexts[0]")
        self.assertIn("Changed subject", context["text"])

        self.page.locator(".email-draft-use-context-btn").click()
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)

    def test_editable_fields_and_preview_are_not_drag_sources(self):
        self.load_state_and_email_surface()
        self._render_email_card()
        prompt = self.page.locator("#prompt")
        self.page.locator(".email-draft-subject").drag_to(prompt)
        self.page.locator(".email-draft-body-input").drag_to(prompt)
        self.page.evaluate('() => { const frame = document.querySelector(".email-draft-preview"); const event = new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }); frame.dispatchEvent(event); return event.defaultPrevented; }')
        self.page.wait_for_timeout(120)
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 0)
        self.assertEqual(self.page.locator(".email-draft-subject").input_value(), "Drag subject")

    def test_mobile_use_in_prompt_fallback_keeps_target_reachable(self):
        self.load_state_and_email_surface()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._render_email_card()
        handle_box = self.page.locator(".email-draft-drag-handle").bounding_box()
        self.assertIsNotNone(handle_box)
        self.assertGreaterEqual(handle_box["width"], 40)
        self.assertGreaterEqual(handle_box["height"], 40)

        self.page.locator(".email-draft-use-context-btn").click()
        self.page.wait_for_selector(".email-draft-context-chip")
        self.assertEqual(self.page.locator(".email-draft-context-chip").count(), 1)
        self.assertIn("Use this email draft", self.page.locator(".email-draft-use-context-btn").get_attribute("aria-label"))

    def test_remote_document_url_is_redacted_from_context_persistence_and_request_shape(self):
        self.load_state_and_email_surface()
        remote_url = "https://example.test/private/brief.pdf?token=secret"
        attachment_id = "0123456789abcdef0123456789abcdef"
        draft = {
            "recipient": "person@example.com",
            "subject": "Remote brief",
            "body": "Review the attached brief.",
            "attachments": [{
                "id": attachment_id,
                "filename": "brief.pdf",
                "mime_type": "application/pdf",
                "size": 2048,
                "url": remote_url,
                "source": "remote",
            }],
        }
        self.page.evaluate("""draft => {
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify(draft));
            window.hydrateEmailDraftCards(root);
        }""", draft)
        self.page.locator(".email-draft-attachment-chip").click()
        self.page.wait_for_selector(".composer-context-document")
        result = self.page.evaluate("""url => {
            const context = window.__helperState.attachedContexts[0];
            const fence = String.fromCharCode(34).repeat(3);
            const requestPrompt = '[Attached Context 1]\\n' + fence + context.text + '\\n' + fence;
            const persisted = window.helperSanitizeChatsForPersistence([{
                id: 'remote-doc-chat',
                ms: [{ r: 'u', c: 'Review the brief', apiPrompt: requestPrompt, attachments: [context.attachmentRef] }]
            }]);
            return {
                context,
                requestPrompt,
                persisted: JSON.stringify(persisted),
                visible: document.body.innerText,
                dom: document.body.innerHTML,
                requestAttachments: [context.attachmentRef],
                url,
            };
        }""", remote_url)

        for field in ("context", "requestPrompt", "persisted", "visible", "dom"):
            self.assertNotIn(remote_url, str(result[field]))
        self.assertEqual(result["requestAttachments"][0]["id"], attachment_id)
        self.assertEqual(result["requestAttachments"][0]["name"], "brief.pdf")
        self.assertEqual(result["requestAttachments"][0]["type"], "application/pdf")
        self.assertIn("Owner-scoped attachment available", result["context"]["text"])

    def test_native_image_drag_preserves_file_transfer_types_and_context(self):
        self.load_state_and_email_surface()
        self.page.evaluate("""() => {
            const bubble = document.createElement('article');
            bubble.className = 'msg b-msg';
            bubble.innerHTML = '<div class="txt"><p>Generated reference</p><img class="chat-rendered-img" alt="Generated reference" src="https://example.test/generated.png" style="width:180px;height:120px;display:block;"></div>';
            document.getElementById('chat-area').appendChild(bubble);
            window.__dragAudit = [];
            document.addEventListener('dragstart', event => {
                window.__dragAudit.push({
                    types: Array.from(event.dataTransfer?.types || []),
                    uri: event.dataTransfer?.getData('text/uri-list') || '',
                    download: event.dataTransfer?.getData('DownloadURL') || '',
                    plain: event.dataTransfer?.getData('text/plain') || '',
                });
            }, true);
            window.syncComposerDragSources();
        }""")
        image = self.page.locator(".chat-rendered-img")
        self.assertEqual(image.get_attribute("draggable"), "true")
        image.drag_to(self.page.locator("#prompt"))
        self.page.wait_for_selector(".composer-context-image")
        audit = self.page.evaluate("() => window.__dragAudit[window.__dragAudit.length - 1]")
        self.assertIn("application/x-helper-composer-context", audit["types"])
        self.assertIn("text/uri-list", audit["types"])
        self.assertIn("downloadurl", [item.lower() for item in audit["types"]])
        self.assertEqual(audit["uri"], "https://example.test/generated.png\r\n")
        self.assertIn("image/png:Generated-reference.png:https://example.test/generated.png", audit["download"])
        self.assertEqual(audit["plain"], "https://example.test/generated.png")
        self.assertIn("https://example.test/generated.png", self.page.evaluate("() => window.__helperState.attachedContexts[0].text"))

    def test_production_generated_image_drag_and_post_upscale_refresh(self):
        self.load_state_and_email_surface()
        self.add_script(
            "static/js/ui.js",
            module=True,
            replacements=[
                ("import { state } from './state.js?v=210';", "const state = window.__helperState;"),
                ("import { sortChatsNewestFirst } from './chat_sync.js?v=203';", "const sortChatsNewestFirst = chats => chats;"),
            ],
        )
        self.add_script("static/js/utils.js")
        generated_url = "https://image.pollinations.ai/prompt/digital%20india%20infrastructure?model=flux&width=1024&height=1024&seed=123&uid=test-job"
        enhanced_url = "https://cdn.example.test/enhanced-image.png"
        rendered = self.page.evaluate("""({ generatedUrl, enhancedUrl }) => {
            const root = document.getElementById('chat-area');
            window.marked = {
                Renderer: function () {},
                parse: (_text, options) => {
                    const alt = 'Digital India infrastructure';
                    return options.renderer.image({ href: window.__testGeneratedUrl, text: alt }, null, alt);
                },
            };
            window.__testGeneratedUrl = generatedUrl;
            root.innerHTML = window.renderMarkdown('![Digital India infrastructure](' + generatedUrl + ')');
            const image = root.querySelector('.chat-rendered-img');
            image.style.display = 'block';
            image.src = generatedUrl;
            image.dataset.loaded = 'true';
            window.hydrateRenderedMarkdown(root);
            window.syncComposerDragSources();
            window.fetch = async () => ({
                json: async () => ({ success: true, status: 'ready', url: enhancedUrl }),
            });
            window.Image = class {
                set src(value) {
                    this.currentSrc = value;
                    if (this.onload) this.onload();
                }
            };
            window.initUpscaleImagePolling(root);
            return root.innerHTML;
        }""", {"generatedUrl": generated_url, "enhancedUrl": enhanced_url})
        self.assertIn("chat-rendered-img", rendered)
        self.page.wait_for_function("() => document.querySelector('.chat-rendered-img')?.src === 'https://cdn.example.test/enhanced-image.png'")
        image = self.page.locator(".chat-rendered-img")
        self.assertEqual(image.get_attribute("data-generated"), "true")
        self.assertEqual(self.page.locator(".chat-image-actions").count(), 1)
        self.assertEqual(image.get_attribute("draggable"), "true")

        self.page.evaluate("""() => {
            window.__dragAudit = [];
            document.addEventListener('dragstart', event => {
                window.__dragAudit.push({
                    types: Array.from(event.dataTransfer?.types || []),
                    uri: event.dataTransfer?.getData('text/uri-list') || '',
                    download: event.dataTransfer?.getData('DownloadURL') || '',
                    plain: event.dataTransfer?.getData('text/plain') || '',
                });
            }, true);
            window.syncComposerDragSources();
        }""")
        image.drag_to(self.page.locator("#prompt"))
        self.page.wait_for_selector(".composer-context-image")
        audit = self.page.evaluate("() => window.__dragAudit[window.__dragAudit.length - 1]")
        self.assertIn("application/x-helper-composer-context", audit["types"])
        self.assertIn("text/uri-list", audit["types"])
        self.assertIn("downloadurl", [item.lower() for item in audit["types"]])
        self.assertEqual(audit["uri"], enhanced_url + "\r\n")
        self.assertIn("image/png:Digital-India-infrastructure.png:" + enhanced_url, audit["download"])
        self.assertEqual(audit["plain"], enhanced_url)
        contexts = self.page.evaluate("() => window.__helperState.attachedContexts")
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["kind"], "image")
        self.assertNotIn("base64", str(contexts))
        self.assertEqual(self.page.locator(".chat-image-actions").count(), 1)
    def test_data_url_image_drag_never_enters_native_file_transfer(self):
        self.load_state_and_email_surface()
        result = self.page.evaluate("""() => {
            const image = document.createElement('img');
            image.className = 'chat-img-preview';
            image.alt = 'Transient preview';
            image.src = 'data:image/png;base64,not-persisted';
            document.getElementById('chat-area').appendChild(image);
            window.__dragAudit = null;
            document.addEventListener('dragstart', event => {
                window.__dragAudit = {
                    types: Array.from(event.dataTransfer?.types || []),
                    uri: event.dataTransfer?.getData('text/uri-list') || '',
                    download: event.dataTransfer?.getData('DownloadURL') || '',
                };
            }, true);
            window.syncComposerDragSources();
            const transfer = new DataTransfer();
            image.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: transfer }));
            return window.__dragAudit;
        }""")
        self.assertIn("application/x-helper-composer-context", result["types"])
        self.assertNotIn("text/uri-list", result["types"])
        self.assertNotIn("DownloadURL", result["types"])
        self.assertEqual(result["uri"], "")
        self.assertEqual(result["download"], "")

    def test_attachment_actions_keep_images_and_documents_typed(self):
        self.load_state_and_email_surface()
        draft = {
            "recipient": "person@example.com",
            "subject": "Attachment actions",
            "body": "Review these files",
            "attachments": [
                {
                    "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "filename": "reference.png",
                    "mime_type": "image/png",
                    "source": "upload",
                    "url": "https://example.test/reference.png",
                    "available": True,
                },
                {
                    "filename": "Edit the draft change the tone and include the product image in the email.png",
                    "mime_type": "image/png",
                    "source": "generated",
                    "url": "https://example.test/generated.png",
                    "available": True,
                },
                {
                    "id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "filename": "brief.pdf",
                    "mime_type": "application/pdf",
                    "source": "upload",
                    "available": True,
                },
                {
                    "id": "cccccccccccccccccccccccccccccccc",
                    "filename": "expired.png",
                    "mime_type": "image/png",
                    "source": "upload",
                    "available": False,
                },
            ],
        }
        self.page.evaluate("""draft => {
            window.openImageModal = (src, options) => { window.__openedImage = { src, ...options }; };
            const root = document.getElementById('chat-area');
            root.innerHTML = window.renderMarkdown('EMAIL_DRAFT_PAYLOAD:' + JSON.stringify(draft));
            window.hydrateEmailDraftCards(root);
        }""", draft)
        self.assertEqual(self.page.locator(".email-draft-attachment-row").count(), 4)
        self.assertEqual(self.page.locator(".email-draft-attachment-chip").count(), 4)
        self.assertIn("tone-include-product-image.png", self.page.locator(".email-draft-attachment-row").nth(1).inner_text())
        self.assertEqual(self.page.locator("[aria-disabled='true']").count(), 1)
        self.assertNotIn("https://example.test", self.page.locator("#chat-area").inner_text())
        self.page.locator(".email-draft-attachment-chip").nth(0).click()
        self.page.wait_for_function("() => window.__openedImage && window.__openedImage.filename === 'reference.png'")
        opened = self.page.evaluate("() => window.__openedImage")
        self.assertEqual(opened["filename"], "reference.png")
        self.assertEqual(opened["source"], "Uploaded")
        self.assertEqual(opened["downloadable"], True)

        self.page.locator(".email-draft-attachment-use").nth(1).click()
        self.page.locator(".email-draft-attachment-use").nth(2).click()
        self.page.wait_for_function("() => document.querySelectorAll('.composer-context-chip').length === 2")
        contexts = self.page.evaluate("() => window.__helperState.attachedContexts")
        self.assertEqual([item["kind"] for item in contexts], ["image", "document"])
        self.assertTrue(all("content" not in item["text"].lower() for item in contexts))
        self.assertIn("brief.pdf", contexts[1]["text"])
        self.assertEqual(contexts[1]["attachmentRef"]["id"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(self.page.locator(".email-draft-attachment-use").count(), 3)
if __name__ == "__main__":
    unittest.main()
