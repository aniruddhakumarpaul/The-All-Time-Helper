import time
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.inference_queue import inference_queue
from app.logic.chat_job_registry import chat_job_registry
from app.security import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _visible_jobs_for_owner(owner: str) -> list[dict]:
    now = time.time()
    jobs_by_id: dict[str, dict] = {}
    for job in chat_job_registry.list_for_owner(owner):
        if job.get("status") not in {"active", "cancelling"}:
            continue
        jobs_by_id[job["job_id"]] = {
            "id": job["job_id"],
            "owner": owner,
            "created_at": float(job.get("created_at") or now),
            "elapsed_seconds": max(0, round(now - float(job.get("created_at") or now), 2)),
            "timeout_seconds": 0,
            "lane": "inference",
            "cancelled": bool(job.get("cancel_requested")),
            "status": job.get("status") or "active",
        }

    # Queue metadata is useful locally, but the durable store is authoritative.
    for job_id, job in list(getattr(inference_queue, "_active_jobs", {}).items()):
        if getattr(job, "owner", None) != owner or job_id in jobs_by_id:
            continue
        created_at = float(getattr(job, "created_at", now) or now)
        abort_event = getattr(job, "abort_event", None)
        cancelled = bool(abort_event and abort_event.is_set())
        jobs_by_id[job_id] = {
            "id": job_id,
            "owner": owner,
            "created_at": created_at,
            "elapsed_seconds": max(0, round(now - created_at, 2)),
            "timeout_seconds": float(getattr(job, "timeout", 0) or 0),
            "lane": getattr(job, "lane", "inference"),
            "cancelled": cancelled,
            "status": "cancelling" if cancelled else "active",
        }
    return sorted(jobs_by_id.values(), key=lambda item: item["created_at"], reverse=True)


@router.get("/status")
def job_status(current_user: str = Depends(get_current_user)):
    active_jobs = _visible_jobs_for_owner(current_user)
    return {
        "success": True,
        "queue": {
            "started": bool(getattr(inference_queue, "_started", False)),
            "queue_depth": inference_queue.queue_depth,
            "inference_queue_depth": inference_queue.inference_queue_depth,
            "tool_queue_depth": inference_queue.tool_queue_depth,
            "max_queue_depth": int(getattr(inference_queue, "_max_queue_depth", 0)),
            "max_workers": int(getattr(inference_queue, "_max_workers", 0)),
            "tool_workers": int(getattr(inference_queue, "_max_fast_workers", 0)),
            "user_active_jobs": len(active_jobs),
        },
        "jobs": active_jobs,
    }


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(str(job_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    queue_cancelled = inference_queue.cancel(job_id, current_user)
    store_cancelled = chat_job_registry.cancel(job_id, current_user)
    if not queue_cancelled and not store_cancelled:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}