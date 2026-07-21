import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import requests
from fastapi import APIRouter, Depends

from app.database import DB_FILE, get_db
from app.inference_queue import inference_queue
from app.logic.agent_model_registry import CLOUD_MODEL_CONFIG, cloud_runtime_status
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
            return {"running": False, "model_count": 0}
        models = [item.get("name") for item in response.json().get("models", []) if item.get("name")]
        return {"running": True, "model_count": len(models)}
    except Exception:
        return {"running": False, "model_count": 0}


def _memory_status() -> dict[str, Any]:
    try:
        from app.logic.memory import memory_runtime_status

        return memory_runtime_status()
    except Exception:
        return {"healthy": False, "degraded": True, "retry_after_seconds": 0}

def _queue_status(owner: str) -> dict[str, Any]:
    active_jobs = getattr(inference_queue, "_active_jobs", {})
    user_active_jobs = sum(1 for job in active_jobs.values() if getattr(job, "owner", None) == owner)
    return {
        "started": bool(getattr(inference_queue, "_started", False)),
        "queue_depth": inference_queue.queue_depth,
        "inference_queue_depth": inference_queue.inference_queue_depth,
        "tool_queue_depth": inference_queue.tool_queue_depth,
        "max_queue_depth": int(getattr(inference_queue, "_max_queue_depth", 0)),
        "max_workers": int(getattr(inference_queue, "_max_workers", 0)),
        "tool_workers": int(getattr(inference_queue, "_max_fast_workers", 0)),
        "user_active_jobs": user_active_jobs,
    }


def _public_link_active() -> tuple[bool, bool]:
    enabled = str(os.getenv("ENABLE_NGROK", "")).lower() in {"1", "true", "yes", "on"}
    public_url = str(os.getenv("NGROK_PUBLIC_URL") or "").strip()
    runtime_url = BASE_DIR / ".runtime" / "ngrok_url.txt"
    if not public_url and runtime_url.is_file():
        try:
            public_url = runtime_url.read_text(encoding="utf-8").strip()
        except OSError:
            public_url = ""
    return enabled, bool(public_url)


@router.get("/status")
async def admin_status(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    cloud = cloud_runtime_status()
    cloud_ready = cloud["available"]
    cloud_reason = cloud.get("reason")
    cloud_summary = (
        "Ready"
        if cloud_ready
        else "Cloud provider needs configuration"
        if not cloud["configured"]
        else {
            "rate_limited": "Provider rate limit reached; Auto is using local models",
            "network_unavailable": "Cloud network unavailable; Auto is using local models",
            "authentication_failed": "Cloud credentials were rejected; Auto is using local models",
            "timed_out": "Cloud provider timed out; Auto is using local models",
            "provider_unavailable": "Cloud provider unavailable; Auto is using local models",
        }.get(cloud_reason, "Temporarily unavailable; Auto is using local models")
    )
    ollama = await _ollama_status()
    memory = _memory_status()
    queue = _queue_status(current_user)
    public_link_enabled, public_link_active = _public_link_active()
    db_available = Path(DB_FILE).exists()
    chat_count = _count_user_chats(db, current_user)

    email_mode = str(os.getenv("EMAIL_MODE", "SIMULATE")).strip().upper()
    smtp_ready = _has_real_env("SENDER_EMAIL") and _has_real_env("SENDER_PWD")
    email_ready = email_mode != "LIVE" or smtp_ready

    components = [
        _component(
            "Cloud assistant",
            "ok" if cloud_ready else "warn",
            cloud_summary,
            {
                "configured": cloud["configured"],
                "available": cloud_ready,
                "retry_after_seconds": cloud["retry_after_seconds"],
                "routes_configured": len(CLOUD_MODEL_CONFIG),
                "state": "ready" if cloud_ready else cloud_reason,
                "max_output_tokens": cloud_output_token_budget(),
            },
        ),
        _component(
            "Local assistant",
            "ok" if ollama["running"] else "off",
            f"{ollama['model_count']} local model(s) available" if ollama["running"] else "Optional local service is offline",
            {
                "available": ollama["running"],
                "model_count": ollama["model_count"],
            },
        ),
        _component(
            "Public link",
            "ok" if public_link_active else "warn" if public_link_enabled else "off",
            "Active" if public_link_active else "Enabled but unavailable" if public_link_enabled else "Disabled",
            {
                "enabled": public_link_enabled,
                "active": public_link_active,
            },
        ),
        _component(
            "Conversations",
            "ok" if db_available else "fail",
            f"{chat_count} saved conversation(s)",
            {
                "available": db_available,
                "current_user_chats": chat_count,
            },
        ),
        _component(
            "Active tasks",
            "ok" if queue["queue_depth"] < queue["max_queue_depth"] else "warn",
            f"{queue['user_active_jobs']} active for your account",
            {
                "service_started": queue["started"],
                "waiting": queue["queue_depth"],
                "capacity": queue["max_workers"],
            },
        ),
        _component(
            "Memory",
            "ok" if memory["healthy"] else "warn",
            "Ready" if memory["healthy"] else "Retrying after a transient storage error",
            {
                "available": memory["healthy"],
                "retry_after_seconds": int(memory.get("retry_after_seconds", 0)),
            },
        ),
        _component(
            "Email delivery",
            "ok" if email_ready else "warn",
            "Live delivery ready" if email_mode == "LIVE" and smtp_ready else "Safe simulation mode" if email_mode != "LIVE" else "Live delivery needs configuration",
            {
                "mode": "live" if email_mode == "LIVE" else "simulate",
                "configured": smtp_ready,
            },
        ),
    ]

    core_available = db_available and (cloud_ready or ollama["running"])
    if not core_available:
        overall = "fail"
    elif any(item["status"] == "warn" for item in components):
        overall = "warn"
    else:
        overall = "ok"

    return {
        "success": True,
        "overall": overall,
        "user": current_user,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
    }
