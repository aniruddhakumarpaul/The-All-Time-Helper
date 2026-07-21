import os
import sys
from pathlib import Path

import certifi
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def report(name: str, ok: bool, detail: str) -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}: {detail}", flush=True)
    return ok


def check_openrouter() -> bool:
    from app.logic.agent_model_registry import get_cloud_api_key

    key = get_cloud_api_key("agentic-pro")
    if not key:
        return report("OpenRouter", False, "No credential is configured.")
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
            verify=certifi.where(),
        )
        return report(
            "OpenRouter",
            response.status_code == 200,
            "Credential accepted." if response.status_code == 200 else f"Credential check returned HTTP {response.status_code}.",
        )
    except requests.RequestException as exc:
        return report("OpenRouter", False, f"Connectivity check failed ({type(exc).__name__}).")


def check_ollama() -> bool:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    try:
        response = requests.get(f"{url}/api/tags", timeout=2)
        if response.status_code != 200:
            return report("Ollama", False, f"Model inventory returned HTTP {response.status_code}.")
        models = {str(item.get("name") or "") for item in response.json().get("models", [])}
        has_gemma_vision = any(name in models for name in ("gemma4:e2b", "gemma4:latest"))
        has_fast_vision = any(name in models for name in ("moondream", "moondream:latest"))
        ready = has_gemma_vision and has_fast_vision
        detail = (
            f"{len(models)} model(s) found; Gemma 4 {'ready' if has_gemma_vision else 'missing'}, "
            f"Moondream {'ready' if has_fast_vision else 'missing'}."
        )
        return report("Ollama", ready, detail)
    except (requests.RequestException, ValueError) as exc:
        return report("Ollama", False, f"Model inventory failed ({type(exc).__name__}).")


def check_email_draft_tool() -> bool:
    from app.logic.exceptions import AgentFastExit
    from app.logic.tools import send_email_tool

    try:
        send_email_tool.func(
            recipient="audit@example.com",
            subject="Pipeline audit",
            body="Draft pipeline check.",
        )
    except AgentFastExit as exc:
        return report("Email draft", str(exc).startswith("EMAIL_DRAFT_PAYLOAD:"), "Deterministic draft payload produced without sending SMTP.")
    except Exception as exc:
        return report("Email draft", False, f"Draft tool failed ({type(exc).__name__}).")
    return report("Email draft", False, "Draft tool returned without the required payload.")


def check_tool_routing() -> bool:
    from app.logic.agents import _resolve_visual_task_continuation
    from app.logic.agent_intent import is_image_generation_request, is_tool_capability_discussion

    history = [
        {"role": "user", "content": "i want to see an arcilic scenery"},
        {"role": "assistant", "content": "Would you prefer a forest or mountains?"},
    ]
    continuation = _resolve_visual_task_continuation("anything you like", history)
    meta_prompt = "check why the image generation tool did not run"
    ready = bool(
        continuation
        and is_image_generation_request(continuation)
        and is_tool_capability_discussion(meta_prompt)
        and not is_image_generation_request(meta_prompt)
    )
    return report("Tool routing", ready, "Contextual action and capability-discussion guards are consistent.")

def check_memory() -> bool:
    from app.logic.memory import memory_runtime_status

    status = memory_runtime_status()
    return report(
        "Neural memory",
        bool(status.get("healthy")),
        "Ready." if status.get("healthy") else f"Temporarily degraded; retry in {status.get('retry_after_seconds', 0)}s.",
    )


def run_audit() -> bool:
    print("--- THE ALL TIME HELPER PIPELINE AUDIT ---", flush=True)
    checks = [check_openrouter(), check_ollama(), check_email_draft_tool(), check_tool_routing(), check_memory()]
    passed = sum(checks)
    print(f"--- {passed}/{len(checks)} checks passed ---", flush=True)
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if run_audit() else 1)