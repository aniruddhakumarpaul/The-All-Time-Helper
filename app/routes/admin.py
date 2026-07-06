import os
import sqlite3
from pathlib import Path
from typing import Any

import anyio
import requests
from fastapi import APIRouter, Depends

from app.database import DB_FILE, get_db
from app.inference_queue import inference_queue
from app.logic.agent_model_registry import CLOUD_MODEL_CONFIG, OPENROUTER_KEY_ENVS
from app.logic.cloud_token_budget import cloud_output_token_budget
from app.security import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])

BASE_DIR = Path(__file__).resolve().parents[2]


def _has_real_env(name: str) -> bool:
    value = str(os.getenv(name) or "").strip().strip('"').strip("'")
    lowered = value.lower()
    return bool(value) and not lowered.startswith("your-") and "placeholder" not in lowered and "optional-" not in lowered


def _component(name: str, status: str, summary: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "details": details or {},
    }


def _frontend_versions() -> dict[str, str]:
    template = BASE_DIR / "templates" / "index.html"
    versions: dict[str, str] = {}
    try:
        text = template.read_text(encoding="utf-8")
    except OSError:
        return versions
    for asset in ("app.js", "particles.js", "palette.js", "utils.js", "email_draft.js"):
        marker = f"/static/js/{asset}?v="
        if marker in text:
            versions[asset] = text.split(marker, 1)[1].split('"', 1)[0]
    return versions


def _count_user_chats(db: sqlite3.Connection, current_user: str) -> int:
    try:
        row = db.execute("SELECT COUNT(*) AS count FROM chats WHERE user_email = ?", (current_user,)).fetchone()
        return int(row["count"] if row else 0)
    except Exception:
        return 0


async def _ollama_status() -> dict[str, Any]:
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    try:
        response = await anyio.to_thread.run_sync(lambda: requests.get(f"{ollama_url}/api/tags", timeout=0.75))
        if response.status_code != 200:
            return {"running": False, "url": ollama_url, "error": f"HTTP {response.status_code}"}
        models = [item.get("name") for item in response.json().get("models", []) if item.get("name")]
        return {"running": True, "url": ollama_url, "models": models[:20], "model_count": len(models)}
    except Exception as exc:
        return {"running": False, "url": ollama_url, "error": str(exc)}


def _memory_status() -> dict[str, Any]:
    try:
        from app.logic.memory import collection

        return {"healthy": True, "count": collection.count()}
    except Exception as exc:
        return {"healthy": False, "error": str(exc)}


def _queue_status() -> dict[str, Any]:
    return {
        "started": bool(getattr(inference_queue, "_started", False)),
        "queue_depth": inference_queue.queue_depth,
        "max_queue_depth": int(getattr(inference_queue, "_max_queue_depth", 0)),
        "max_workers": int(getattr(inference_queue, "_max_workers", 0)),
        "active_jobs": len(getattr(inference_queue, "_active_jobs", {})),
    }


@router.get("/status")
async def admin_status(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    openrouter_ready = any(_has_real_env(name) for name in OPENROUTER_KEY_ENVS)
    smtp_ready = _has_real_env("SENDER_EMAIL") and _has_real_env("SENDER_PWD")
    admin_key_ready = _has_real_env("ADMIN_KEY")
    ngrok_enabled = str(os.getenv("ENABLE_NGROK", "")).lower() in {"1", "true", "yes", "on"}
    root_ngrok_url = BASE_DIR / "ngrok_url.txt"
    runtime_ngrok_url = BASE_DIR / ".runtime" / "ngrok_url.txt"
    public_url = str(os.getenv("NGROK_PUBLIC_URL") or "").strip()
    if not public_url and runtime_ngrok_url.is_file():
        try:
            public_url = runtime_ngrok_url.read_text(encoding="utf-8").strip()
        except OSError:
            public_url = ""

    ollama = await _ollama_status()
    memory = _memory_status()
    queue = _queue_status()
    frontend = _frontend_versions()
    db_path = Path(DB_FILE)
    chat_count = _count_user_chats(db, current_user)

    components = [
        _component(
            "OpenRouter",
            "ok" if openrouter_ready else "warn",
            "Configured" if openrouter_ready else "Missing API key",
            {
                "env_names": list(OPENROUTER_KEY_ENVS),
                "token_budget": cloud_output_token_budget(),
                "configured_models": sorted(CLOUD_MODEL_CONFIG.keys()),
            },
        ),
        _component(
            "Ollama",
            "ok" if ollama.get("running") else "warn",
            f"{ollama.get('model_count', 0)} local model(s) available" if ollama.get("running") else "Local daemon unavailable",
            ollama,
        ),
        _component(
            "Ngrok",
            "ok" if ngrok_enabled and public_url else "warn" if ngrok_enabled else "off",
            "Tunnel active" if public_url else "Enabled but no public URL" if ngrok_enabled else "Disabled",
            {
                "enabled": ngrok_enabled,
                "public_url": public_url,
                "root_ngrok_url_file_present": root_ngrok_url.is_file(),
            },
        ),
        _component(
            "Database",
            "ok" if db_path.exists() else "warn",
            f"{chat_count} chat(s) for current user",
            {"path": str(db_path), "exists": db_path.exists(), "current_user_chats": chat_count},
        ),
        _component(
            "Inference Queue",
            "ok" if queue["queue_depth"] < queue["max_queue_depth"] else "warn",
            f"{queue['active_jobs']} active / {queue['queue_depth']} queued",
            queue,
        ),
        _component(
            "Memory",
            "ok" if memory.get("healthy") else "warn",
            f"{memory.get('count', 0)} memory item(s)" if memory.get("healthy") else "Memory unavailable",
            memory,
        ),
        _component(
            "Email",
            "ok" if smtp_ready else "warn",
            "SMTP configured" if smtp_ready else "SMTP incomplete",
            {"mode": os.getenv("EMAIL_MODE", "SIMULATE"), "sender_configured": _has_real_env("SENDER_EMAIL")},
        ),
        _component(
            "Security",
            "ok" if admin_key_ready else "warn",
            "Admin key configured" if admin_key_ready else "Admin key missing",
            {"admin_key_configured": admin_key_ready},
        ),
        _component(
            "Frontend Assets",
            "ok" if frontend else "warn",
            f"app.js v{frontend.get('app.js', 'unknown')}",
            {"versions": frontend},
        ),
    ]

    overall = "ok"
    if any(item["status"] == "fail" for item in components):
        overall = "fail"
    elif any(item["status"] == "warn" for item in components):
        overall = "warn"

    return {
        "success": True,
        "overall": overall,
        "user": current_user,
        "generated_at": anyio.current_time(),
        "components": components,
    }
