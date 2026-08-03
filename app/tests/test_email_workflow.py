import base64
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.contracts.email_draft import (
    draft_marker,
    serialize_persistable,
    serialize_prompt_context,
)
from app.logic import attachment_store
from app.logic.email_draft_image_workflow import build_email_draft_body_update_payload_from_history
from app.routes import email_delivery


OWNER = "owner@example.com"


def png_bytes(color: bytes = b"\x10\x20\x30") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + color


class EmailWorkflowTests(unittest.TestCase):
    def test_two_image_draft_round_trip_keeps_ids_and_drops_bytes_from_persistence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
            first = attachment_store.save_attachment_bytes("first.png", "image/png", png_bytes(), OWNER)
            second = attachment_store.save_attachment_bytes("second.png", "image/png", png_bytes(b"\x40\x50\x60"), OWNER)
            full = {
                "recipient": OWNER,
                "subject": "Two images",
                "body": "",
                "attachments": [
                    {**first, "filename": first["name"], "content": base64.b64encode(png_bytes()).decode()},
                    {**second, "filename": second["name"], "content": base64.b64encode(png_bytes(b"\x40\x50\x60")).decode()},
                ],
            }
            compact = serialize_prompt_context(full)
            prompt = "EMAIL_DRAFT_CONTEXT:" + json.dumps(compact) + "\n\nwrite something for the body i am lazy"
            result = build_email_draft_body_update_payload_from_history(prompt, [])
            updated = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
            persisted = serialize_persistable(updated)

            self.assertEqual([item["id"] for item in updated["attachments"]], [first["id"], second["id"]])
            self.assertNotIn('"content":', json.dumps(persisted))
            self.assertEqual(attachment_store.resolve_attachment_reference({"id": first["id"]}, OWNER)["filename"], first["name"])
            self.assertEqual(attachment_store.resolve_attachment_reference({"id": second["id"]}, OWNER)["filename"], second["name"])
            with self.assertRaises(attachment_store.AttachmentStoreError):
                attachment_store.resolve_attachment_reference({"id": first["id"], "type": "image/jpeg"}, "other@example.com")

    def test_mixed_image_and_pdf_context_preserves_metadata_without_binary_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
            image = attachment_store.save_attachment_bytes("photo.png", "image/png", png_bytes(), OWNER)
            pdf = attachment_store.save_attachment_bytes("notes.pdf", "application/pdf", b"%PDF-1.4 notes", OWNER)
            context = serialize_prompt_context({"recipient": OWNER, "attachments": [image, pdf]})
            marker = draft_marker(context, context=True)

            self.assertEqual([item["id"] for item in context["attachments"]], [image["id"], pdf["id"]])
            self.assertEqual(context["attachments"][1]["type"], "application/pdf")
            self.assertNotIn('"content":', marker)

    def test_generated_image_is_transient_and_prompt_context_is_metadata_only(self):
        draft = {
            "recipient": OWNER,
            "subject": "Generated",
            "attachments": [{
                "content": "https://image.example/generated.png",
                "filename": "generated.png",
                "type": "image/png",
                "source": "generated",
            }],
        }
        context = serialize_prompt_context(draft)
        self.assertEqual(context["attachments"][0]["source"], "generated")
        self.assertNotIn("https://image.example", json.dumps(context))

    def test_delivery_requires_request_scoped_admin_key_and_accepts_id_metadata(self):
        draft = email_delivery.EmailDraftPayload.model_validate({
            "recipient": OWNER,
            "subject": "Approved",
            "body": "Hello",
            "attachments": [{"id": "0123456789abcdef0123456789abcdef", "name": "photo.png", "type": "image/png"}],
        })
        from app.services.email_delivery_service import (
            EmailAuthorizationError,
            EmailDeliveryResult,
        )

        with patch.object(
            email_delivery.email_delivery_service,
            "send_approved_email",
            side_effect=EmailAuthorizationError("invalid"),
        ):
            with self.assertRaises(HTTPException) as raised:
                email_delivery.send_approved_email_draft(
                    email_delivery.SendDraftRequest(draft=draft, admin_key="wrong"),
                    current_user=OWNER,
                )
        self.assertEqual(raised.exception.status_code, 403)

        delivery_result = EmailDeliveryResult(
            success=True,
            status="SIMULATE SUCCESS",
            request_id="req-1",
            mode="simulated",
        )
        with patch.object(
            email_delivery.email_delivery_service,
            "send_approved_email",
            return_value=delivery_result,
        ) as send:
            result = email_delivery.send_approved_email_draft(
                email_delivery.SendDraftRequest(draft=draft, admin_key="right", request_id="req-1"),
                current_user=OWNER,
            )
        self.assertTrue(result["success"])
        sent_draft = send.call_args.kwargs["draft"]
        self.assertEqual(sent_draft.attachments[0].id, "0123456789abcdef0123456789abcdef")
        self.assertIsNone(sent_draft.attachments[0].content)


    def test_shared_delivery_service_validates_key_and_deduplicates_request(self):
        from app.logic.bus import job_id_context
        from app.logic.memory import user_context
        from app.services import email_delivery_service as service_module

        receipts = {}
        sends = []

        def sender(**kwargs):
            sends.append(kwargs)
            return f"SIMULATE SUCCESS: prepared for {kwargs['recipient']}"

        def record(job_id, owner, recipient, status):
            receipts[job_id] = status

        service = service_module.EmailDeliveryService(
            key_verifier=lambda candidate: candidate == "valid-key",
            sender=sender,
        )
        draft = {
            "recipient": OWNER,
            "subject": "Approved",
            "body": "Hello",
        }
        with (
            patch.object(service_module, "_existing_delivery", side_effect=lambda job_id: receipts.get(job_id)),
            patch.object(service_module, "_record_delivery", side_effect=record),
        ):
            with self.assertRaises(service_module.EmailAuthorizationError):
                service.send_approved_email(
                    draft=draft,
                    owner=OWNER,
                    admin_key="invalid-key",
                    request_id="same-request",
                )
            first = service.send_approved_email(
                draft=draft,
                owner=OWNER,
                admin_key="valid-key",
                request_id="same-request",
            )
            duplicate = service.send_approved_email(
                draft=draft,
                owner=OWNER,
                admin_key="valid-key",
                request_id="same-request",
            )
            restarted_service = service_module.EmailDeliveryService(
                key_verifier=lambda candidate: candidate == "valid-key",
                sender=sender,
            )
            persisted_duplicate = restarted_service.send_approved_email(
                draft=draft,
                owner=OWNER,
                admin_key="valid-key",
                request_id="same-request",
            )

        self.assertTrue(first.success)
        self.assertTrue(duplicate.duplicate)
        self.assertTrue(persisted_duplicate.duplicate)
        self.assertNotIn(OWNER, first.status)
        self.assertEqual(len(sends), 1)
        self.assertEqual(job_id_context.get(), "")
        self.assertIsNone(user_context.get())

    def test_delivery_service_rejects_invalid_recipient_and_sanitizes_sender_exception(self):
        from app.logic.bus import job_id_context
        from app.logic.memory import user_context
        from app.services import email_delivery_service as service_module

        sends = []

        def broken_sender(**kwargs):
            sends.append(kwargs)
            raise RuntimeError("provider included owner@example.com and secret material")

        service = service_module.EmailDeliveryService(
            key_verifier=lambda _candidate: True,
            sender=broken_sender,
        )
        with self.assertRaises(service_module.EmailValidationError):
            service.send_approved_email(
                draft={"recipient": "not-an-email", "subject": "Invalid", "body": "Body"},
                owner=OWNER,
                admin_key="request-only-key",
            )
        self.assertEqual(sends, [])

        result = service.send_approved_email(
            draft={"recipient": OWNER, "subject": "Safe", "body": "Body"},
            owner=OWNER,
            admin_key="request-only-key",
            request_id="sender-exception",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "Email delivery failed. The draft remains available to retry.")
        self.assertNotIn(OWNER, result.status)
        self.assertEqual(job_id_context.get(), "")
        self.assertIsNone(user_context.get())

if __name__ == "__main__":
    unittest.main()
