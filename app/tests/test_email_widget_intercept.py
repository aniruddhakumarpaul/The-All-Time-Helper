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

    def test_intercept_uses_plain_ndjson_response_not_streaming_response(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / "app" / "services" / "email_widget_intercept.py").read_text(encoding="utf-8")
        self.assertIn("from fastapi.responses import Response", source)
        self.assertIn("_email_widget_ndjson", source)
        self.assertIn("Response(content=_email_widget_ndjson(message), media_type=\"application/x-ndjson\")", source)
        self.assertNotIn("StreamingResponse", source)

    def test_request_body_replay_is_one_shot(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / "app" / "services" / "email_widget_intercept.py").read_text(encoding="utf-8")
        self.assertIn("sent = False", source)
        self.assertIn("if not sent:", source)
        self.assertIn("sent = True", source)
        self.assertIn("await anyio.sleep(86400)", source)

    def test_factory_installs_intercept_middleware(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        factory = (root / "app" / "factory.py").read_text(encoding="utf-8")
        self.assertIn("email_widget_chat_middleware", factory)
        self.assertIn('app.middleware("http")(email_widget_chat_middleware)', factory)


if __name__ == "__main__":
    unittest.main()
