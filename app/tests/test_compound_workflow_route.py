import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.contracts.email_draft import draft_marker, normalize_email_draft
from app.routes import chat


OWNER = "owner@example.com"
IMAGE_URL = "https://image.example/generated.png"


def draft(subject="Current draft"):
    return normalize_email_draft({
        "recipient": OWNER,
        "subject": subject,
        "body": "A useful email body.",
        "tone": "formal",
    })


class ImmediateQueue:
    def __init__(self):
        self.lanes = []

    def cancel(self, job_id, owner):
        return True

    async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
        self.lanes.append(lane)
        return fn()


class CompoundWorkflowRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[chat.get_current_user] = lambda: OWNER
        cls.app = app

    async def _post(self, prompt, history=None):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/chat", json={
                "prompt": prompt,
                "history": history or [],
                "model": "helper-auto",
            })

    async def test_valid_context_sources_use_one_workflow_and_one_image_call(self):
        marker = draft_marker(draft())
        cases = {
            "history": ("generate an image about the draft topic and attach it to this email widget", [{"role": "assistant", "content": marker}]),
            "prompt": (f"{marker}\ngenerate an image about the draft topic and attach it to this email widget", []),
            "both": (f"{marker}\ngenerate an image about the draft topic and attach it to this email widget", [{"role": "assistant", "content": marker}]),
            "frontend_active_injection": (f"EMAIL_DRAFT_CONTEXT:{json.dumps({**json.loads(marker.split(':', 1)[1]), 'subject': 'Live edited subject'})}\ngenerate an image about the draft topic and attach it to this email widget", []),
        }
        for name, (prompt, history) in cases.items():
            with self.subTest(source=name):
                queue = ImmediateQueue()
                with (
                    patch.object(chat, "inference_queue", queue),
                    patch.object(chat, "ask_the_helper", side_effect=AssertionError("model fallback must not run")) as ask,
                    patch("app.logic.tools.image_generate_tool.func", return_value=f"![Generated]({IMAGE_URL})") as image_tool,
                ):
                    response = await self._post(prompt, history)
                self.assertEqual(response.status_code, 200)
                self.assertIn("EMAIL_DRAFT_PAYLOAD:", response.text)
                self.assertIn(IMAGE_URL, response.text)
                self.assertEqual(queue.lanes, ["tool"])
                ask.assert_not_called()
                image_tool.assert_called_once()

    async def test_missing_malformed_and_unsupported_contexts_never_generate_standalone_image(self):
        cases = {
            "missing": ("generate an image about the draft topic and attach it to this email widget", []),
            "malformed": ("generate an image about the draft topic and attach it to this email widget", [{"role": "assistant", "content": "EMAIL_DRAFT_PAYLOAD:{broken"}]),
            "unsupported": ("generate an image about the draft topic and attach it to this email widget", [{"role": "assistant", "content": 'EMAIL_DRAFT_PAYLOAD:{"schema_version":99,"recipient":"owner@example.com","subject":"x","body":"y"}'}]),
        }
        for name, (prompt, history) in cases.items():
            with self.subTest(context=name):
                with patch("app.logic.tools.image_generate_tool.func", return_value=f"![Generated]({IMAGE_URL})") as image_tool:
                    response = await self._post(prompt, history)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(IMAGE_URL, response.text)
                self.assertNotIn("EMAIL_DRAFT_PAYLOAD:{", response.text)
                self.assertIn("email draft", response.text.lower())
                image_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
