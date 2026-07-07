import json
import unittest

from app.logic.email_draft_image_workflow import (
    build_generated_image_email_draft_payload,
    clean_prompt_without_attached_context,
    extract_email_draft_from_prompt,
    image_description_from_prompt,
    is_generated_image_email_draft_request,
)


class GeneratedImageEmailDraftWorkflowTests(unittest.TestCase):
    def _prompt(self):
        draft = {
            "recipient": "",
            "subject": "Image Attachment",
            "body": "",
            "tone": "modern",
        }
        return (
            "[Attached Context 1]\n\"\"\"\n"
            f"EMAIL_DRAFT_CONTEXT:{json.dumps(draft)}\n"
            "\"\"\"\n\n"
            "content will be an image of an annable doll with dim asthetic and realistic horror effect"
        )

    def test_extracts_attached_email_draft_context(self):
        draft = extract_email_draft_from_prompt(self._prompt())
        self.assertEqual(draft["subject"], "Image Attachment")
        self.assertEqual(draft["recipient"], "")

    def test_detects_generated_image_email_widget_request(self):
        self.assertTrue(is_generated_image_email_draft_request(self._prompt()))
        clean = clean_prompt_without_attached_context(self._prompt())
        self.assertNotIn("EMAIL_DRAFT_CONTEXT", clean)
        self.assertIn("annable doll", clean)

    def test_cleans_image_description(self):
        description = image_description_from_prompt(self._prompt()).lower()
        self.assertIn("annable doll", description)
        self.assertNotIn("content will be", description)
        self.assertNotIn("email_draft_context", description)

    def test_generates_image_and_returns_updated_email_draft_payload(self):
        calls = []

        def fake_image_generate(description):
            calls.append(description)
            return f"![{description}](https://image.pollinations.ai/prompt/generated-doll?uid=abc123)"

        result = build_generated_image_email_draft_payload(self._prompt(), fake_image_generate)
        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(len(calls), 1)
        self.assertIn("annable doll", calls[0].lower())
        self.assertEqual(payload["attachment_content"], "https://image.pollinations.ai/prompt/generated-doll?uid=abc123")
        self.assertEqual(payload["attachments"][0]["content"], payload["attachment_content"])
        self.assertTrue(payload["attachment_filename"].endswith("_image.png"))
        self.assertEqual(payload["subject"], "Image Attachment")


if __name__ == "__main__":
    unittest.main()
