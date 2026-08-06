"""Server-owned lifecycle and event storage for refresh-safe chat jobs."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


MAX_JOB_CONTENT_CHARS = 120_000
MAX_JOB_EVENTS = 2_000
MAX_RETAINED_JOBS = 500
JOB_RETENTION_SECONDS = 60 * 60


@dataclass
class ChatJob:
    job_id: str
    owner: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "active"
    content: str = ""
    next_sequence: int = 0
    events: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    cancel_event: Any = None


class ChatJobRegistry:
    """Keep execution results independent from any one HTTP stream or browser tab."""

    def __init__(self) -> None:
        self._jobs: dict[str, ChatJob] = {}
        self._lock = threading.RLock()

    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob:
        with self._lock:
            self._prune_locked()
            job = ChatJob(job_id=job_id, owner=owner, cancel_event=cancel_event)
            self._jobs[job_id] = job
            return job

    def publish(self, job_id: str, owner: str, event: dict[str, Any]) -> bool:
        with self._lock:
            job = self._owned_locked(job_id, owner)
            if not job or job.status != "active":
                return False
            clean_event = dict(event)
            if "message" in clean_event and isinstance(clean_event["message"], dict):
                message = dict(clean_event["message"])
                message["content"] = str(message.get("content") or "")[:MAX_JOB_CONTENT_CHARS]
                clean_event["message"] = message
            job.next_sequence += 1
            job.events.append((job.next_sequence, clean_event))
            if len(job.events) > MAX_JOB_EVENTS:
                job.events = job.events[-MAX_JOB_EVENTS:]
            job.updated_at = time.time()
            return True

    def cancel(self, job_id: str, owner: str) -> bool:
        """Signal explicit cancellation without making the job terminal prematurely."""
        with self._lock:
            job = self._owned_locked(job_id, owner)
            if not job or job.status != "active":
                return False
            if job.cancel_event is not None:
                job.cancel_event.set()
            job.updated_at = time.time()
            return True

    def complete(
        self,
        job_id: str,
        owner: str,
        content: str,
        *,
        streamed: bool = False,
        cancelled: bool = False,
    ) -> bool:
        with self._lock:
            job = self._owned_locked(job_id, owner)
            if not job or job.status != "active":
                return False
            job.content = str(content or "")[:MAX_JOB_CONTENT_CHARS]
            job.status = "cancelled" if cancelled else "completed"
            job.updated_at = time.time()
            if job.content and not streamed:
                self._append_locked(job, {"message": {"content": job.content}, "done": True})
            self._append_locked(job, {"done": True})
            return True

    def fail(self, job_id: str, owner: str, safe_message: str) -> bool:
        with self._lock:
            job = self._owned_locked(job_id, owner)
            if not job or job.status != "active":
                return False
            job.content = str(safe_message or "The assistant could not complete this request.")[:MAX_JOB_CONTENT_CHARS]
            job.status = "failed"
            job.updated_at = time.time()
            self._append_locked(job, {"message": {"content": job.content}, "done": True})
            self._append_locked(job, {"done": True})
            return True

    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None:
        with self._lock:
            job = self._owned_locked(job_id, owner)
            if not job:
                return None
            events = [
                {"seq": sequence, "event": dict(event)}
                for sequence, event in job.events
                if sequence > max(0, int(after))
            ]
            return {
                "id": job.job_id,
                "status": job.status,
                "content": job.content,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "events": events,
                "next_seq": job.next_sequence,
            }

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            jobs = []
            for job in self._jobs.values():
                if job.owner != owner:
                    continue
                jobs.append({
                    "id": job.job_id,
                    "status": job.status,
                    "content": job.content,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                })
            jobs.sort(key=lambda item: item["created_at"], reverse=True)
            return jobs

    def _owned_locked(self, job_id: str, owner: str) -> ChatJob | None:
        job = self._jobs.get(job_id)
        if not job or not owner or job.owner != owner:
            return None
        return job

    @staticmethod
    def _append_locked(job: ChatJob, event: dict[str, Any]) -> None:
        job.next_sequence += 1
        job.events.append((job.next_sequence, event))
        if len(job.events) > MAX_JOB_EVENTS:
            job.events = job.events[-MAX_JOB_EVENTS:]

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if now - job.updated_at > JOB_RETENTION_SECONDS
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        ordered = sorted(self._jobs.values(), key=lambda job: job.updated_at)
        for job in ordered[: len(self._jobs) - MAX_RETAINED_JOBS]:
            self._jobs.pop(job.job_id, None)


chat_job_registry = ChatJobRegistry()