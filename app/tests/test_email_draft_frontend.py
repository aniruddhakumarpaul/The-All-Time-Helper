import unittest
from pathlib import Path


class EmailDraftFrontendTests(unittest.TestCase):
    def test_rendered_outerhtml_cards_rehydrate_field_sync_and_use_button(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "email_draft.js").read_text(encoding="utf-8")
        self.assertIn("function syncDraftFromCard", script)
        self.assertIn("card.dataset.emailDraft = JSON.stringify(current)", script)
        self.assertIn(".email-draft-recipient, .email-draft-subject, .email-draft-tone, .email-draft-body-input", script)
        self.assertIn(".email-draft-use-context-btn", script)
        self.assertIn("window.attachEmailDraftToPrompt", script)
        self.assertIn("window.syncEmailDraftFromCard = syncDraftFromCard", script)

    def test_collector_reads_current_fields_before_drag_or_send(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "email_draft.js").read_text(encoding="utf-8")
        self.assertIn("const fromCard = syncDraftFromCard(card)", script)
        self.assertIn("return normalizeDraft(fromCard)", script)
        self.assertIn("event.dataTransfer.setData(DRAFT_MIME, JSON.stringify(emailDraft))", script)
        self.assertIn("EMAIL_DRAFT_CONTEXT:${JSON.stringify(emailDraft)}", script)

    def test_approval_uses_shared_current_draft_collector(self):
        root = Path(__file__).resolve().parents[2]
        approval = (root / "static" / "js" / "email_approval.js").read_text(encoding="utf-8")
        self.assertIn("window.collectEmailDraftForDrag", approval)
        self.assertIn("body: JSON.stringify({ draft, admin_key: approvalSecret", approval)


if __name__ == "__main__":
    unittest.main()
