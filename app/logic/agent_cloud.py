import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from crewai import Crew, Task

from app.logic import tools
from app.logic.agent_intent import specialist_for_prompt
from app.logic.email_draft_image_workflow import (
    build_email_draft_body_update_payload_from_history,
    build_generated_image_email_draft_payload,
)
from app.logic.exceptions import AgentFastExit
from app.logic.profile_links import resolve_public_profile_link_request


@dataclass(frozen=True)
class CloudRuntime:
    get_api_key: Callable
    get_config: Callable
    candidate_models: Callable
    next_groq_key: Callable
    is_rate_limit_error: Callable
    rate_limit_message: Callable
    get_agent_swarm: Callable
    extract_crew_result: Callable
    step_callback: Callable
    clean_prompt: Callable
    status_context: Any
    abort_context: Any
    logger: Any


def _conversation_messages(context_data: dict, history: list) -> list[dict]:
    system_prompt = (
        "You are 'The All Time Helper', a high-capability AI assistant and professional software architect. "
        "You are helpful, technical, and proactive. Integrate supplied context naturally. "
        "Provide the best available answer. Maintain a premium, sophisticated tone."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if context_data.get("memory_block"):
        messages.append({"role": "system", "content": f"NEURAL MEMORY:\n{context_data['memory_block']}"})
    for message in (history or [])[-30:]:
        role = "user" if str(message.get("role")).lower() in {"user", "u", "human"} else "assistant"
        content = str(message.get("content", "")).strip()
        if message.get("masked"):
            content = "[MASKED_SECRET]"
        elif len(content) > 3000:
            content = f"{content[:3000]}..."
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": context_data["final_prompt"]})
    return messages


def _is_direct_image_generation(prompt: str) -> bool:
    text = str(prompt or "").lower().strip()
    if not text:
        return False
    if any(marker in text for marker in ("search image", "search photo", "find image", "find photo", "real picture", "real photo")):
        return False
    if any(term in text for term in ("draw", "paint", "sketch", "artwork", "painting", "drawing", "portrait", "wallpaper")):
        return True
    if re.search(r"\bcontent\s+will\s+be\s+an?\s+image\s+(of|about|depicting)?\b", text):
        return True
    if re.search(r"\bimage\s+of\s+", text) and any(term in text for term in ("aesthetic", "effect", "realistic", "cinematic", "style", "doll", "car", "scene")):
        return True
    return bool(re.search(r"\b(generate|create|make|render)\s+(?:[a-z0-9]+\s+){0,8}(image|picture|pic|photo|artwork|scene|illustration|apple|car|dog|cat|doll)\b", text))


def _image_description(prompt: str) -> str:
    clean = str(prompt or "").strip()
    clean = re.sub(r"(?i)^\s*(please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?", "", clean)
    clean = re.sub(r"(?i)^\s*content\s+will\s+be\s+an?\s+image\s+(of|about|depicting)?\s*", "", clean)
    clean = re.sub(r"(?i)^\s*(generate|create|make|draw|paint|sketch|render)\s+(me\s+)?(an?\s+)?(image|picture|pic|photo|artwork|illustration)\s*(of\s+)?", "", clean)
    clean = re.sub(r"(?i)^\s*(generate|create|make|draw|paint|sketch|render)\s+(me\s+)?", "", clean)
    clean = re.sub(r"[?.!]+$", "", clean).strip()
    return clean or prompt or "a polished creative image"


def execute_cloud(
    intent,
    context_data,
    target_model,
    sys_config,
    history,
    *,
    runtime: CloudRuntime,
    status_callback=None,
    chunk_callback=None,
    abort_event=None,
):
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

    email_draft_image = build_generated_image_email_draft_payload(
        prompt_text,
        tools.image_generate_tool.func,
        status_callback=status_callback,
        logger=runtime.logger,
    )
    if email_draft_image:
        if chunk_callback:
            chunk_callback(email_draft_image)
        return email_draft_image

    if intent.get("requires_tools") and _is_direct_image_generation(prompt_text):
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        if status_callback:
            status_callback("🎨 Generating creative visual...")
        description = _image_description(prompt_text)
        runtime.logger.info(f"[Cloud Direct Tool] image_generate_tool: '{description}'")
        try:
            result = tools.image_generate_tool.func(description=description)
            if chunk_callback:
                chunk_callback(result)
            return result
        except Exception as exc:
            runtime.logger.error(f"[Cloud Direct Tool] image_generate_tool failed: {exc}", exc_info=True)
            return f"Error generating image: {exc}"

    try:
        target_key = runtime.get_api_key(target_model)
    except ValueError as exc:
        return f"Cloud Engine Error: {exc}"
    cloud_cfg = runtime.get_config(target_model)
    is_groq = cloud_cfg["provider"] == "groq"
    candidate_models = runtime.candidate_models(cloud_cfg)
    if status_callback:
        runtime.status_context.set(status_callback)
    if abort_event:
        runtime.abort_context.set(abort_event)

    max_attempts = 3 if is_groq else len(candidate_models)
    for attempt in range(max_attempts):
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        current_key = target_key
        if is_groq and attempt > 0:
            current_key = runtime.next_groq_key() or os.getenv("GROQ_API_KEY")
        active_model = cloud_cfg["model"] if is_groq else candidate_models[attempt]
        try:
            if intent["requires_tools"]:
                entity_hint = f"\nRESOLVED ENTITY: {context_data['resolved_email']}\n" if context_data.get("resolved_email") else ""
                grounding = (
                    f"GROUNDING CONTEXT:\n{context_data['memory_block']}\n{context_data['history_context']}{entity_hint}\n"
                    "Use the corresponding tool when needed. Preserve supplied technical text."
                )
                agents = runtime.get_agent_swarm(target_model, current_key, force_no_tools=False, sys_config=sys_config, model_override=active_model)
                developer, secretary, artist, manager, generalist = agents
                if intent.get("complexity") == "swarm":
                    task = Task(description=f'Respond to: "{context_data["final_prompt"]}"\n\n{grounding}', expected_output="A final summary or direct answer.", agent=manager)
                    selected = [developer, secretary, artist, manager]
                else:
                    specialist_name = specialist_for_prompt(runtime.clean_prompt(context_data["final_prompt"]))
                    specialist = {"developer": developer, "secretary": secretary, "artist": artist, "manager": manager, "generalist": generalist}[specialist_name]
                    runtime.logger.debug(f"Cloud Single-Agent Fast Path to specialist: {specialist_name}")
                    task = Task(description=f'Execute: "{context_data["final_prompt"]}"\n\n{grounding}', expected_output="The raw tool result or a direct response.", agent=specialist)
                    selected = [specialist]
                try:
                    crew = Crew(agents=selected, tasks=[task], step_callback=runtime.step_callback)
                    return runtime.extract_crew_result(crew)
                except AgentFastExit as exc:
                    return exc.result

            import litellm

            messages = _conversation_messages(context_data, history)
            if chunk_callback:
                response = litellm.completion(model=active_model, messages=messages, api_key=current_key, stream=True)
                full_response = ""
                for chunk in response:
                    content = chunk.choices[0].delta.content
                    if content:
                        full_response += content
                        chunk_callback(content)
                return full_response
            response = litellm.completion(model=active_model, messages=messages, api_key=current_key)
            return response.choices[0].message.content
        except Exception as exc:
            retryable = runtime.is_rate_limit_error(exc)
            if abort_event and abort_event.is_set():
                return "Operation cancelled."
            if retryable and is_groq:
                if attempt < max_attempts - 1:
                    for _ in range(30):
                        if abort_event and abort_event.is_set():
                            return "Operation cancelled."
                        time.sleep(0.1)
                    continue
            if retryable and not is_groq:
                if attempt < max_attempts - 1:
                    runtime.logger.warning(f"Cloud model {active_model} unavailable. Trying fallback {candidate_models[attempt + 1]}...")
                    continue
                return runtime.rate_limit_message(target_model)
            return f"Cloud Engine Error: {exc}"
