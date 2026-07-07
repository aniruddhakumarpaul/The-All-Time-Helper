import json
import unittest

from app.logic.email_draft_image_workflow import (
    build_email_draft_body_update_payload,
    build_email_draft_body_update_payload_from_history,
    build_generated_image_email_draft_payload,
    clean_prompt_without_attached_context,
    extract_email_draft_from_prompt,
    image_description_from_prompt,
    is_email_body_fill_request,
    is_generated_image_email_draft_request,
)
from app.logic.profile_links import resolve_public_profile_link_request


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

    def _body_draft(self):
        return {
            "recipient": "friend@example.com",
            "subject": "annable",
            "body": "",
            "tone": "modern",
            "attachment_content": "https://image.pollinations.ai/prompt/an%20annable%20doll.png",
            "attachment_filename": "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png",
            "attachments": [{
                "content": "https://image.pollinations.ai/prompt/an%20annable%20doll.png",
                "filename": "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png",
                "type": "image/png",
            }],
        }

    def _body_prompt(self):
        return (
            "[Attached Context 1]\n\"\"\"\n"
            f"EMAIL_DRAFT_CONTEXT:{json.dumps(self._body_draft())}\n"
            "\"\"\"\n\n"
            "write something for the body i am lazy"
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

    def test_fills_dragged_email_widget_body_without_losing_details(self):
        self.assertTrue(is_email_body_fill_request(self._body_prompt()))
        result = build_email_draft_body_update_payload(self._body_prompt())
        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "friend@example.com")
        self.assertEqual(payload["subject"], "annable")
        self.assertIn("attached", payload["body"].lower())
        self.assertIn("horror", payload["body"].lower())
        self.assertEqual(payload["attachment_content"], "https://image.pollinations.ai/prompt/an%20annable%20doll.png")
        self.assertEqual(payload["attachments"][0]["filename"], "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png")

    def test_fills_body_from_latest_history_draft_when_current_prompt_has_no_marker(self):
        history = [{"role": "user", "content": f"EMAIL_DRAFT_CONTEXT:{json.dumps(self._body_draft())}"}]
        result = build_email_draft_body_update_payload_from_history(
            "do one thing u make relevent body i am lazy",
            history,
        )
        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "friend@example.com")
        self.assertEqual(payload["subject"], "annable")
        self.assertIn("horror", payload["body"].lower())
        self.assertEqual(payload["attachments"][0]["filename"], "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png")

    def test_body_fill_uses_typed_email_and_subject_when_draft_is_blank(self):
        blank_draft = {
            "recipient": "",
            "subject": "Image Attachment",
            "body": "",
            "tone": "formal",
            "attachment_content": "https://image.pollinations.ai/prompt/an%20annable%20doll.png",
            "attachment_filename": "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png",
            "attachments": [{"content": "https://image.pollinations.ai/prompt/an%20annable%20doll.png", "filename": "an%20annable%20doll%20with%20dim%20asthetic%20and%20realistic%20horror%20effect.png"}],
        }
        prompt = (
            f"EMAIL_DRAFT_CONTEXT:{json.dumps(blank_draft)}\n\n"
            "aniruddha24680kumarpaul@gmail.com\n"
            "image annable\n"
            "do one thing u make relevent body i am lazy"
        )
        result = build_email_draft_body_update_payload_from_history(prompt, [])
        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "aniruddha24680kumarpaul@gmail.com")
        self.assertEqual(payload["subject"], "image annable")
        self.assertIn("horror", payload["body"].lower())

    def test_preflight_hook_returns_body_update_before_llm(self):
        result = resolve_public_profile_link_request(self._body_prompt())
        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertTrue(payload["body"].strip())


if __name__ == "__main__":
    unittest.main()
