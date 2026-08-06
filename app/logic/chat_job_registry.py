"""Durable, owner-scoped lifecycle and event storage for chat jobs."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ACTIVE = "active"
CANCELLING = "cancelling"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED, "expired"})
MAX_JOB_CONTENT_CHARS = int(os.getenv("CHAT_JOB_MAX_CONTENT_CHARS", "120000"))
MAX_JOB_EVENTS = int(os.getenv("CHAT_JOB_MAX_EVENTS", "2000"))
MAX_RETAINED_JOBS = int(os.getenv("CHAT_JOB_MAX_RETAINED_JOBS", "500"))
MAX_EVENT_BYTES = int(os.getenv("CHAT_JOB_MAX_EVENT_BYTES", "32768"))
JOB_RETENTION_SECONDS = max(60, int(os.getenv("CHAT_JOB_RETENTION_SECONDS", "3600")))
JOB_CANCEL_POLL_SECONDS = max(0.1, float(os.getenv("CHAT_JOB_CANCEL_POLL_SECONDS", "0.4")))


def _truncate(value: Any, limit: int) -> str:
    raw = str(value or "").encode("utf-8", "replace")
    return raw.decode("utf-8", "ignore") if len(raw) <= limit else raw[: max(0, limit - 3)].decode("utf-8", "ignore") + "..."


def _event_size(event: dict[str, Any]) -> int:
    return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace"))


def _bound_event(event: dict[str, Any], limit: int = MAX_EVENT_BYTES) -> dict[str, Any]:
    clean = dict(event)
    if isinstance(clean.get("message"), dict):
        clean["message"] = dict(clean["message"])
        clean["message"]["content"] = _truncate(clean["message"].get("content"), limit)
    if "content" in clean:
        clean["content"] = _truncate(clean["content"], limit)
    if _event_size(clean) <= limit:
        return clean
    message = clean.get("message") if isinstance(clean.get("message"), dict) else {}
    compact = {
        "status": str(clean.get("status") or "Working")[:256],
        "final": bool(clean.get("final")),
        "done": bool(clean.get("done")),
        "content": _truncate(message.get("content") or clean.get("content"), max(128, limit - 512)),
    }
    while _event_size(compact) > limit and compact["message"]["content"]:
        compact["message"]["content"] = compact["message"]["content"][:-128]
    return compact


@dataclass
class ChatJob:
    job_id: str
    owner: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = ACTIVE
    content: str = ""
    next_sequence: int = 0
    cancel_requested: bool = False
    events: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    cancel_event: Any = None


class ChatJobStore(Protocol):
    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob: ...
    def publish(self, job_id: str, owner: str, event: dict[str, Any]) -> bool: ...
    def request_cancel(self, job_id: str, owner: str) -> bool: ...
    def is_cancel_requested(self, job_id: str, owner: str) -> bool: ...
    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False) -> bool: ...
    def fail(self, job_id: str, owner: str, safe_message: str) -> bool: ...
    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None: ...
    def list_for_owner(self, owner: str) -> list[dict[str, Any]]: ...
    def prune(self) -> int: ...


class SQLiteChatJobStore:
    """SQLite WAL persistence shared by local workers and process restarts."""

    def __init__(self, db_file: str | Path | None = None, *, retention_seconds: int = JOB_RETENTION_SECONDS,
                 max_event_bytes: int = MAX_EVENT_BYTES, max_content_chars: int = MAX_JOB_CONTENT_CHARS,
                 max_events: int = MAX_JOB_EVENTS, max_retained_jobs: int = MAX_RETAINED_JOBS,
                 max_storage_bytes: int = 64 * 1024 * 1024) -> None:
        self.db_file = Path(db_file) if db_file else Path(__file__).resolve().parents[2] / ".runtime" / "chat_jobs.db"
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = max(1, int(retention_seconds))
        self.max_event_bytes = max(512, int(max_event_bytes))
        self.max_content_chars = max(256, int(max_content_chars))
        self.max_events = max(1, int(max_events))
        self.max_retained_jobs = max(1, int(max_retained_jobs))
        self.max_storage_bytes = max(1024, int(max_storage_bytes))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_file, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _open(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self._open() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_jobs(
              job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at REAL NOT NULL,
              updated_at REAL NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL,
              content TEXT NOT NULL DEFAULT '', next_seq INTEGER NOT NULL DEFAULT 0,
              cancel_requested INTEGER NOT NULL DEFAULT 0, event_bytes INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS chat_jobs_owner_updated ON chat_jobs(owner, updated_at DESC);
            CREATE INDEX IF NOT EXISTS chat_jobs_expiry ON chat_jobs(expires_at);
            CREATE TABLE IF NOT EXISTS chat_job_events(
              job_id TEXT NOT NULL REFERENCES chat_jobs(job_id) ON DELETE CASCADE,
              seq INTEGER NOT NULL, payload TEXT NOT NULL, payload_bytes INTEGER NOT NULL,
              created_at REAL NOT NULL, PRIMARY KEY(job_id, seq));
            CREATE INDEX IF NOT EXISTS chat_events_cursor ON chat_job_events(job_id, seq);
            """)

    def _prune_locked(self, db: sqlite3.Connection, now: float | None = None) -> int:
        now = time.time() if now is None else now
        expired = db.execute("SELECT job_id FROM chat_jobs WHERE expires_at <= ?", (now,)).fetchall()
        db.execute("DELETE FROM chat_jobs WHERE expires_at <= ?", (now,))
        old = db.execute("SELECT job_id FROM chat_jobs WHERE status IN (?,?,?) "
                         "ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
                         (COMPLETED, FAILED, CANCELLED, self.max_retained_jobs)).fetchall()
        db.executemany("DELETE FROM chat_jobs WHERE job_id=?", [(row["job_id"],) for row in old])
        usage = db.execute("SELECT COALESCE(SUM(event_bytes + length(content)), 0) AS bytes FROM chat_jobs").fetchone()["bytes"]
        if int(usage) > self.max_storage_bytes:
            for row in db.execute("SELECT job_id FROM chat_jobs WHERE status IN (?,?,?) ORDER BY updated_at ASC",
                                  (COMPLETED, FAILED, CANCELLED)).fetchall():
                db.execute("DELETE FROM chat_jobs WHERE job_id=?", (row["job_id"],))
                usage = db.execute("SELECT COALESCE(SUM(event_bytes + length(content)), 0) AS bytes FROM chat_jobs").fetchone()["bytes"]
                if int(usage) <= self.max_storage_bytes:
                    break
        return len(expired) + len(old)

    def _row(self, db: sqlite3.Connection, job_id: str, owner: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM chat_jobs WHERE job_id=? AND owner=?", (job_id, owner)).fetchone()

    def _trim(self, db: sqlite3.Connection, job_id: str) -> None:
        count = db.execute("SELECT COUNT(*) AS count FROM chat_job_events WHERE job_id=?", (job_id,)).fetchone()["count"]
        overflow = max(0, int(count) - self.max_events)
        if overflow:
            db.execute("DELETE FROM chat_job_events WHERE job_id=? AND seq IN "
                       "(SELECT seq FROM chat_job_events WHERE job_id=? ORDER BY seq LIMIT ?)",
                       (job_id, job_id, overflow))
        db.execute("UPDATE chat_jobs SET event_bytes=COALESCE("
                   "(SELECT SUM(payload_bytes) FROM chat_job_events WHERE job_id=?),0) WHERE job_id=?", (job_id, job_id))

    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob:
        now = time.time()
        expires = now + self.retention_seconds
        with self._open() as db:
            self._prune_locked(db, now)
            db.execute("INSERT INTO chat_jobs(job_id,owner,created_at,updated_at,expires_at,status) VALUES(?,?,?,?,?,?)",
                       (job_id, owner, now, now, expires, ACTIVE))
        return ChatJob(job_id, owner, now, now, expires, cancel_event=cancel_event)

    def publish(self, job_id: str, owner: str, event: dict[str, Any]) -> bool:
        now = time.time()
        with self._open() as db:
            row = self._row(db, job_id, owner)
            if not row or row["status"] != ACTIVE:
                return False
            bounded = _bound_event(event, self.max_event_bytes)
            payload = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
            size = len(payload.encode("utf-8", "replace"))
            seq = int(row["next_seq"]) + 1
            db.execute("INSERT INTO chat_job_events VALUES(?,?,?,?,?)", (job_id, seq, payload, size, now))
            db.execute("UPDATE chat_jobs SET next_seq=?,updated_at=?,event_bytes=event_bytes+? "
                       "WHERE job_id=? AND owner=? AND status=?", (seq, now, size, job_id, owner, ACTIVE))
            self._trim(db, job_id)
        return True

    def request_cancel(self, job_id: str, owner: str) -> bool:
        now = time.time()
        with self._open() as db:
            self._prune_locked(db, now)
            row = self._row(db, job_id, owner)
            if not row or row["status"] not in (ACTIVE, CANCELLING):
                return False
            db.execute("UPDATE chat_jobs SET status=?,cancel_requested=1,updated_at=? "
                       "WHERE job_id=? AND owner=? AND status IN (?,?)",
                       (CANCELLING, now, job_id, owner, ACTIVE, CANCELLING))
        return True

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        with self._open() as db:
            row = self._row(db, job_id, owner)
            return bool(row and row["cancel_requested"])

    def _finish(self, job_id: str, owner: str, content: str, status: str) -> bool:
        now = time.time()
        content = _truncate(content, self.max_content_chars)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, job_id, owner)
                if not row or row["status"] in TERMINAL_STATUSES:
                    db.rollback()
                    return False
                # Read cancellation inside the same write transaction as finalization.
                final_status = CANCELLED if bool(row["cancel_requested"]) else status
                event = _bound_event({"final": True, "status": final_status, "content": content, "done": True}, self.max_event_bytes)
                payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                size = len(payload.encode("utf-8", "replace"))
                seq = int(row["next_seq"]) + 1
                db.execute("INSERT INTO chat_job_events VALUES(?,?,?,?,?)", (job_id, seq, payload, size, now))
                db.execute("UPDATE chat_jobs SET status=?,content=?,next_seq=?,updated_at=?,expires_at=?,event_bytes=event_bytes+? "
                           "WHERE job_id=? AND owner=?", (final_status, content, seq, now, now + self.retention_seconds, size, job_id, owner))
                self._trim(db, job_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return True

    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False) -> bool:
        status = CANCELLED if cancelled or self.is_cancel_requested(job_id, owner) else COMPLETED
        return self._finish(job_id, owner, content, status)

    def fail(self, job_id: str, owner: str, safe_message: str) -> bool:
        now = time.time()
        message = _truncate(safe_message, self.max_content_chars)
        event = _bound_event({"message": {"role": "assistant", "content": message},
                              "final": True, "status": FAILED, "done": True}, self.max_event_bytes)
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        size = len(payload.encode("utf-8", "replace"))
        with self._open() as db:
            row = self._row(db, job_id, owner)
            if not row or row["status"] in TERMINAL_STATUSES:
                return False
            seq = int(row["next_seq"]) + 1
            progress = json.dumps({"message": {"role": "assistant", "content": message}}, ensure_ascii=False, separators=(",", ":"))
            progress_size = len(progress.encode("utf-8", "replace"))
            db.execute("INSERT INTO chat_job_events VALUES(?,?,?,?,?)", (job_id, seq, progress, progress_size, now))
            db.execute("INSERT INTO chat_job_events VALUES(?,?,?,?,?)", (job_id, seq + 1, payload, size, now))
            db.execute("UPDATE chat_jobs SET status=?,content=?,next_seq=?,updated_at=?,expires_at=?,event_bytes=event_bytes+? "
                       "WHERE job_id=? AND owner=?", (FAILED, message, seq + 1, now, now + self.retention_seconds, progress_size + size, job_id, owner))
            self._trim(db, job_id)
        return True

    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None:
        with self._open() as db:
            self._prune_locked(db)
            row = self._row(db, job_id, owner)
            if not row:
                return None
            events = db.execute("SELECT seq,payload FROM chat_job_events WHERE job_id=? AND seq>? ORDER BY seq",
                                (job_id, max(0, int(after)))).fetchall()
            return {"job_id": row["job_id"], "owner": row["owner"], "created_at": row["created_at"],
                    "updated_at": row["updated_at"], "expires_at": row["expires_at"], "status": row["status"],
                    "content": row["content"], "cancel_requested": bool(row["cancel_requested"]),
                    "next_seq": int(row["next_seq"]),
                    "events": [{"seq": int(item["seq"]), "event": json.loads(item["payload"])} for item in events]}

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        with self._open() as db:
            self._prune_locked(db)
            rows = db.execute("SELECT job_id,owner,created_at,updated_at,expires_at,status,content,next_seq,cancel_requested "
                              "FROM chat_jobs WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()
            return [dict(row) | {"cancel_requested": bool(row["cancel_requested"])} for row in rows]

    def prune(self) -> int:
        with self._open() as db:
            return self._prune_locked(db)


class InMemoryChatJobStore:
    """Explicit test backend; use CHAT_JOB_BACKEND=memory only for isolated development/tests."""

    def __init__(self, *, retention_seconds: int = JOB_RETENTION_SECONDS) -> None:
        self.retention_seconds = max(1, retention_seconds)
        self._jobs: dict[str, ChatJob] = {}
        self._lock = threading.RLock()

    def _prune_locked(self) -> int:
        now = time.time()
        ids = [key for key, job in self._jobs.items() if job.expires_at <= now]
        for key in ids:
            self._jobs.pop(key, None)
        return len(ids)

    def _owned(self, job_id: str, owner: str) -> ChatJob | None:
        job = self._jobs.get(job_id)
        return job if job and job.owner == owner and job.expires_at > time.time() else None

    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob:
        with self._lock:
            self._prune_locked()
            now = time.time()
            job = ChatJob(job_id, owner, now, now, now + self.retention_seconds, cancel_event=cancel_event)
            self._jobs[job_id] = job
            return job

    def publish(self, job_id: str, owner: str, event: dict[str, Any]) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status != ACTIVE:
                return False
            job.next_sequence += 1
            job.events.append((job.next_sequence, _bound_event(event)))
            job.events = job.events[-MAX_JOB_EVENTS:]
            job.updated_at = time.time()
            return True

    def request_cancel(self, job_id: str, owner: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status not in (ACTIVE, CANCELLING):
                return False
            job.cancel_requested, job.status = True, CANCELLING
            if job.cancel_event is not None:
                job.cancel_event.set()
            return True

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            return bool(job and job.cancel_requested)

    def _finish(self, job_id: str, owner: str, content: str, status: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status in TERMINAL_STATUSES:
                return False
            job.status, job.content = status, _truncate(content, MAX_JOB_CONTENT_CHARS)
            job.next_sequence += 1
            job.events.append((job.next_sequence, {"final": True, "status": status, "content": job.content, "done": True}))
            job.events = job.events[-MAX_JOB_EVENTS:]
            job.updated_at = time.time()
            return True

    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            requested = bool(job and job.cancel_requested)
        return self._finish(job_id, owner, content, CANCELLED if cancelled or requested else COMPLETED)

    def fail(self, job_id: str, owner: str, safe_message: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status in TERMINAL_STATUSES:
                return False
            message = _truncate(safe_message, MAX_JOB_CONTENT_CHARS)
            job.status, job.content = FAILED, message
            job.next_sequence += 1
            job.events.append((job.next_sequence, {"message": {"role": "assistant", "content": message}}))
            job.next_sequence += 1
            job.events.append((job.next_sequence, {"message": {"role": "assistant", "content": message},
                                                  "final": True, "status": FAILED, "done": True}))
            job.events = job.events[-MAX_JOB_EVENTS:]
            job.updated_at = time.time()
            return True

    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None:
        with self._lock:
            self._prune_locked()
            job = self._owned(job_id, owner)
            if not job:
                return None
            return {"job_id": job.job_id, "owner": job.owner, "created_at": job.created_at,
                    "updated_at": job.updated_at, "expires_at": job.expires_at, "status": job.status,
                    "content": job.content, "cancel_requested": job.cancel_requested,
                    "next_seq": job.next_sequence,
                    "events": [{"seq": seq, "event": dict(event)} for seq, event in job.events if seq > after]}

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            return [self.snapshot(job.job_id, owner) for job in self._jobs.values() if job.owner == owner]

    def prune(self) -> int:
        with self._lock:
            return self._prune_locked()


class ChatJobRegistry:
    """Compatibility facade plus process-local cancellation optimization."""

    def __init__(self, backend: ChatJobStore | None = None) -> None:
        if backend is not None:
            self.store = backend
        else:
            name = os.getenv("CHAT_JOB_BACKEND", "sqlite").strip().lower()
            if name == "memory":
                self.store = InMemoryChatJobStore()
            elif name == "sqlite":
                self.store = SQLiteChatJobStore()
            elif name in {"redis", "valkey"}:
                raise RuntimeError("CHAT_JOB_BACKEND=redis requires a configured shared Redis/Valkey adapter; SQLite is the supported default.")
            else:
                raise RuntimeError(f"Unsupported CHAT_JOB_BACKEND: {name}")
        self._local_cancel_events: dict[str, Any] = {}
        self._lock = threading.RLock()

    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob:
        with self._lock:
            if cancel_event is not None:
                self._local_cancel_events[job_id] = cancel_event
            return self.store.create(job_id, owner, cancel_event)

    def publish(self, job_id: str, owner: str, event: dict[str, Any]) -> bool:
        return self.store.publish(job_id, owner, event)

    def cancel(self, job_id: str, owner: str) -> bool:
        accepted = self.store.request_cancel(job_id, owner)
        if accepted and (event := self._local_cancel_events.get(job_id)) is not None:
            event.set()
        return accepted

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        return self.store.is_cancel_requested(job_id, owner)

    def complete(self, job_id: str, owner: str, content: str, *, streamed: bool = False, cancelled: bool = False) -> bool:
        return self.store.complete(job_id, owner, content, cancelled=cancelled)

    def fail(self, job_id: str, owner: str, safe_message: str) -> bool:
        return self.store.fail(job_id, owner, safe_message)

    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None:
        return self.store.snapshot(job_id, owner, after)

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        return self.store.list_for_owner(owner)

    def prune(self) -> int:
        return self.store.prune()


chat_job_registry = ChatJobRegistry()
