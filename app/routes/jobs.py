import time
import uuid

from fastapi import APIRouter, Depends

from app.inference_queue import inference_queue
from app.security import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _visible_jobs_for_owner(owner: str) -> list[dict]:
    jobs = []
    active_jobs = getattr(inference_queue, "_active_jobs", {})
    now = time.time()
    for job_id, job in list(active_jobs.items()):
        if getattr(job, "owner", None) != owner:
            continue
        created_at = float(getattr(job, "created_at", now) or now)
        timeout = float(getattr(job, "timeout", 0) or 0)
        abort_event = getattr(job, "abort_event", None)
        cancelled = bool(abort_event and abort_event.is_set())
        jobs.append(
            {
                "id": job_id,
                "owner": owner,
                "created_at": created_at,
                "elapsed_seconds": max(0, round(now - created_at, 2)),
                "timeout_seconds": timeout,
                "cancelled": cancelled,
                "status": "cancelling" if cancelled else "active",
            }
        )
    jobs.sort(key=lambda item: item["created_at"], reverse=True)
    return jobs


@router.get("/status")
def job_status(current_user: str = Depends(get_current_user)):
    active_jobs = _visible_jobs_for_owner(current_user)
    return {
        "success": True,
        "queue": {
            "started": bool(getattr(inference_queue, "_started", False)),
            "queue_depth": inference_queue.queue_depth,
            "max_queue_depth": int(getattr(inference_queue, "_max_queue_depth", 0)),
            "max_workers": int(getattr(inference_queue, "_max_workers", 0)),
            "user_active_jobs": len(active_jobs),
        },
        "jobs": active_jobs,
    }


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(str(job_id))
    except ValueError:
        return {"success": False, "error": "Job not found"}
    cancelled = inference_queue.cancel(job_id, current_user)
    return {"success": cancelled, **({} if cancelled else {"error": "Job not found"})}
