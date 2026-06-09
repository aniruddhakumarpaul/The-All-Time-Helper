from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / "codex_hook_state"


def read_event() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")))


def turn_id(event: dict[str, Any]) -> str:
    value = str(event.get("turn_id") or event.get("session_id") or "session")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe[:120] or "session"


def state_path(event: dict[str, Any]) -> Path:
    return STATE_DIR / f"{turn_id(event)}.json"


def load_state(event: dict[str, Any]) -> dict[str, Any]:
    path = state_path(event)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_state(event: dict[str, Any], state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(event).write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def hook_context(event_name: str, additional_context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        }
    }


def is_verification_command(command: str) -> bool:
    normalized = re.sub(r"\s+", " ", command.strip().lower())
    if not normalized:
        return False

    verification_patterns = [
        r"\bpytest\b",
        r"\bpython(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+-m\s+pytest\b",
        r"\bpy\s+-m\s+pytest\b",
        r"\bunittest\b",
        r"\bpython(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+-m\s+unittest\b",
        r"\bpython(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+-m\s+compileall\b",
        r"\bpython(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+-m\s+py_compile\b",
        r"\bnode\s+--check\b",
        r"\bnpm\s+(?:run\s+)?(?:test|lint|build)\b",
        r"\bpnpm\s+(?:run\s+)?(?:test|lint|build)\b",
        r"\byarn\s+(?:test|lint|build)\b",
        r"\buv\s+run\s+pytest\b",
        r"\bruff\s+(?:check|format\s+--check)\b",
        r"\bmypy\b",
        r"\bpyright\b",
    ]
    return any(re.search(pattern, normalized) for pattern in verification_patterns)


def command_text(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("command") or tool_input.get("cmd")
        return value if isinstance(value, str) else ""
    return ""
