import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from crewai import Crew, Task

from app.logic.agent_intent import specialist_for_prompt
from app.logic.exceptions import AgentFastExit
from app.logic.memory import admin_auth_context


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
        "You are helpful, technical, and proactive. Integrate supplied neural or visual context naturally. "
        "Never claim you cannot perform actions during standard conversation; provide the best available information. "
        "Maintain a premium, sophisticated tone."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if context_data.get("memory_block"):
        messages.append({"role": "system", "content": f"NEURAL MEMORY (Long-term Context):\n{context_data['memory_block']}"})
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

    prompt_scan = context_data.get("final_prompt", "").lower()
    if intent["requires_tools"] and any(keyword in prompt_scan for keyword in ("email", "mail", "send")):
        if not admin_auth_context.get():
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."

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
                entity_hint = (
                    f"\nRESOLVED ENTITY: If the user says 'send to him/her', use this email: {context_data['resolved_email']}\n"
                    if context_data.get("resolved_email") else ""
                )
                grounding = (
                    f"GROUNDING CONTEXT:\n{context_data['memory_block']}\n{context_data['history_context']}{entity_hint}\n"
                    "Use the corresponding tool for current send, search, draw, or archive requests. Preserve supplied technical text."
                )
                agents = runtime.get_agent_swarm(
                    target_model, current_key, force_no_tools=False, sys_config=sys_config, model_override=active_model
                )
                developer, secretary, artist, manager, generalist = agents
                if intent.get("complexity") == "swarm":
                    task = Task(
                        description=f'Respond to: "{context_data["final_prompt"]}"\n\n{grounding}',
                        expected_output="A final summary of the task result or a direct answer.",
                        agent=manager,
                    )
                    selected = [developer, secretary, artist, manager]
                else:
                    specialist_name = specialist_for_prompt(runtime.clean_prompt(context_data["final_prompt"]))
                    specialist = {
                        "developer": developer,
                        "secretary": secretary,
                        "artist": artist,
                        "manager": manager,
                        "generalist": generalist,
                    }[specialist_name]
                    runtime.logger.debug(f"Cloud Single-Agent Fast Path to specialist: {specialist_name}")
                    task = Task(
                        description=f'Execute: "{context_data["final_prompt"]}"\n\n{grounding}',
                        expected_output="The raw result of the tool call or a direct response.",
                        agent=specialist,
                    )
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
            rate_limited = runtime.is_rate_limit_error(exc)
            if abort_event and abort_event.is_set():
                return "Operation cancelled."
            if rate_limited and is_groq:
                if target_model != "gemma4-cloud" and "llama-3.1-8b" not in str(exc).lower():
                    runtime.logger.warning("Cloud Llama 70B rate limited. Retrying with Llama 3.1 8B Cloud model...")
                    target_model = "gemma4-cloud"
                    cloud_cfg = runtime.get_config(target_model)
                    candidate_models = runtime.candidate_models(cloud_cfg)
                    continue
                if attempt < max_attempts - 1:
                    for _ in range(30):
                        if abort_event and abort_event.is_set():
                            return "Operation cancelled."
                        time.sleep(0.1)
                    continue
            if rate_limited and not is_groq:
                if attempt < max_attempts - 1:
                    runtime.logger.warning(
                        f"Cloud model {active_model} rate limited. Trying fallback {candidate_models[attempt + 1]}..."
                    )
                    continue
                return runtime.rate_limit_message(target_model)
            return f"Cloud Engine Error: {exc}"
