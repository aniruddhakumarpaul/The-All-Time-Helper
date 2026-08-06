import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routes import chat
from app.logic.chat_job_registry import ChatJobRegistry, InMemoryChatJobStore


class FakeRequest:
    async def is_disconnected(self):
        return False


class FakeQueue:
    def __init__(self):
        self.lanes = []

    async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
        self.lanes.append(lane)
        return fn()

    def cancel(self, job_id, owner):
        return True


async def response_lines(response):
    result = []
    async for chunk in response.body_iterator:
        result.extend(json.loads(line) for line in chunk.decode().splitlines() if line.strip())
    return result


class ChatCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_chat_preserves_ndjson_order_and_completion(self):
        queue = FakeQueue()
        with patch.object(chat, "inference_queue", queue), patch.object(chat, "ask_the_helper", return_value="safe response"):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt="hello", model="gemma2:2b"),
                FakeRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        self.assertIn("job_id", lines[0])
        self.assertEqual(lines[1]["status"], "Starting your request...")
        self.assertEqual(lines[-2], {"message": {"content": "safe response"}, "done": True})
        self.assertEqual(lines[-1], {"done": True})
        self.assertEqual(queue.lanes, ["inference"])

    async def test_visual_attachment_cannot_enter_deterministic_tool_lane(self):
        queue = FakeQueue()
        request = chat.ChatRequest(
            prompt="describe this image",
            model="gemma2:2b",
            attachments=[chat.Attachment(name="photo.png", type="image/png", data="validated-image")],
        )
        with patch.object(chat, "inference_queue", queue), patch.object(chat, "is_deterministic_tool_lane_request", return_value=True), patch.object(chat, "ask_the_helper", return_value="visual response"):
            response = await chat.chat_endpoint(request, FakeRequest(), current_user="owner@example.com")
            await response_lines(response)
        self.assertEqual(queue.lanes, ["inference"])

    async def test_provider_failure_is_sanitized_in_stream(self):
        queue = FakeQueue()
        with patch.object(chat, "inference_queue", queue), patch.object(chat, "ask_the_helper", side_effect=RuntimeError("provider secret and prompt text")):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt="private prompt", model="gemma2:2b"),
                FakeRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        joined = json.dumps(lines)
        self.assertIn("I could not complete that response.", joined)
        self.assertNotIn("provider secret", joined)
        self.assertNotIn("private prompt", joined)

    async def test_document_attachment_is_prepared_as_text_context(self):
        queue = FakeQueue()
        request = chat.ChatRequest(
            prompt="summarize this",
            model="gemma2:2b",
            attachments=[chat.Attachment(name="notes.txt", type="text/plain", data="ignored-client-data")],
        )
        captured = {}

        def fake_helper(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return "document response"

        with patch.object(chat, "inference_queue", queue), patch.object(chat, "extract_attachment_text", return_value="bounded document text"), patch.object(chat, "ask_the_helper", side_effect=fake_helper):
            response = await chat.chat_endpoint(request, FakeRequest(), current_user="owner@example.com")
            await response_lines(response)
        self.assertIn("ATTACHED DOCUMENT: notes.txt", captured["prompt"])
        self.assertIn("bounded document text", captured["prompt"])
        self.assertEqual(queue.lanes, ["inference"])

    async def test_current_attachment_owner_failure_is_a_client_error(self):
        request = chat.ChatRequest(
            prompt="look at this",
            attachments=[chat.Attachment(id="0123456789abcdef0123456789abcdef", name="fake.png")],
        )
        with patch.object(chat, "resolve_attachment_reference", side_effect=chat.AttachmentStoreError("not found")):
            with self.assertRaises(HTTPException) as raised:
                await chat.chat_endpoint(request, FakeRequest(), current_user="other@example.com")
        self.assertEqual(raised.exception.status_code, 400)

    async def test_create_then_events_protocol_is_durable_and_owner_scoped(self):
        registry = ChatJobRegistry(InMemoryChatJobStore())
        queue = FakeQueue()
        with (
            patch.object(chat, "chat_job_registry", registry),
            patch.object(chat, "inference_queue", queue),
            patch.object(chat, "ask_the_helper", return_value="durable response"),
        ):
            response = await chat.create_chat_job(
                chat.ChatRequest(prompt="hello", model="gemma2:2b"),
                FakeRequest(),
                current_user="owner@example.com",
            )
            created = json.loads(response.body)
            self.assertEqual(response.status_code, 202)
            self.assertTrue(created["job_id"])

            stream = await chat.stream_chat_job_events(created["job_id"], current_user="owner@example.com")
            lines = await response_lines(stream)

            with self.assertRaises(HTTPException) as raised:
                await chat.get_chat_job(created["job_id"], current_user="other@example.com")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertTrue(any(item.get("final") for item in lines))
        self.assertEqual(lines[-1]["content"], "durable response")


if __name__ == "__main__":
    unittest.main()
