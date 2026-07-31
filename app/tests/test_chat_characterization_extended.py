import asyncio
import json
import unittest
from unittest.mock import patch

from app.logic.memory import admin_auth_context
from app.routes import chat


class ImmediateRequest:
    async def is_disconnected(self):
        return False


class DisconnectingRequest:
    async def is_disconnected(self):
        return True


class BlockingQueue:
    def __init__(self):
        self.lanes = []

    async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
        self.lanes.append(lane)
        while not abort_event.is_set():
            await asyncio.sleep(0.01)
        return "cancelled worker"

    def cancel(self, job_id, owner):
        return True


class ImmediateQueue:
    def cancel(self, job_id, owner):
        return True

    async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
        return fn()

async def response_lines(response):
    lines = []
    async for chunk in response.body_iterator:
        lines.extend(json.loads(line) for line in chunk.decode().splitlines() if line.strip())
    return lines


class ExtendedChatCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_request_uses_tool_lane(self):
        queue = type("Queue", (), {"lanes": [], "cancel": lambda self, job_id, owner: True})()

        async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
            self.lanes.append(lane)
            return fn()

        queue.submit = submit.__get__(queue)
        with patch.object(chat, "inference_queue", queue), patch.object(chat, "is_deterministic_tool_lane_request", return_value=True), patch.object(chat, "ask_the_helper", return_value="tool response"):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt="search the web for the latest status", model="helper-auto"),
                ImmediateRequest(),
                current_user="owner@example.com",
            )
            await response_lines(response)
        self.assertEqual(queue.lanes, ["tool"])

    async def test_invalid_admin_key_is_streamed_without_echoing_candidate(self):
        candidate = "wrong-secret-admin-key"
        with patch.object(chat, "verify_admin_key", return_value=False):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt=candidate, model="helper-auto", isMasked=True),
                ImmediateRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        joined = json.dumps(lines)
        self.assertIn("AUTH_REQUIRED", joined)
        self.assertNotIn(candidate, joined)
        self.assertIsNone(admin_auth_context.get())

    async def test_valid_admin_key_is_request_scoped(self):
        queue = ImmediateQueue()
        with patch.object(chat, "verify_admin_key", return_value=True), patch.object(chat, "ask_the_helper", return_value="approved"), patch.object(chat, "inference_queue", queue):

            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt="admin-key-value", model="helper-auto", isMasked=True, history=[{"r": "u", "c": "Please perform the sensitive operation now."}]),
                ImmediateRequest(),
                current_user="owner@example.com",
            )
            self.assertIsNone(admin_auth_context.get())
            lines = await response_lines(response)
        self.assertEqual(lines[-1], {"done": True})

    async def test_client_disconnect_emits_cancellation_and_preserves_stream_shape(self):
        queue = BlockingQueue()
        with patch.object(chat, "inference_queue", queue), patch.object(chat, "ask_the_helper", return_value="unreachable"):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt="keep working", model="helper-auto"),
                DisconnectingRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        self.assertEqual(queue.lanes, ["inference"])
        self.assertTrue(any(item.get("message", {}).get("content") == "Request cancelled." for item in lines))
        self.assertEqual(lines[-1], {"done": True})


if __name__ == "__main__":
    unittest.main()
