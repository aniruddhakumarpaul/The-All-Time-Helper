import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-email-widget-intercept")


class EmailWidgetInterceptTests(unittest.TestCase):
    def test_detects_image_to_email_widget_with_typo(self):
        from app.services.email_widget_intercept import _is_email_widget_attachment_request

        self.assertTrue(_is_email_widget_attachment_request("attach this pic to the email wedgit"))
        self.assertTrue(_is_email_widget_attachment_request("add the last image to the email widget"))
        self.assertFalse(_is_email_widget_attachment_request("send the email now"))
        self.assertFalse(_is_email_widget_attachment_request("what is this image?"))

    def test_builds_blank_recipient_draft_with_latest_image(self):
        from app.services.email_widget_intercept import _latest_image_email_draft

        with patch("app.logic.tools.resolve_chat_image", return_value=("base64-image", "apple")):
            draft = _latest_image_email_draft([{"role": "assistant", "content": "![apple](https://example.test/apple.png)"}])

        self.assertEqual(draft["recipient"], "")
        self.assertEqual(draft["subject"], "Image Attachment")
        self.assertEqual(draft["attachment_content"], "base64-image")
        self.assertEqual(draft["attachment_filename"], "apple.png")

    def test_middleware_is_body_safe_pass_through(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / "app" / "services" / "email_widget_intercept.py").read_text(encoding="utf-8")
        self.assertIn("return await call_next(request)", source)
        self.assertNotIn("await request.body()", source)
        self.assertNotIn("request._receive", source)
        self.assertNotIn("StreamingResponse", source)
        self.assertNotIn("anyio.sleep", source)

    def test_email_widget_shortcut_lives_inside_chat_route(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        chat = (root / "app" / "routes" / "chat.py").read_text(encoding="utf-8")
        self.assertIn("_is_email_widget_attachment_request(prompt)", chat)
        self.assertIn("_latest_image_email_draft(history)", chat)
        self.assertIn("Response(content=_email_widget_ndjson(message), media_type=\"application/x-ndjson\")", chat)
        self.assertIn("except ValueError", chat)
        self.assertIn("User context reset skipped", chat)

    def test_factory_still_imports_compatibility_middleware(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        factory = (root / "app" / "factory.py").read_text(encoding="utf-8")
        self.assertIn("email_widget_chat_middleware", factory)
        self.assertIn('app.middleware("http")(email_widget_chat_middleware)', factory)


if __name__ == "__main__":
    unittest.main()
