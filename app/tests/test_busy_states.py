import unittest
from pathlib import Path


class FrontendBusyStateTests(unittest.TestCase):
    def test_busy_state_script_tracks_restore_context_and_attachments(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "busy_states.js").read_text(encoding="utf-8")
        self.assertIn("/get_chats", script)
        self.assertIn("/retrieve_context", script)
        self.assertIn("/attachments", script)
        self.assertIn("chat-restore-busy", script)
        self.assertIn("neural-context-busy", script)
        self.assertIn("attachment-upload-busy", script)
        self.assertIn("setHelperBusyState", script)

    def test_busy_state_styles_are_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "busy_states.css").read_text(encoding="utf-8")
        self.assertIn("busy_states.css?v=1", animations)
        self.assertIn("helper-floating-busy", css)
        self.assertIn("helper-inline-busy", css)
        self.assertIn("email-send-spinner", css)
        self.assertIn("img-btn.is-uploading", css)
        self.assertIn("helper-busy-spin", css)

    def test_bootstrap_loads_busy_script_and_email_approval_v2(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("injectScript('busy_states', '1', 'busy-states')", bootstrap)
        self.assertIn("injectScript('email_approval', '2', 'draft-send')", bootstrap)
        self.assertIn("injectScript('composer_context_tray', '6', 'composer-context-tray')", bootstrap)

    def test_email_approval_has_real_spinner_state(self):
        root = Path(__file__).resolve().parents[2]
        email = (root / "static" / "js" / "email_approval.js").read_text(encoding="utf-8")
        self.assertIn("setEmailSending", email)
        self.assertIn("email-send-spinner", email)
        self.assertIn("is-sending-email", email)
        self.assertIn("setHelperBusyState", email)
        self.assertIn("Validating and sending", email)


if __name__ == "__main__":
    unittest.main()
