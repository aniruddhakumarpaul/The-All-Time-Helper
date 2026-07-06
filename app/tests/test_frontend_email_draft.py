import unittest
from pathlib import Path


class FrontendEmailDraftTests(unittest.TestCase):
    def test_email_draft_frontend_helpers_are_loaded_and_safe(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        helper_js = (root / "static" / "js" / "email_draft.js").read_text(encoding="utf-8")

        self.assertIn('/static/js/email_draft.js', template)
        self.assertLess(template.index('/static/js/email_draft.js'), template.index('/static/js/app.js'))

        self.assertIn('function parseEmailDraftContext(text)', helper_js)
        self.assertIn('function stripInternalEmailDraftMarkers(text)', helper_js)
        self.assertIn('function buildEmailDraftDragContext(message, widgetEl = null)', helper_js)
        self.assertIn('function getVisibleUserMessageContent(message, element = null)', helper_js)
        self.assertIn('function collectEmailDraftForDrag(card)', helper_js)
        self.assertIn('function approveEmailDraft(card)', helper_js)
        self.assertIn('EMAIL_DRAFT_CONTEXT:', helper_js)
        self.assertIn('EMAIL_DRAFT_PAYLOAD:', helper_js)
        self.assertIn('application/x-helper-email-draft', helper_js)
        self.assertIn('card.__emailDraft = draft', helper_js)
        self.assertIn('email-draft-approve-btn', helper_js)
        self.assertIn("fetch('/email/send-draft'", helper_js)
        self.assertIn('admin_key: adminKey', helper_js)
        self.assertIn('event.dataTransfer.setData("text/plain", `EMAIL_DRAFT_CONTEXT:${JSON.stringify(emailDraft)}`)', helper_js)
        self.assertIn("iframe.setAttribute('sandbox', '')", helper_js)
        self.assertNotIn('allow-scripts', helper_js)
        self.assertNotIn('localStorage.setItem(\'admin', helper_js)
        self.assertNotIn('localStorage.setItem("admin', helper_js)


if __name__ == "__main__":
    unittest.main()
