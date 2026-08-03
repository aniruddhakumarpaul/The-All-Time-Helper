import asyncio
import json
import unittest
from unittest.mock import ANY, patch

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
    def __init__(self):
        self.lanes = []

    def cancel(self, job_id, owner):
        return True

    async def submit(self, job_id, fn, abort_event, timeout, owner, lane):
        self.lanes.append(lane)
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

    async def test_known_workflow_has_auto_cloud_and_local_route_parity(self):
        for model in ("helper-auto", "agentic-pro", "gemma4:e2b"):
            with self.subTest(model=model):
                queue = ImmediateQueue()
                pending_plan = object()
                with (
                    patch.object(chat, "plan_known_workflow", return_value=pending_plan),
                    patch.object(chat, "execute_workflow_for_chat", return_value="EMAIL_DRAFT_PAYLOAD:{}") as execute,
                    patch.object(chat, "ask_the_helper", side_effect=AssertionError("known workflows must bypass model routing")),
                    patch.object(chat, "inference_queue", queue),
                ):
                    response = await chat.chat_endpoint(
                        chat.ChatRequest(prompt="attach a reference image to this draft", model=model),
                        ImmediateRequest(),
                        current_user="owner@example.com",
                    )
                    lines = await response_lines(response)
                self.assertEqual(queue.lanes, ["tool"])
                self.assertIn("EMAIL_DRAFT_PAYLOAD", json.dumps(lines))
                execute.assert_called_once_with(
                    pending_plan,
                    admin_key=None,
                    abort_event=ANY,
                    status_callback=ANY,
                )

    async def test_masked_key_is_sent_only_to_pending_workflow_executor(self):
        candidate = "wrong-secret-admin-key"
        queue = ImmediateQueue()
        pending_plan = object()
        with (
            patch.object(chat, "plan_known_workflow", return_value=pending_plan),
            patch.object(chat, "execute_workflow_for_chat", return_value="ERROR: AUTH_REQUIRED. Incorrect Admin Key.") as execute,
            patch.object(chat, "ask_the_helper", side_effect=AssertionError("masked keys must not reach a model")),
            patch.object(chat, "inference_queue", queue),
        ):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt=candidate, model="helper-auto", isMasked=True),
                ImmediateRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        joined = json.dumps(lines)
        self.assertIn("AUTH_REQUIRED", joined)
        self.assertNotIn(candidate, joined)
        self.assertEqual(execute.call_args.kwargs["admin_key"], candidate)
        self.assertIsNone(admin_auth_context.get())

    async def test_masked_input_without_pending_workflow_is_controlled(self):
        candidate = "admin-key-value"
        with (
            patch.object(chat, "plan_known_workflow", return_value=None),
            patch.object(chat, "ask_the_helper", side_effect=AssertionError("masked keys must not reach a model")),
        ):
            response = await chat.chat_endpoint(
                chat.ChatRequest(prompt=candidate, model="helper-auto", isMasked=True),
                ImmediateRequest(),
                current_user="owner@example.com",
            )
            lines = await response_lines(response)
        joined = json.dumps(lines)
        self.assertIn("No pending email delivery", joined)
        self.assertNotIn(candidate, joined)
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
