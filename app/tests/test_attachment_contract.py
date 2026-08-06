import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class AttachmentContractTests(unittest.TestCase):
    def test_attachment_ids_are_bounded_and_owner_metadata_wins(self):
        from app.logic import attachment_store

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "brief.pdf",
                    "application/pdf",
                    b"%PDF-1.4 minimal",
                    "owner@example.com",
                )
                resolved = attachment_store.resolve_attachment_reference(
                    {"id": saved["id"], "name": "fake.png", "type": "image/png"},
                    "owner@example.com",
                )
                self.assertEqual(resolved["type"], "application/pdf")
                self.assertEqual(resolved["filename"], "brief.pdf")
                with self.assertRaises(attachment_store.AttachmentStoreError):
                    attachment_store.resolve_attachment_metadata("../escape", "owner@example.com")

    def test_documents_are_not_visual_inputs(self):
        from app.logic.agent_context import _is_visual_item
        from app.routes.chat import _is_visual_attachment

        document = {"name": "brief.pdf", "type": "application/pdf"}
        image = {"name": "photo.png", "type": "image/png"}
        self.assertFalse(_is_visual_item(document))
        self.assertFalse(_is_visual_attachment(document))
        self.assertTrue(_is_visual_item(image))
        self.assertTrue(_is_visual_attachment(image))

    def test_chat_persistence_keeps_attachment_payloads_out_of_history(self):
        app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        state = (ROOT / "static" / "js" / "state.js").read_text(encoding="utf-8")
        repair = (ROOT / "static" / "js" / "email_draft_repair.js").read_text(encoding="utf-8")
        self.assertNotIn("chat.ms.push({ r: 'u', c: userText, i: state.currentImg", app)
        self.assertIn("helperSanitizeChatsForPersistence", app)
        self.assertIn("delete next.i", state)
        self.assertIn("delete next.attachment_content", state)
        self.assertIn("helperSanitizeChatsForPersistence", repair)
        self.assertNotIn("$1", (ROOT / "static/js/ui.js").read_text(encoding="utf-8"))

    def test_frontend_attachment_preview_does_not_force_vision_for_documents(self):
        ui = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        self.assertIn("attachment-file-label", ui)
        self.assertIn("file.type.startsWith('image/')", ui)
        self.assertIn("selModel('helper-auto', 'Helper Auto')", ui)
        self.assertNotIn("selModel('moondream', 'Moondream (Vision)')", ui)



    def test_attachment_download_boundary_is_owner_scoped_and_private(self):
        from fastapi import HTTPException
        from app.logic import attachment_store
        from app.routes import chat

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "brief.pdf",
                    "application/pdf",
                    b"%PDF-1.4 minimal",
                    "owner@example.com",
                )
                response = chat.get_attachment(saved["id"], "owner@example.com")
                self.assertEqual(response.media_type, "application/pdf")
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                self.assertIn("brief.pdf", response.headers["content-disposition"])
                with self.assertRaises(HTTPException) as different_owner:
                    chat.get_attachment(saved["id"], "other@example.com")
                self.assertEqual(different_owner.exception.status_code, 404)
                self.assertEqual(different_owner.exception.detail, "Attachment unavailable.")
                with self.assertRaises(HTTPException):
                    chat.get_attachment("0" * 32, "owner@example.com")
                with patch.object(attachment_store, "ATTACHMENT_TTL_SECONDS", 1):
                    attachment_store.cleanup_expired_attachments(now=attachment_store.time.time() + 10)
                with self.assertRaises(HTTPException) as expired:
                    chat.get_attachment(saved["id"], "owner@example.com")
                self.assertEqual(expired.exception.detail, "Attachment unavailable.")
                self.assertNotIn(tmp, str(expired.exception.detail))


if __name__ == "__main__":
    unittest.main()