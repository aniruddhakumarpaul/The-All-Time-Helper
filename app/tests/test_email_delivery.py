import unittest
from unittest.mock import patch

from fastapi import HTTPException


class EmailDeliveryRouteTests(unittest.TestCase):
    def test_send_approved_email_draft_requires_admin_key(self):
        from app.routes import email_delivery

        req = email_delivery.SendDraftRequest(
            draft={"recipient": "person@example.com", "subject": "Hello", "body": "Body"},
            admin_key="wrong",
            request_id="req-1",
        )

        with patch.object(email_delivery, "verify_admin_key", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                email_delivery.send_approved_email_draft(req, current_user="owner@example.com")

        self.assertEqual(ctx.exception.status_code, 403)

    def test_send_approved_email_draft_uses_deterministic_sender(self):
        from app.routes import email_delivery

        req = email_delivery.SendDraftRequest(
            draft={
                "recipient": "person@example.com",
                "subject": "Hello",
                "body": "Approved body",
                "tone": "modern",
                "attachments": [],
            },
            admin_key="configured-secret",
            request_id="req-1",
        )

        with patch.object(email_delivery, "verify_admin_key", return_value=True):
            with patch.object(
                email_delivery,
                "send_or_simulate_email",
                return_value="SIMULATE SUCCESS: Email prepared for person@example.com.",
            ) as sender:
                result = email_delivery.send_approved_email_draft(req, current_user="owner@example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "simulated")
        sender.assert_called_once()
        kwargs = sender.call_args.kwargs
        self.assertEqual(kwargs["recipient"], "person@example.com")
        self.assertEqual(kwargs["subject"], "Hello")
        self.assertEqual(kwargs["body"], "Approved body")
        self.assertEqual(kwargs["owner"], "owner@example.com")

    def test_send_approved_email_draft_rejects_large_payload_shape(self):
        from app.routes import email_delivery

        req = email_delivery.SendDraftRequest(
            draft={
                "recipient": "person@example.com",
                "subject": "Hello",
                "body": "Body",
                "attachments": [{"filename": f"{i}.txt", "content": "AA=="} for i in range(11)],
            },
            admin_key="configured-secret",
            request_id="req-1",
        )

        with patch.object(email_delivery, "verify_admin_key", return_value=True):
            with self.assertRaises(HTTPException) as ctx:
                email_delivery.send_approved_email_draft(req, current_user="owner@example.com")

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
