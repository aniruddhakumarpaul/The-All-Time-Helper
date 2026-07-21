import json
import sqlite3
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]


class ProductFrontendAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        cls.api_js = (ROOT / "static" / "js" / "api.js").read_text(encoding="utf-8")
        cls.ui_js = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        cls.bootstrap_js = (ROOT / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        cls.dialog_js = (ROOT / "static" / "js" / "dialog_manager.js").read_text(encoding="utf-8")
        cls.job_js = (ROOT / "static" / "js" / "job_center.js").read_text(encoding="utf-8")
        cls.status_js = (ROOT / "static" / "js" / "admin_dashboard.js").read_text(encoding="utf-8")

    def test_primary_controls_have_native_semantics_and_names(self):
        required = [
            'id="mobile-menu-btn" aria-label="Open navigation"',
            'id="open-settings-btn"',
            'id="model-toggle" aria-label="Choose assistant route"',
            'class="action-btn img-btn" id="attach-files-btn" aria-label="Attach files"',
            'id="export-chat-btn" title="Export chat as Markdown" aria-label="Export chat as Markdown"',
            'id="prompt" placeholder="Ask me anything..." aria-label="Message The All Time Helper"',
            'id="main-send-btn" class="action-btn send-btn" aria-label="Send message"',
            'id="persona-toggle" aria-describedby="persona-help"',
        ]
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.template)

        self.assertIn('<button type="button" id="mobile-menu-btn"', self.template)
        self.assertIn('<button type="button" class="set-btn" id="open-settings-btn"', self.template)
        self.assertIn('<button type="button" id="model-toggle"', self.template)

    def test_auth_controls_are_labeled_and_browser_assisted(self):
        required = [
            'id="l-email" placeholder="Email Address" aria-label="Email address" autocomplete="username"',
            'id="l-pwd" placeholder="Password" aria-label="Password" autocomplete="current-password"',
            'id="s-name" placeholder="Full Name" aria-label="Full name" autocomplete="name"',
            'id="s-pwd" placeholder="Password" aria-label="Password" autocomplete="new-password" minlength="8"',
            'id="v-otp" inputmode="numeric" maxlength="6"',
            'aria-label="Six digit verification code" autocomplete="one-time-code"',
        ]
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.template)

        self.assertNotIn("alert(", self.app_js)
        self.assertIn("ui.notify(", self.app_js)
        self.assertIn("validateAuthInput", self.app_js)

    def test_dialogs_are_isolated_and_restore_focus(self):
        for dialog_id in (
            "auth-overlay",
            "neural-context-card",
            "settings-modal",
            "cmd-palette",
            "delete-confirm-modal",
            "theme-modal",
            "image-modal",
        ):
            with self.subTest(dialog_id=dialog_id):
                self.assertRegex(
                    self.template,
                    rf'id="{dialog_id}"[^>]*(?:role="dialog"[^>]*data-helper-dialog|data-helper-dialog[^>]*role="dialog")',
                )

        self.assertIn("injectScript('dialog_manager', '1', 'dialog-manager')", self.bootstrap_js)
        self.assertIn("child.inert = true", self.dialog_js)
        self.assertIn("function restoreFocus", self.dialog_js)
        self.assertIn("event.key !== 'Tab'", self.dialog_js)
        self.assertIn("focusableElements", self.dialog_js)

    def test_preferences_persist_and_drive_chat_policy(self):
        self.assertIn("helper_preferences_v1", self.ui_js)
        self.assertIn("function loadPreferences()", self.ui_js)
        self.assertIn("function persistPreferences()", self.ui_js)
        self.assertIn("helper_response_style_v1", self.ui_js)
        self.assertIn("response_style: state.responseStyle || 'adaptive'", self.app_js)
        self.assertIn("ui.loadPreferences()", self.app_js)
        self.assertIn('id="response-style-setting"', self.template)
        for style in ("adaptive", "concise", "deep", "creative"):
            self.assertIn(f'<option value="{style}">', self.template)

    def test_each_sidebar_utility_has_one_purpose(self):
        self.assertIn("on('open-settings-btn', 'click', ui.openSettings)", self.app_js)
        self.assertNotIn("#open-settings-btn, .set-btn", self.app_js)
        self.assertIn("<span>Active Tasks</span>", self.job_js)
        self.assertIn("<span>System Status</span>", self.status_js)
        self.assertNotIn("Job Center", self.job_js)
        self.assertNotIn("Admin Operations", self.status_js)

    def test_choice_surfaces_are_keyboard_operable_and_non_destructive(self):
        required = [
            'id="model-menu" role="listbox" aria-label="Assistant route"',
            '<button type="button" class="model-opt" role="option"',
            'id="attach-files-btn" aria-label="Attach files"',
            'class="menu-item" role="menuitemradio"',
            '<button type="button" class="theme-opt" aria-pressed=',
        ]
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.template)

        self.assertIn("on('attach-files-btn', 'click'", self.app_js)
        self.assertIn("on('model-menu', 'keydown'", self.app_js)
        self.assertIn("on('theme-menu-settings', 'keydown'", self.app_js)
        self.assertIn("candidate.setAttribute('aria-selected'", self.ui_js)
        self.assertNotIn("} else if (event.key === 'Escape') {\n        startNewChat();", self.app_js)
    def test_task_cancellation_surfaces_failures(self):
        self.assertIn("if (!response.ok || data.success === false)", self.job_js)
        self.assertIn("button.disabled = true", self.job_js)
        self.assertIn("showJobError", self.job_js)
        self.assertIn("loadJobs({ showLoading: false })", self.job_js)
        self.assertNotIn("finally {\n            loadJobs();", self.job_js)

    def test_api_adapter_normalizes_http_and_network_failures(self):
        self.assertIn("async function parseJsonResponse", self.api_js)
        self.assertIn("payload?.detail", self.api_js)
        self.assertIn("status: response.status", self.api_js)
        self.assertIn("Check your connection and try again", self.api_js)

    def test_original_brand_contract_remains_active(self):
        self.assertIn('/static/css/style_v3.css?v=146', self.template)
        self.assertIn('id="particle-canvas"', self.template)
        self.assertIn('id="center-greet"', self.template)
        self.assertIn("var(--greet-grad)", self.template)
        self.assertNotIn("flagship.css", self.template)


class ProductBackendAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_status_is_useful_but_does_not_expose_runtime_secrets(self):
        from app.routes import admin

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE chats (id TEXT PRIMARY KEY, user_email TEXT, title TEXT, messages_json TEXT, updated_at REAL)"
        )
        db.execute(
            "INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
            ("chat-1", "user@example.com", "Test", "[]", 1.0),
        )

        with (
            patch.object(admin, "_has_real_env", return_value=True),
            patch.object(admin, "_ollama_status", AsyncMock(return_value={"running": True, "model_count": 2})),
            patch.object(admin, "_memory_status", return_value={"healthy": True}),
            patch.object(admin, "_public_link_active", return_value=(True, True)),
            patch.object(admin, "DB_FILE", Path(__file__)),
        ):
            result = await admin.admin_status(current_user="user@example.com", db=db)

        db.close()
        self.assertTrue(result["success"])
        self.assertEqual(result["user"], "user@example.com")
        self.assertIsNotNone(datetime.fromisoformat(result["generated_at"]).tzinfo)
        names = {component["name"] for component in result["components"]}
        self.assertIn("Cloud assistant", names)
        self.assertIn("Active tasks", names)
        self.assertIn("Email delivery", names)

        encoded = json.dumps(result).lower()
        for forbidden_key in (
            '"public_url"',
            '"path"',
            '"env_names"',
            '"configured_models"',
            '"admin_key_configured"',
            '"error"',
            '"url"',
        ):
            with self.subTest(forbidden_key=forbidden_key):
                self.assertNotIn(forbidden_key, encoded)

    async def test_request_models_bound_expensive_inputs(self):
        from app.routes.chat import Attachment, ChatRequest, RetrieveRequest

        with self.assertRaises(ValidationError):
            RetrieveRequest(text="query", n=11)
        with self.assertRaises(ValidationError):
            RetrieveRequest(text="", n=3)
        with self.assertRaises(ValidationError):
            ChatRequest(prompt="x", history=[{}] * 201)
        with self.assertRaises(ValidationError):
            ChatRequest(
                prompt="x",
                attachments=[Attachment(name=f"{index}.png") for index in range(7)],
            )

    async def test_task_cancel_routes_use_real_not_found_responses(self):
        from app.routes import chat, jobs

        with self.assertRaises(HTTPException) as jobs_error:
            jobs.cancel_job("not-a-task", current_user="user@example.com")
        self.assertEqual(jobs_error.exception.status_code, 404)

        with self.assertRaises(HTTPException) as chat_error:
            await chat.cancel_chat_job("not-a-task", current_user="user@example.com")
        self.assertEqual(chat_error.exception.status_code, 404)

        valid_id = str(uuid.uuid4())
        with patch.object(jobs.inference_queue, "cancel", return_value=False):
            with self.assertRaises(HTTPException) as owner_error:
                jobs.cancel_job(valid_id, current_user="user@example.com")
        self.assertEqual(owner_error.exception.status_code, 404)

    async def test_chat_failures_are_logged_but_not_streamed_verbatim(self):
        route = (ROOT / "app" / "routes" / "chat.py").read_text(encoding="utf-8")
        self.assertIn('logger.exception("[Chat] Assistant task failed', route)
        self.assertIn("I could not complete that response. Please retry or choose another route.", route)
        self.assertNotIn('**Agent Error:** {str(exc)}', route)
        self.assertNotIn('return {"error": str(exc)}', route)


if __name__ == "__main__":
    unittest.main()
