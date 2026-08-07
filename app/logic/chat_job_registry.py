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
MAX_JOB_CONTENT_BYTES = int(os.getenv("CHAT_JOB_MAX_CONTENT_BYTES", "2097152"))
MAX_EVENT_STORAGE_BYTES = int(os.getenv("CHAT_JOB_MAX_EVENT_STORAGE_BYTES", "2097152"))
JOB_LEASE_SECONDS = max(1.0, float(os.getenv("CHAT_JOB_LEASE_SECONDS", "30")))
JOB_LEASE_RENEW_SECONDS = max(0.2, float(os.getenv("CHAT_JOB_LEASE_RENEW_SECONDS", "5")))
INTERRUPTED_MESSAGE = "The server restarted before this response completed. Please retry."


class ChatJobCapacityError(RuntimeError):
    """Raised when bounded job storage cannot safely accept another job."""


def _truncate(value: Any, limit: int) -> str:
    limit = max(0, int(limit))
    raw = str(value or "").encode("utf-8", "replace")
    if len(raw) <= limit:
        return raw.decode("utf-8", "ignore")
    if limit <= 3:
        return raw[:limit].decode("utf-8", "ignore")
    return raw[: limit - 3].decode("utf-8", "ignore") + "..."


def _event_size(event: dict[str, Any]) -> int:
    return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace"))


def _bound_event(event: dict[str, Any], limit: int = MAX_EVENT_BYTES) -> dict[str, Any]:
    limit = max(1, int(limit))
    clean = dict(event or {})
    if isinstance(clean.get("message"), dict):
        clean["message"] = dict(clean["message"])
        clean["message"]["content"] = _truncate(clean["message"].get("content"), limit)
    if "content" in clean:
        clean["content"] = _truncate(clean["content"], limit)
    if _event_size(clean) <= limit:
        return clean

    message = clean.get("message") if isinstance(clean.get("message"), dict) else {}
    content = str(message.get("content") or clean.get("content") or "")
    compact = {
        "status": str(clean.get("status") or "Working")[:64],
        "final": bool(clean.get("final")),
        "done": bool(clean.get("done")),
        "content": "",
    }
    # Remove content by UTF-8 byte length; structural fields and flags survive.
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = dict(compact, content=_truncate(content, middle))
        if _event_size(candidate) <= limit:
            low = middle
        else:
            high = middle - 1
    compact["content"] = _truncate(content, low)
    if _event_size(compact) <= limit:
        return compact
    # A tiny limit may not fit the normal fallback keys. Return a valid JSON object.
    return {} if limit < 2 else {"content": ""}


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
    execution_id: str | None = None
    lease_expires_at: float = 0.0
    heartbeat_at: float = 0.0
    events: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    cancel_event: Any = None


class ChatJobStore(Protocol):
    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob: ...
    def claim(self, job_id: str, owner: str, execution_id: str) -> bool: ...
    def renew_lease(self, job_id: str, owner: str, execution_id: str) -> bool: ...
    def publish(self, job_id: str, owner: str, event: dict[str, Any], *, execution_id: str | None = None) -> bool: ...
    def request_cancel(self, job_id: str, owner: str) -> bool: ...
    def is_cancel_requested(self, job_id: str, owner: str) -> bool: ...
    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False, execution_id: str | None = None) -> bool: ...
    def fail(self, job_id: str, owner: str, safe_message: str, *, execution_id: str | None = None) -> bool: ...
    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None: ...
    def list_for_owner(self, owner: str) -> list[dict[str, Any]]: ...
    def prune(self) -> int: ...


class SQLiteChatJobStore:
    """SQLite WAL persistence shared by local workers and process restarts."""

    def __init__(self, db_file: str | Path | None = None, *, retention_seconds: int = JOB_RETENTION_SECONDS,
                 max_event_bytes: int = MAX_EVENT_BYTES, max_content_chars: int = MAX_JOB_CONTENT_CHARS,
                 max_events: int = MAX_JOB_EVENTS, max_retained_jobs: int = MAX_RETAINED_JOBS,
                 max_storage_bytes: int = 64 * 1024 * 1024,
                 max_content_bytes: int = MAX_JOB_CONTENT_BYTES,
                 max_event_storage_bytes: int = MAX_EVENT_STORAGE_BYTES,
                 lease_seconds: float = JOB_LEASE_SECONDS,
                 lease_renew_seconds: float = JOB_LEASE_RENEW_SECONDS) -> None:
        self.db_file = Path(db_file) if db_file else Path(__file__).resolve().parents[2] / ".runtime" / "chat_jobs.db"
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = max(1, int(retention_seconds))
        self.max_event_bytes = max(512, int(max_event_bytes))
        self.max_content_chars = max(256, int(max_content_chars))
        self.max_events = max(1, int(max_events))
        self.max_retained_jobs = max(1, int(max_retained_jobs))
        self.max_storage_bytes = max(1024, int(max_storage_bytes))
        self.max_content_bytes = max(256, int(max_content_bytes))
        self.max_event_storage_bytes = max(512, int(max_event_storage_bytes))
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.lease_renew_seconds = max(0.2, float(lease_renew_seconds))
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
              cancel_requested INTEGER NOT NULL DEFAULT 0, event_bytes INTEGER NOT NULL DEFAULT 0,
              content_bytes INTEGER NOT NULL DEFAULT 0,
              event_storage_bytes INTEGER NOT NULL DEFAULT 0,
              execution_id TEXT, lease_expires_at REAL NOT NULL DEFAULT 0,
              heartbeat_at REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS chat_jobs_owner_updated ON chat_jobs(owner, updated_at DESC);
            CREATE INDEX IF NOT EXISTS chat_jobs_expiry ON chat_jobs(expires_at);
            CREATE TABLE IF NOT EXISTS chat_job_events(
              job_id TEXT NOT NULL REFERENCES chat_jobs(job_id) ON DELETE CASCADE,
              seq INTEGER NOT NULL, payload TEXT NOT NULL, payload_bytes INTEGER NOT NULL,
              created_at REAL NOT NULL, PRIMARY KEY(job_id, seq));
            CREATE INDEX IF NOT EXISTS chat_events_cursor ON chat_job_events(job_id, seq);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(chat_jobs)").fetchall()}
            migrations = {
                "content_bytes": "ALTER TABLE chat_jobs ADD COLUMN content_bytes INTEGER NOT NULL DEFAULT 0",
                "event_storage_bytes": "ALTER TABLE chat_jobs ADD COLUMN event_storage_bytes INTEGER NOT NULL DEFAULT 0",
                "execution_id": "ALTER TABLE chat_jobs ADD COLUMN execution_id TEXT",
                "lease_expires_at": "ALTER TABLE chat_jobs ADD COLUMN lease_expires_at REAL NOT NULL DEFAULT 0",
                "heartbeat_at": "ALTER TABLE chat_jobs ADD COLUMN heartbeat_at REAL NOT NULL DEFAULT 0",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    db.execute(statement)
            db.execute("UPDATE chat_jobs SET content_bytes=length(CAST(content AS BLOB)) WHERE content_bytes=0 AND content <> ''")
            db.execute("UPDATE chat_jobs SET event_storage_bytes=event_bytes WHERE event_storage_bytes=0 AND event_bytes <> 0")

    def _logical_usage_locked(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT COALESCE(SUM(event_storage_bytes + content_bytes), 0) AS bytes FROM chat_jobs").fetchone()
        return int(row["bytes"] or 0)

    def _append_event_locked(self, db: sqlite3.Connection, job_id: str, seq: int,
                             event: dict[str, Any], now: float) -> int:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        payload_bytes = len(payload.encode("utf-8", "replace"))
        db.execute("INSERT INTO chat_job_events VALUES(?,?,?,?,?)", (job_id, seq, payload, payload_bytes, now))
        return payload_bytes

    def _terminalize_locked(self, db: sqlite3.Connection, row: sqlite3.Row, status: str,
                            content: str, now: float) -> None:
        message = _truncate(content, self.max_content_chars)
        final_status = CANCELLED if status != CANCELLED and bool(row["cancel_requested"]) else status
        progress = _bound_event({"message": {"role": "assistant", "content": message}}, self.max_event_bytes)
        final = _bound_event({"message": {"role": "assistant", "content": message},
                              "final": True, "status": final_status, "done": True,
                              "content": message}, self.max_event_bytes)
        seq = int(row["next_seq"]) + 1
        progress_bytes = self._append_event_locked(db, row["job_id"], seq, progress, now)
        final_bytes = self._append_event_locked(db, row["job_id"], seq + 1, final, now)
        content_bytes = len(message.encode("utf-8", "replace"))
        db.execute("UPDATE chat_jobs SET status=?,content=?,content_bytes=?,next_seq=?,updated_at=?,expires_at=?,"
                   "event_storage_bytes=event_storage_bytes+?,event_bytes=event_storage_bytes,"
                   "execution_id=NULL,lease_expires_at=0,heartbeat_at=? WHERE job_id=?",
                   (final_status, message, content_bytes, seq + 1, now, now + self.retention_seconds,
                    progress_bytes + final_bytes, now, row["job_id"]))
        self._trim(db, row["job_id"])

    def _recover_orphans_locked(self, db: sqlite3.Connection, now: float) -> int:
        rows = db.execute("SELECT * FROM chat_jobs WHERE status IN (?,?) AND lease_expires_at > 0 AND lease_expires_at <= ?",
                          (ACTIVE, CANCELLING, now)).fetchall()
        for row in rows:
            self._terminalize_locked(db, row, FAILED, INTERRUPTED_MESSAGE, now)
        return len(rows)

    def _prune_locked(self, db: sqlite3.Connection, now: float | None = None) -> int:
        now = time.time() if now is None else now
        recovered = self._recover_orphans_locked(db, now)
        expired = db.execute("SELECT job_id FROM chat_jobs WHERE expires_at <= ? AND "
                             "(status IN (?,?,?) OR (status IN (?,?) AND COALESCE(execution_id,'')=''))",
                             (now, COMPLETED, FAILED, CANCELLED, ACTIVE, CANCELLING)).fetchall()
        db.execute("DELETE FROM chat_jobs WHERE expires_at <= ? AND "
                   "(status IN (?,?,?) OR (status IN (?,?) AND COALESCE(execution_id,'')=''))",
                   (now, COMPLETED, FAILED, CANCELLED, ACTIVE, CANCELLING))
        old = db.execute("SELECT job_id FROM chat_jobs WHERE status IN (?,?,?) "
                         "ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
                         (COMPLETED, FAILED, CANCELLED, self.max_retained_jobs)).fetchall()
        db.executemany("DELETE FROM chat_jobs WHERE job_id=?", [(row["job_id"],) for row in old])
        usage = self._logical_usage_locked(db)
        if usage > self.max_storage_bytes:
            for row in db.execute("SELECT job_id FROM chat_jobs WHERE status IN (?,?,?) ORDER BY updated_at ASC",
                                  (COMPLETED, FAILED, CANCELLED)).fetchall():
                db.execute("DELETE FROM chat_jobs WHERE job_id=?", (row["job_id"],))
                usage = self._logical_usage_locked(db)
                if usage <= self.max_storage_bytes:
                    break
        return recovered + len(expired) + len(old)

    def _row(self, db: sqlite3.Connection, job_id: str, owner: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM chat_jobs WHERE job_id=? AND owner=?", (job_id, owner)).fetchone()

    def _trim(self, db: sqlite3.Connection, job_id: str) -> None:
        rows = db.execute("SELECT seq,payload,payload_bytes FROM chat_job_events WHERE job_id=? ORDER BY seq",
                          (job_id,)).fetchall()
        total = sum(int(row["payload_bytes"]) for row in rows)
        remove: list[int] = []
        for row in rows:
            if len(rows) - len(remove) <= self.max_events and total <= self.max_event_storage_bytes:
                break
            try:
                is_final = bool(json.loads(row["payload"]).get("final"))
            except (TypeError, ValueError):
                is_final = False
            if not is_final:
                remove.append(int(row["seq"]))
                total -= int(row["payload_bytes"])
        if remove:
            db.executemany("DELETE FROM chat_job_events WHERE job_id=? AND seq=?", [(job_id, seq) for seq in remove])
        db.execute("UPDATE chat_jobs SET event_bytes=COALESCE((SELECT SUM(payload_bytes) FROM chat_job_events WHERE job_id=?),0),"
                   "event_storage_bytes=COALESCE((SELECT SUM(payload_bytes) FROM chat_job_events WHERE job_id=?),0) WHERE job_id=?",
                   (job_id, job_id, job_id))

    def create(self, job_id: str, owner: str, cancel_event: Any = None) -> ChatJob:
        now = time.time()
        expires = now + self.retention_seconds
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db, now)
                if self._logical_usage_locked(db) >= self.max_storage_bytes:
                    raise ChatJobCapacityError("Chat job storage is temporarily full. Please retry shortly.")
                db.execute("INSERT INTO chat_jobs(job_id,owner,created_at,updated_at,expires_at,status) VALUES(?,?,?,?,?,?)",
                           (job_id, owner, now, now, expires, ACTIVE))
                db.commit()
            except Exception:
                db.rollback()
                raise
        return ChatJob(job_id, owner, now, now, expires, cancel_event=cancel_event)

    def claim(self, job_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._recover_orphans_locked(db, now)
                row = self._row(db, job_id, owner)
                if not row or row["status"] not in (ACTIVE, CANCELLING):
                    db.rollback()
                    return False
                if row["execution_id"] and float(row["lease_expires_at"] or 0) > now:
                    db.rollback()
                    return False
                db.execute("UPDATE chat_jobs SET execution_id=?,lease_expires_at=?,heartbeat_at=?,updated_at=?,expires_at=? "
                           "WHERE job_id=? AND owner=? AND status IN (?,?) AND (execution_id IS NULL OR lease_expires_at <= ?)",
                           (execution_id, now + self.lease_seconds, now, now, now + self.retention_seconds,
                            job_id, owner, ACTIVE, CANCELLING, now))
                accepted = db.execute("SELECT changes()").fetchone()[0] == 1
                db.commit()
                return accepted
            except Exception:
                db.rollback()
                raise

    def renew_lease(self, job_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute("UPDATE chat_jobs SET lease_expires_at=?,heartbeat_at=?,updated_at=?,expires_at=? "
                                    "WHERE job_id=? AND owner=? AND status IN (?,?) AND execution_id=? AND lease_expires_at > ?",
                                    (now + self.lease_seconds, now, now, now + self.retention_seconds,
                                     job_id, owner, ACTIVE, CANCELLING, execution_id, now)).rowcount == 1
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def publish(self, job_id: str, owner: str, event: dict[str, Any], *, execution_id: str | None = None) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, job_id, owner)
                if not row or row["status"] != ACTIVE:
                    db.rollback()
                    return False
                if execution_id is not None and (row["execution_id"] != execution_id or float(row["lease_expires_at"] or 0) <= now):
                    db.rollback()
                    return False
                bounded = _bound_event(event, self.max_event_bytes)
                seq = int(row["next_seq"]) + 1
                self._append_event_locked(db, job_id, seq, bounded, now)
                db.execute("UPDATE chat_jobs SET next_seq=?,updated_at=?,expires_at=? WHERE job_id=? AND owner=? AND status=?",
                           (seq, now, now + self.retention_seconds, job_id, owner, ACTIVE))
                self._trim(db, job_id)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def request_cancel(self, job_id: str, owner: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db, now)
                changed = db.execute("UPDATE chat_jobs SET status=?,cancel_requested=1,updated_at=?,expires_at=? "
                                    "WHERE job_id=? AND owner=? AND status IN (?,?)",
                                    (CANCELLING, now, now + self.retention_seconds, job_id, owner, ACTIVE, CANCELLING)).rowcount == 1
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        with self._open() as db:
            row = self._row(db, job_id, owner)
            return bool(row and row["cancel_requested"])

    def _finish(self, job_id: str, owner: str, content: str, status: str, execution_id: str | None = None) -> bool:
        now = time.time()
        content = _truncate(content, min(self.max_content_chars, self.max_content_bytes))
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, job_id, owner)
                if not row or row["status"] in TERMINAL_STATUSES:
                    db.rollback()
                    return False
                if execution_id is not None and row["execution_id"] != execution_id:
                    db.rollback()
                    return False
                # Read cancellation inside the same write transaction as finalization.
                final_status = CANCELLED if bool(row["cancel_requested"]) else status
                event = _bound_event({"final": True, "status": final_status, "content": content, "done": True}, self.max_event_bytes)
                seq = int(row["next_seq"]) + 1
                size = self._append_event_locked(db, job_id, seq, event, now)
                content_bytes = len(content.encode("utf-8", "replace"))
                db.execute("UPDATE chat_jobs SET status=?,content=?,content_bytes=?,next_seq=?,updated_at=?,expires_at=?,"
                           "execution_id=NULL,lease_expires_at=0,heartbeat_at=?,event_storage_bytes=event_storage_bytes+? "
                           "WHERE job_id=? AND owner=?", (final_status, content, content_bytes, seq, now,
                           now + self.retention_seconds, now, size, job_id, owner))
                self._trim(db, job_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return True

    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False,
                 execution_id: str | None = None) -> bool:
        return self._finish(job_id, owner, content, CANCELLED if cancelled else COMPLETED, execution_id)

    def fail(self, job_id: str, owner: str, safe_message: str, *, execution_id: str | None = None) -> bool:
        now = time.time()
        message = _truncate(safe_message, min(self.max_content_chars, self.max_content_bytes))
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, job_id, owner)
                if not row or row["status"] in TERMINAL_STATUSES:
                    db.rollback()
                    return False
                if execution_id is not None and row["execution_id"] != execution_id:
                    db.rollback()
                    return False
                progress = _bound_event({"message": {"role": "assistant", "content": message}}, self.max_event_bytes)
                final_status = CANCELLED if bool(row["cancel_requested"]) else FAILED
                final = _bound_event({"message": {"role": "assistant", "content": message},
                                      "final": True, "status": final_status, "done": True, "content": message}, self.max_event_bytes)
                seq = int(row["next_seq"]) + 1
                progress_size = self._append_event_locked(db, job_id, seq, progress, now)
                final_size = self._append_event_locked(db, job_id, seq + 1, final, now)
                content_bytes = len(message.encode("utf-8", "replace"))
                db.execute("UPDATE chat_jobs SET status=?,content=?,content_bytes=?,next_seq=?,updated_at=?,expires_at=?,"
                           "execution_id=NULL,lease_expires_at=0,heartbeat_at=?,event_storage_bytes=event_storage_bytes+? "
                           "WHERE job_id=? AND owner=?", (final_status, message, content_bytes, seq + 1, now,
                           now + self.retention_seconds, now, progress_size + final_size, job_id, owner))
                self._trim(db, job_id)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

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
                    "execution_id": row["execution_id"], "lease_expires_at": row["lease_expires_at"],
                    "heartbeat_at": row["heartbeat_at"],
                    "next_seq": int(row["next_seq"]),
                    "events": [{"seq": int(item["seq"]), "event": json.loads(item["payload"])} for item in events]}

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        with self._open() as db:
            self._prune_locked(db)
            rows = db.execute("SELECT job_id,owner,created_at,updated_at,expires_at,status,content,next_seq,cancel_requested,"
                              "execution_id,lease_expires_at,heartbeat_at "
                              "FROM chat_jobs WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()
            return [dict(row) | {"cancel_requested": bool(row["cancel_requested"])} for row in rows]

    def prune(self) -> int:
        with self._open() as db:
            return self._prune_locked(db)


class InMemoryChatJobStore:
    """Explicit test backend; use CHAT_JOB_BACKEND=memory only for isolated development/tests."""

    def __init__(self, *, retention_seconds: int = JOB_RETENTION_SECONDS,
                 lease_seconds: float = JOB_LEASE_SECONDS,
                 lease_renew_seconds: float = JOB_LEASE_RENEW_SECONDS) -> None:
        self.retention_seconds = max(1, retention_seconds)
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.lease_renew_seconds = max(0.2, float(lease_renew_seconds))
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

    def claim(self, job_id: str, owner: str, execution_id: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            now = time.time()
            if not job or job.status not in (ACTIVE, CANCELLING):
                return False
            if job.execution_id and job.lease_expires_at > now:
                return False
            job.execution_id = execution_id
            job.lease_expires_at = job.heartbeat_at = now + self.lease_seconds
            job.updated_at = now
            job.expires_at = now + self.retention_seconds
            return True

    def renew_lease(self, job_id: str, owner: str, execution_id: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            now = time.time()
            if not job or job.status not in (ACTIVE, CANCELLING) or job.execution_id != execution_id:
                return False
            if job.lease_expires_at <= now:
                return False
            job.lease_expires_at = job.heartbeat_at = now + self.lease_seconds
            job.updated_at = now
            job.expires_at = now + self.retention_seconds
            return True

    def publish(self, job_id: str, owner: str, event: dict[str, Any], *, execution_id: str | None = None) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status != ACTIVE:
                return False
            if execution_id is not None and (job.execution_id != execution_id or job.lease_expires_at <= time.time()):
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
            job.updated_at = time.time()
            job.expires_at = job.updated_at + self.retention_seconds
            if job.cancel_event is not None:
                job.cancel_event.set()
            return True

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            return bool(job and job.cancel_requested)

    def _finish(self, job_id: str, owner: str, content: str, status: str, execution_id: str | None = None) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status in TERMINAL_STATUSES:
                return False
            if execution_id is not None and job.execution_id != execution_id:
                return False
            final_status = CANCELLED if job.cancel_requested and status != CANCELLED else status
            job.status, job.content = final_status, _truncate(content, MAX_JOB_CONTENT_CHARS)
            job.execution_id = None
            job.lease_expires_at = 0
            job.next_sequence += 1
            job.events.append((job.next_sequence, _bound_event({"final": True, "status": final_status,
                                                               "content": job.content, "done": True})))
            job.events = job.events[-MAX_JOB_EVENTS:]
            job.updated_at = time.time()
            return True

    def complete(self, job_id: str, owner: str, content: str, *, cancelled: bool = False,
                 execution_id: str | None = None) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            requested = bool(job and job.cancel_requested)
        return self._finish(job_id, owner, content, CANCELLED if cancelled or requested else COMPLETED, execution_id)

    def fail(self, job_id: str, owner: str, safe_message: str, *, execution_id: str | None = None) -> bool:
        with self._lock:
            job = self._owned(job_id, owner)
            if not job or job.status in TERMINAL_STATUSES:
                return False
            if execution_id is not None and job.execution_id != execution_id:
                return False
            message = _truncate(safe_message, MAX_JOB_CONTENT_CHARS)
            job.status, job.content = FAILED, message
            job.execution_id = None
            job.lease_expires_at = 0
            job.next_sequence += 1
            job.events.append((job.next_sequence, _bound_event({"message": {"role": "assistant", "content": message}})))
            job.next_sequence += 1
            job.events.append((job.next_sequence, _bound_event({"message": {"role": "assistant", "content": message},
                                                                "final": True, "status": FAILED, "done": True})))
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
                    "execution_id": job.execution_id, "lease_expires_at": job.lease_expires_at,
                    "heartbeat_at": job.heartbeat_at,
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

    def claim(self, job_id: str, owner: str, execution_id: str) -> bool:
        return self.store.claim(job_id, owner, execution_id)

    def renew_lease(self, job_id: str, owner: str, execution_id: str) -> bool:
        return self.store.renew_lease(job_id, owner, execution_id)

    def publish(self, job_id: str, owner: str, event: dict[str, Any], *, execution_id: str | None = None) -> bool:
        return self.store.publish(job_id, owner, event, execution_id=execution_id)

    def cancel(self, job_id: str, owner: str) -> bool:
        accepted = self.store.request_cancel(job_id, owner)
        if accepted and (event := self._local_cancel_events.get(job_id)) is not None:
            event.set()
        return accepted

    def is_cancel_requested(self, job_id: str, owner: str) -> bool:
        return self.store.is_cancel_requested(job_id, owner)

    def complete(self, job_id: str, owner: str, content: str, *, streamed: bool = False,
                 cancelled: bool = False, execution_id: str | None = None) -> bool:
        return self.store.complete(job_id, owner, content, cancelled=cancelled, execution_id=execution_id)

    def fail(self, job_id: str, owner: str, safe_message: str, *, execution_id: str | None = None) -> bool:
        return self.store.fail(job_id, owner, safe_message, execution_id=execution_id)

    def snapshot(self, job_id: str, owner: str, after: int = 0) -> dict[str, Any] | None:
        return self.store.snapshot(job_id, owner, after)

    def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        return self.store.list_for_owner(owner)

    def prune(self) -> int:
        return self.store.prune()


chat_job_registry = ChatJobRegistry()
