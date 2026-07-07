import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import requests
from crewai import Crew, Task

from app.logic.agent_intent import specialist_for_prompt
from app.logic.email_draft_image_workflow import build_email_draft_body_update_payload_from_history
from app.logic.exceptions import AgentFastExit
from app.logic.memory import admin_auth_context
from app.logic.profile_links import resolve_public_profile_link_request


@dataclass(frozen=True)
class LocalRuntime:
    get_agent_swarm: Callable
    extract_crew_result: Callable
    step_callback: Callable
    clean_prompt: Callable
    execute_cloud: Callable
    status_context: Any
    abort_context: Any
    logger: Any
    ollama_url: str


def _conversation_messages(context_data: dict, history: list) -> list[dict]:
    messages = [{
        "role": "system",
        "content": (
            "You are 'The All Time Helper', a high-capability AI assistant. You are technical, proactive, and elite. "
            "Integrate supplied neural or visual context and maintain a sophisticated, helpful tone."
        ),
    }]
    if context_data.get("memory_block"):
        messages.append({"role": "system", "content": context_data["memory_block"]})
    for message in (history or [])[-10:]:
        role = "user" if str(message.get("role")).lower() in {"user", "u", "human"} else "assistant"
        content = str(message.get("content", "")).strip()
        if message.get("masked"):
            content = "[MASKED_SECRET]"
        elif len(content) > 3000:
            content = f"{content[:3000]}..."
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": context_data["final_prompt"]})
    return messages


def execute_local(
    intent,
    context_data,
    target_model,
    sys_config,
    history,
    *,
    runtime: LocalRuntime,
    status_callback=None,
    chunk_callback=None,
    abort_event=None,
):
    if status_callback:
        runtime.status_context.set(status_callback)
    if abort_event:
        runtime.abort_context.set(abort_event)

    prompt_text = context_data.get("final_prompt", "")
    profile_link = resolve_public_profile_link_request(prompt_text)
    if profile_link:
        if chunk_callback:
            chunk_callback(profile_link)
        return profile_link

    email_body_update = build_email_draft_body_update_payload_from_history(prompt_text, history, logger=runtime.logger)
    if email_body_update:
        if status_callback:
            status_callback("✍️ Updating email draft body...")
        if chunk_callback:
            chunk_callback(email_body_update)
        return email_body_update

    prompt_scan = prompt_text.lower()
    if intent["requires_tools"] and any(keyword in prompt_scan for keyword in ("email", "mail", "send")):
        if not admin_auth_context.get():
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."

    try:
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        if intent["requires_tools"]:
            runtime.logger.debug(f"STARTING LOCAL TOOL EXECUTION (Model: {target_model})")
            developer, secretary, artist, manager, generalist = runtime.get_agent_swarm(
                target_model, None, force_no_tools=False, sys_config=sys_config
            )
            specialist_name = specialist_for_prompt(
                runtime.clean_prompt(context_data["final_prompt"]),
                swarm=intent.get("complexity") == "swarm",
            )
            specialist = {
                "developer": developer,
                "secretary": secretary,
                "artist": artist,
                "manager": manager,
                "generalist": generalist,
            }[specialist_name]
            runtime.logger.debug(f"Local Execution path routed to: {specialist_name}")
            task = Task(
                description=(
                    "Action: Execute the user request using the appropriate tool if needed.\n"
                    f"Current Request: {context_data.get('final_prompt', '')}\n\n"
                    f"Conversation History:\n{context_data.get('history_context', '')}\n\n"
                    f"Grounding Memory:\n{context_data.get('memory_block', '')}\n\n"
                    "Preserve supplied technical text and use send_email_tool only to build a draft."
                ),
                expected_output="The output of the tool execution or a final helpful answer.",
                agent=specialist,
            )
            try:
                crew = Crew(agents=[specialist], tasks=[task], step_callback=runtime.step_callback)
                return runtime.extract_crew_result(crew)
            except AgentFastExit as exc:
                return exc.result

        payload = {
            "model": target_model,
            "messages": _conversation_messages(context_data, history),
            "stream": bool(chunk_callback),
        }
        if chunk_callback:
            response = requests.post(
                f"{runtime.ollama_url}/api/chat", json=payload, stream=True, timeout=120, verify=False
            )
            response.raise_for_status()
            full_response = ""
            for line in response.iter_lines():
                if abort_event and abort_event.is_set():
                    return "Operation cancelled."
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise ValueError(f"Ollama streaming error: {chunk['error']}")
                content = chunk.get("message", {}).get("content", "")
                if content:
                    full_response += content
                    chunk_callback(content)
                if chunk.get("done"):
                    break
            return full_response

        response = requests.post(f"{runtime.ollama_url}/api/chat", json=payload, timeout=120, verify=False)
        response.raise_for_status()
        response_json = response.json()
        if "error" in response_json:
            raise ValueError(f"Ollama error: {response_json['error']}")
        return response_json.get("message", {}).get("content", "Error parsing response.")
    except Exception as exc:
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        error_message = str(exc)
        runtime.logger.warning(f"Local Engine Timeout/Error ({error_message}). Attempting Cloud Fallback...")
        clean_error = error_message
        if "model requires more system memory" in error_message:
            match = re.search(r'"message":\s*"([^"]+)"', error_message) or re.search(r"'message':\s*'([^']+)'", error_message)
            if match:
                clean_error = match.group(1)
        warning = (
            f"⚠️ **System Alert**: Local model `{target_model}` failed to load ({clean_error}). "
            "Falling back to Cloud engine...\n\n"
        )
        if chunk_callback:
            chunk_callback(warning)
        fallback_model = "gemma4-cloud" if target_model == "gemma4:e2b" else "agentic-pro"
        try:
            cloud_result = runtime.execute_cloud(
                intent,
                context_data,
                fallback_model,
                sys_config,
                history,
                status_callback=status_callback,
                chunk_callback=chunk_callback,
                abort_event=abort_event,
            )
            return f"{warning}{cloud_result}"
        except Exception as cloud_exc:
            failure = f"Cloud fallback failed: {cloud_exc}"
            if chunk_callback:
                chunk_callback(failure)
            return f"{warning}{failure}"
