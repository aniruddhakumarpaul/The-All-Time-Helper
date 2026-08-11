"""Durable owner-scoped workflow state and coordination.

The store records state transitions only. It never executes actions and never
replays interrupted work during startup or recovery.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app.logic.capability_policy import (
    ApprovalRequirement,
    CAPABILITY_REGISTRY,
    WORKFLOW_ACTION_CAPABILITY_IDS,
)


WORKFLOW_SCHEMA_VERSION = 1

PLANNED = "planned"
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

PENDING_ACTION = "pending"
RUNNING_ACTION = "running"
COMPLETED_ACTION = "completed"
FAILED_ACTION = "failed"
BLOCKED_ACTION = "blocked"
CANCELLED_ACTION = "cancelled"
PAUSED_ACTION = "paused"
INTERRUPTED_ACTION = "interrupted"
UNKNOWN_EXTERNAL_RESULT = "unknown_external_result"

TERMINAL_WORKFLOWS = {COMPLETED, FAILED, CANCELLED, INTERRUPTED}
SETTLED_ACTIONS = {
    COMPLETED_ACTION,
    FAILED_ACTION,
    BLOCKED_ACTION,
    CANCELLED_ACTION,
    PAUSED_ACTION,
    INTERRUPTED_ACTION,
    UNKNOWN_EXTERNAL_RESULT,
}
class WorkflowActionClass(str, Enum):
    READ_ONLY = "read_only"
    PURE_GENERATION = "pure_generation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


ACTION_CLASSES = {
    "web_search": WorkflowActionClass.READ_ONLY,
    "image_search": WorkflowActionClass.READ_ONLY,
    "image_generate": WorkflowActionClass.PURE_GENERATION,
    "build_email_draft": WorkflowActionClass.PURE_GENERATION,
    "update_email_draft": WorkflowActionClass.PURE_GENERATION,
    "attach_image": WorkflowActionClass.PURE_GENERATION,
    "general_response": WorkflowActionClass.PURE_GENERATION,
    "deliver_email": WorkflowActionClass.EXTERNAL_SIDE_EFFECT,
}

_EVENT_TYPES = {
    "workflow_planned",
    "action_started",
    "action_completed",
    "action_failed",
    "approval_required",
    "approval_granted",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
    "workflow_interrupted",
}
_SAFE_TOKEN_RE = re.compile(r"^[a-z0-9_.:-]{1,120}$")


class WorkflowStoreError(RuntimeError):
    """Base controlled workflow persistence failure."""


class WorkflowCapacityError(WorkflowStoreError):
    """The bounded workflow store cannot safely admit more state."""


class WorkflowStore(Protocol):
    def create(self, record: dict[str, Any], *, job_id: str | None = None) -> bool: ...
    def get(self, workflow_id: str, owner: str) -> dict[str, Any] | None: ...
    def list_for_owner(self, owner: str, *, limit: int = 50) -> list[dict[str, Any]]: ...
    def claim(self, workflow_id: str, owner: str, execution_id: str) -> bool: ...
    def renew_lease(self, workflow_id: str, owner: str, execution_id: str) -> bool: ...
    def policy_state(self, workflow_id: str, owner: str, execution_id: str) -> dict[str, Any] | None: ...
    def claim_action(self, workflow_id: str, owner: str, action_id: str, execution_id: str) -> bool: ...
    def finish_action(self, workflow_id: str, owner: str, action_id: str, execution_id: str, **kwargs: Any) -> bool: ...
    def pause_for_approval(self, workflow_id: str, owner: str, action_id: str, execution_id: str, record: dict[str, Any]) -> bool: ...
    def approve(self, workflow_id: str, owner: str, execution_id: str) -> bool: ...
    def request_cancel(self, workflow_id: str, owner: str) -> bool: ...
    def finish_workflow(self, workflow_id: str, owner: str, execution_id: str, *, status: str) -> bool: ...
    def prune(self) -> int: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _bytes(value: str | None) -> int:
    return len(str(value or "").encode("utf-8", "replace"))


def _safe_token(value: Any, fallback: str = "unknown") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _SAFE_TOKEN_RE.fullmatch(candidate) else fallback


class SQLiteWorkflowStore:
    """SQLite-WAL workflow store shared by local FastAPI workers."""

    def __init__(
        self,
        db_file: str | Path | None = None,
        *,
        retention_seconds: int = 86_400,
        approval_ttl_seconds: int = 600,
        lease_seconds: float = 30.0,
        lease_renew_seconds: float = 5.0,
        max_events: int = 200,
        max_retained_runs: int = 500,
        max_result_bytes: int = 64 * 1024,
        max_storage_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        configured = db_file or os.getenv("WORKFLOW_DB_FILE") or root / ".runtime" / "workflows.db"
        path = Path(configured)
        if not path.is_absolute():
            path = root / path
        self.db_file = path
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = max(60, int(retention_seconds))
        self.approval_ttl_seconds = max(30, int(approval_ttl_seconds))
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.lease_renew_seconds = max(0.2, min(float(lease_renew_seconds), self.lease_seconds / 2))
        self.max_events = max(1, int(max_events))
        self.max_retained_runs = max(1, int(max_retained_runs))
        self.max_result_bytes = max(512, int(max_result_bytes))
        self.max_storage_bytes = max(4096, int(max_storage_bytes))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        for attempt in range(5):
            db = sqlite3.connect(self.db_file, timeout=10, isolation_level=None)
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA busy_timeout=10000")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("PRAGMA foreign_keys=ON")
                return db
            except sqlite3.OperationalError as exc:
                db.close()
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise WorkflowStoreError("Workflow database connection could not be initialized.")

    @contextmanager
    def _open(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_schema(
                      id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL
                    );
                    INSERT OR IGNORE INTO workflow_schema(id,version) VALUES(1,1);
                    CREATE TABLE IF NOT EXISTS workflow_runs(
                      workflow_id TEXT PRIMARY KEY,
                      owner TEXT NOT NULL,
                      intent TEXT NOT NULL,
                      status TEXT NOT NULL,
                      approval_state TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL,
                      expires_at REAL NOT NULL,
                      execution_id TEXT,
                      lease_expires_at REAL NOT NULL DEFAULT 0,
                      heartbeat_at REAL NOT NULL DEFAULT 0,
                      version INTEGER NOT NULL DEFAULT 1,
                      topic TEXT NOT NULL DEFAULT '',
                      plan_json TEXT NOT NULL,
                      job_id TEXT,
                      cancel_requested INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS workflow_runs_owner_updated
                      ON workflow_runs(owner,updated_at DESC);
                    CREATE INDEX IF NOT EXISTS workflow_runs_status
                      ON workflow_runs(status);
                    CREATE INDEX IF NOT EXISTS workflow_runs_expiry
                      ON workflow_runs(expires_at);
                    CREATE TABLE IF NOT EXISTS workflow_actions(
                      workflow_id TEXT NOT NULL,
                      action_id TEXT NOT NULL,
                      action_type TEXT NOT NULL,
                      state TEXT NOT NULL,
                      depends_on_json TEXT NOT NULL,
                      optional_depends_on_json TEXT NOT NULL,
                      can_run_parallel INTEGER NOT NULL DEFAULT 0,
                      sensitive INTEGER NOT NULL DEFAULT 0,
                      terminal INTEGER NOT NULL DEFAULT 0,
                      attempt INTEGER NOT NULL DEFAULT 0,
                      started_at REAL,
                      completed_at REAL,
                      duration_ms INTEGER NOT NULL DEFAULT 0,
                      error_category TEXT,
                      output_json TEXT,
                      execution_id TEXT,
                      PRIMARY KEY(workflow_id,action_id),
                      FOREIGN KEY(workflow_id) REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS workflow_actions_state
                      ON workflow_actions(workflow_id,state);
                    CREATE TABLE IF NOT EXISTS workflow_approvals(
                      workflow_id TEXT PRIMARY KEY,
                      owner TEXT NOT NULL,
                      approval_state TEXT NOT NULL,
                      approval_required_for TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      expires_at REAL NOT NULL,
                      claimed_at REAL,
                      FOREIGN KEY(workflow_id) REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS workflow_approvals_owner
                      ON workflow_approvals(owner,expires_at);
                    CREATE TABLE IF NOT EXISTS workflow_events(
                      seq INTEGER PRIMARY KEY AUTOINCREMENT,
                      workflow_id TEXT NOT NULL,
                      owner TEXT NOT NULL,
                      event_type TEXT NOT NULL,
                      action_type TEXT,
                      state TEXT,
                      duration_ms INTEGER NOT NULL DEFAULT 0,
                      created_at REAL NOT NULL,
                      FOREIGN KEY(workflow_id) REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS workflow_events_run
                      ON workflow_events(workflow_id,seq);
                    """
                )
                version = db.execute("SELECT version FROM workflow_schema WHERE id=1").fetchone()
                if not version or int(version[0]) != WORKFLOW_SCHEMA_VERSION:
                    raise WorkflowStoreError("Unsupported workflow database schema version.")
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _row(db: sqlite3.Connection, workflow_id: str, owner: str) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id=? AND owner=?",
            (workflow_id, owner),
        ).fetchone()

    def _append_event_locked(
        self,
        db: sqlite3.Connection,
        workflow_id: str,
        owner: str,
        event_type: str,
        *,
        action_type: str | None = None,
        state: str | None = None,
        duration_ms: int = 0,
        now: float | None = None,
    ) -> None:
        safe_event = event_type if event_type in _EVENT_TYPES else "workflow_failed"
        db.execute(
            "INSERT INTO workflow_events(workflow_id,owner,event_type,action_type,state,duration_ms,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                workflow_id,
                owner,
                safe_event,
                _safe_token(action_type, "unknown") if action_type else None,
                _safe_token(state, "unknown") if state else None,
                max(0, min(int(duration_ms or 0), 86_400_000)),
                time.time() if now is None else now,
            ),
        )
        db.execute(
            "DELETE FROM workflow_events WHERE workflow_id=? AND seq NOT IN "
            "(SELECT seq FROM workflow_events WHERE workflow_id=? ORDER BY seq DESC LIMIT ?)",
            (workflow_id, workflow_id, self.max_events),
        )

    def _logical_usage_locked(self, db: sqlite3.Connection) -> int:
        total = 0
        for table in (
            "workflow_runs",
            "workflow_actions",
            "workflow_approvals",
            "workflow_events",
        ):
            for row in db.execute(f"SELECT * FROM {table}"):
                for value in row:
                    total += _bytes(value) if isinstance(value, str) else 8
        return total

    def logical_usage(self) -> int:
        with self._open() as db:
            return self._logical_usage_locked(db)

    def _recover_interrupted_locked(self, db: sqlite3.Connection, now: float) -> int:
        rows = db.execute(
            "SELECT workflow_id,owner FROM workflow_runs WHERE status=? AND execution_id IS NOT NULL "
            "AND lease_expires_at>0 AND lease_expires_at<=?",
            (RUNNING, now),
        ).fetchall()
        for row in rows:
            actions = db.execute(
                "SELECT action_id,action_type FROM workflow_actions WHERE workflow_id=? AND state=?",
                (row["workflow_id"], RUNNING_ACTION),
            ).fetchall()
            for action in actions:
                external = (
                    ACTION_CLASSES.get(action["action_type"])
                    == WorkflowActionClass.EXTERNAL_SIDE_EFFECT
                )
                next_state = UNKNOWN_EXTERNAL_RESULT if external else INTERRUPTED_ACTION
                error = "external_result_unknown" if external else "worker_interrupted"
                db.execute(
                    "UPDATE workflow_actions SET state=?,completed_at=?,error_category=?,execution_id=NULL "
                    "WHERE workflow_id=? AND action_id=? AND state=?",
                    (next_state, now, error, row["workflow_id"], action["action_id"], RUNNING_ACTION),
                )
            db.execute(
                "UPDATE workflow_runs SET status=?,updated_at=?,expires_at=?,execution_id=NULL,"
                "lease_expires_at=0,heartbeat_at=? WHERE workflow_id=? AND owner=? AND status=?",
                (INTERRUPTED, now, now + self.retention_seconds, now, row["workflow_id"], row["owner"], RUNNING),
            )
            self._append_event_locked(
                db,
                row["workflow_id"],
                row["owner"],
                "workflow_interrupted",
                state=INTERRUPTED,
                now=now,
            )
        return len(rows)

    def _prune_locked(self, db: sqlite3.Connection, now: float | None = None) -> int:
        current = time.time() if now is None else now
        changed = self._recover_interrupted_locked(db, current)

        expired = db.execute(
            "DELETE FROM workflow_runs WHERE status IN (?,?,?,?) AND expires_at<=? "
            "AND (execution_id IS NULL OR lease_expires_at<=?)",
            (COMPLETED, FAILED, CANCELLED, INTERRUPTED, current, current),
        ).rowcount
        changed += max(0, expired)

        terminal_rows = db.execute(
            "SELECT workflow_id FROM workflow_runs WHERE status IN (?,?,?,?) "
            "AND (execution_id IS NULL OR lease_expires_at<=?) ORDER BY updated_at DESC",
            (COMPLETED, FAILED, CANCELLED, INTERRUPTED, current),
        ).fetchall()
        for row in terminal_rows[self.max_retained_runs :]:
            changed += db.execute("DELETE FROM workflow_runs WHERE workflow_id=?", (row["workflow_id"],)).rowcount

        while self._logical_usage_locked(db) > self.max_storage_bytes:
            victim = db.execute(
                "SELECT workflow_id FROM workflow_runs WHERE status IN (?,?,?,?) "
                "AND execution_id IS NULL ORDER BY updated_at LIMIT 1",
                (COMPLETED, FAILED, CANCELLED, INTERRUPTED),
            ).fetchone()
            if victim:
                changed += db.execute("DELETE FROM workflow_runs WHERE workflow_id=?", (victim["workflow_id"],)).rowcount
                continue
            event = db.execute(
                "SELECT seq FROM workflow_events WHERE seq NOT IN "
                "(SELECT MAX(seq) FROM workflow_events GROUP BY workflow_id) ORDER BY seq LIMIT 1"
            ).fetchone()
            if not event:
                break
            changed += db.execute("DELETE FROM workflow_events WHERE seq=?", (event["seq"],)).rowcount

        expired_unclaimed = db.execute(
            "DELETE FROM workflow_runs WHERE status IN (?,?) AND expires_at<=? AND execution_id IS NULL",
            (PLANNED, PAUSED, current),
        ).rowcount
        changed += max(0, expired_unclaimed)
        return changed

    def _check_capacity_locked(self, db: sqlite3.Connection) -> None:
        if self._logical_usage_locked(db) > self.max_storage_bytes:
            raise WorkflowCapacityError("Workflow storage is temporarily full.")

    def create(self, record: dict[str, Any], *, job_id: str | None = None) -> bool:
        if int(record.get("schema_version") or 0) != WORKFLOW_SCHEMA_VERSION:
            raise WorkflowStoreError("Unsupported workflow persistence schema version.")
        workflow_id = str(record.get("workflow_id") or "")
        owner = str(record.get("owner") or "")
        if not workflow_id or not owner:
            raise WorkflowStoreError("Workflow identity is required.")
        actions = list(record.get("actions") or [])
        plan_json = _json(record)
        if _bytes(plan_json) > self.max_result_bytes * 4:
            raise WorkflowCapacityError("Workflow plan is too large to persist safely.")
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db, now)
                if self._row(db, workflow_id, owner):
                    db.commit()
                    return False
                if db.execute("SELECT 1 FROM workflow_runs WHERE workflow_id=?", (workflow_id,)).fetchone():
                    db.commit()
                    return False
                db.execute(
                    "INSERT INTO workflow_runs(workflow_id,owner,intent,status,approval_state,created_at,updated_at,"
                    "expires_at,version,topic,plan_json,job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        workflow_id,
                        owner,
                        _safe_token(record.get("intent")),
                        PLANNED,
                        _safe_token(record.get("approval_state"), "not_required"),
                        float(record.get("created_at") or now),
                        now,
                        float(record.get("expires_at") or now + self.retention_seconds),
                        WORKFLOW_SCHEMA_VERSION,
                        str(record.get("topic") or "")[:500],
                        plan_json,
                        str(job_id or "")[:120] or None,
                    ),
                )
                for action in actions:
                    db.execute(
                        "INSERT INTO workflow_actions(workflow_id,action_id,action_type,state,depends_on_json,"
                        "optional_depends_on_json,can_run_parallel,sensitive,terminal) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            workflow_id,
                            str(action.get("id") or "")[:120],
                            _safe_token(action.get("action_type")),
                            PENDING_ACTION,
                            _json(list(action.get("depends_on") or [])),
                            _json(list(action.get("optional_depends_on") or [])),
                            int(bool(action.get("can_run_parallel"))),
                            int(bool(action.get("sensitive"))),
                            int(bool(action.get("terminal"))),
                        ),
                    )
                self._append_event_locked(db, workflow_id, owner, "workflow_planned", state=PLANNED, now=now)
                self._prune_locked(db, now)
                self._check_capacity_locked(db)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def _snapshot_locked(self, db: sqlite3.Connection, workflow_id: str, owner: str) -> dict[str, Any] | None:
        row = self._row(db, workflow_id, owner)
        if not row:
            return None
        actions = db.execute(
            "SELECT action_id,action_type,state,attempt,started_at,completed_at,duration_ms,error_category,output_json "
            "FROM workflow_actions WHERE workflow_id=? ORDER BY rowid",
            (workflow_id,),
        ).fetchall()
        approval = db.execute(
            "SELECT approval_state,approval_required_for,created_at,expires_at,claimed_at "
            "FROM workflow_approvals WHERE workflow_id=? AND owner=?",
            (workflow_id, owner),
        ).fetchone()
        return {
            "workflow_id": row["workflow_id"],
            "owner": row["owner"],
            "intent": row["intent"],
            "status": row["status"],
            "approval_state": row["approval_state"],
            "approval_required": row["approval_state"] == "required",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "execution_id": row["execution_id"],
            "lease_expires_at": row["lease_expires_at"],
            "heartbeat_at": row["heartbeat_at"],
            "version": row["version"],
            "topic": row["topic"],
            "job_id": row["job_id"],
            "cancel_requested": bool(row["cancel_requested"]),
            "plan": json.loads(row["plan_json"]),
            "approval": dict(approval) if approval else None,
            "actions": [
                dict(item) | {"output": json.loads(item["output_json"]) if item["output_json"] else None}
                for item in actions
            ],
        }

    def get(self, workflow_id: str, owner: str) -> dict[str, Any] | None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db)
                snapshot = self._snapshot_locked(db, workflow_id, owner)
                db.commit()
                return snapshot
            except Exception:
                db.rollback()
                raise

    def find_paused_for_owner(self, owner: str) -> dict[str, Any] | None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db)
                row = db.execute(
                    "SELECT workflow_id FROM workflow_runs WHERE owner=? AND status=? "
                    "AND approval_state=? AND cancel_requested=0 ORDER BY updated_at DESC LIMIT 1",
                    (owner, PAUSED, "required"),
                ).fetchone()
                snapshot = self._snapshot_locked(db, row["workflow_id"], owner) if row else None
                db.commit()
                return snapshot
            except Exception:
                db.rollback()
                raise

    def list_for_owner(self, owner: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db)
                ids = db.execute(
                    "SELECT workflow_id FROM workflow_runs WHERE owner=? ORDER BY updated_at DESC LIMIT ?",
                    (owner, max(1, min(int(limit), 100))),
                ).fetchall()
                snapshots = [self._snapshot_locked(db, row["workflow_id"], owner) for row in ids]
                db.commit()
                return [item for item in snapshots if item]
            except Exception:
                db.rollback()
                raise

    def claim(self, workflow_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(db, now)
                changed = db.execute(
                    "UPDATE workflow_runs SET status=?,execution_id=?,lease_expires_at=?,heartbeat_at=?,updated_at=? "
                    "WHERE workflow_id=? AND owner=? AND status IN (?,?) AND cancel_requested=0 "
                    "AND (execution_id IS NULL OR lease_expires_at<=?)",
                    (
                        RUNNING,
                        execution_id,
                        now + self.lease_seconds,
                        now,
                        now,
                        workflow_id,
                        owner,
                        PLANNED,
                        PAUSED,
                        now,
                    ),
                ).rowcount == 1
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def renew_lease(self, workflow_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    "UPDATE workflow_runs SET lease_expires_at=?,heartbeat_at=?,updated_at=? "
                    "WHERE workflow_id=? AND owner=? AND status=? AND execution_id=? "
                    "AND lease_expires_at>? AND cancel_requested=0",
                    (now + self.lease_seconds, now, now, workflow_id, owner, RUNNING, execution_id, now),
                ).rowcount == 1
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def is_cancel_requested(self, workflow_id: str, owner: str) -> bool:
        with self._open() as db:
            row = self._row(db, workflow_id, owner)
            return bool(row and row["cancel_requested"])

    def policy_state(self, workflow_id: str, owner: str, execution_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._open() as db:
            row = self._row(db, workflow_id, owner)
            if not row or row["execution_id"] != execution_id:
                return None
            return {
                "workflow_status": str(row["status"]),
                "workflow_approval_state": str(row["approval_state"]),
                "workflow_lease_valid": bool(float(row["lease_expires_at"] or 0) > now),
                "cancel_requested": bool(row["cancel_requested"]),
            }

    def approve(self, workflow_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                approval = db.execute(
                    "SELECT expires_at FROM workflow_approvals WHERE workflow_id=? AND owner=? AND approval_state=?",
                    (workflow_id, owner, "required"),
                ).fetchone()
                if (
                    not row
                    or row["status"] != RUNNING
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                    or bool(row["cancel_requested"])
                    or not approval
                    or float(approval["expires_at"] or 0) <= now
                ):
                    db.rollback()
                    return False
                db.execute(
                    "UPDATE workflow_runs SET approval_state=?,updated_at=? WHERE workflow_id=? AND owner=?",
                    ("approved", now, workflow_id, owner),
                )
                db.execute(
                    "UPDATE workflow_approvals SET approval_state=?,claimed_at=? WHERE workflow_id=? AND owner=?",
                    ("approved", now, workflow_id, owner),
                )
                db.execute(
                    "UPDATE workflow_actions SET state=? WHERE workflow_id=? AND state=?",
                    (PENDING_ACTION, workflow_id, PAUSED_ACTION),
                )
                self._append_event_locked(db, workflow_id, owner, "approval_granted", state="approved", now=now)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def claim_action(self, workflow_id: str, owner: str, action_id: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                action = db.execute(
                    "SELECT * FROM workflow_actions WHERE workflow_id=? AND action_id=?",
                    (workflow_id, action_id),
                ).fetchone()
                capability_id = (
                    WORKFLOW_ACTION_CAPABILITY_IDS.get(str(action["action_type"]))
                    if action
                    else None
                )
                capability = CAPABILITY_REGISTRY.get(capability_id or "")
                approval_required = bool(
                    capability
                    and capability.approval
                    in {
                        ApprovalRequirement.WORKFLOW_APPROVAL,
                        ApprovalRequirement.REQUEST_SCOPED_AUTHORIZATION,
                        ApprovalRequirement.EXPLICIT_CONFIRMATION,
                    }
                )
                if (
                    not row
                    or not action
                    or capability is None
                    or row["status"] != RUNNING
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                    or bool(row["cancel_requested"])
                    or action["state"] != PENDING_ACTION
                    or (approval_required and row["approval_state"] != "approved")
                ):
                    db.rollback()
                    return False
                required = json.loads(action["depends_on_json"])
                optional = json.loads(action["optional_depends_on_json"])
                states = {
                    item["action_id"]: item["state"]
                    for item in db.execute(
                        "SELECT action_id,state FROM workflow_actions WHERE workflow_id=?",
                        (workflow_id,),
                    )
                }
                if any(states.get(item) != COMPLETED_ACTION for item in required):
                    db.rollback()
                    return False
                if any(states.get(item) not in SETTLED_ACTIONS for item in optional):
                    db.rollback()
                    return False
                changed = db.execute(
                    "UPDATE workflow_actions SET state=?,attempt=attempt+1,started_at=?,completed_at=NULL,"
                    "error_category=NULL,execution_id=? WHERE workflow_id=? AND action_id=? AND state=?",
                    (RUNNING_ACTION, now, execution_id, workflow_id, action_id, PENDING_ACTION),
                ).rowcount == 1
                if changed:
                    self._append_event_locked(
                        db,
                        workflow_id,
                        owner,
                        "action_started",
                        action_type=action["action_type"],
                        state=RUNNING_ACTION,
                        now=now,
                    )
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def finish_action(
        self,
        workflow_id: str,
        owner: str,
        action_id: str,
        execution_id: str,
        *,
        state: str,
        output: dict[str, Any] | None = None,
        error_category: str | None = None,
        duration_ms: int = 0,
    ) -> bool:
        if state not in {
            COMPLETED_ACTION,
            FAILED_ACTION,
            BLOCKED_ACTION,
            CANCELLED_ACTION,
            INTERRUPTED_ACTION,
            UNKNOWN_EXTERNAL_RESULT,
        }:
            raise WorkflowStoreError("Unsupported terminal workflow action state.")
        output_json = _json(output) if output is not None else None
        if _bytes(output_json) > self.max_result_bytes:
            raise WorkflowCapacityError("Workflow action result is too large to persist safely.")
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                action = db.execute(
                    "SELECT action_type,state,execution_id FROM workflow_actions WHERE workflow_id=? AND action_id=?",
                    (workflow_id, action_id),
                ).fetchone()
                if (
                    not row
                    or not action
                    or row["status"] != RUNNING
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                    or action["state"] != RUNNING_ACTION
                    or action["execution_id"] != execution_id
                ):
                    db.rollback()
                    return False
                db.execute(
                    "UPDATE workflow_actions SET state=?,completed_at=?,duration_ms=?,error_category=?,"
                    "output_json=?,execution_id=NULL WHERE workflow_id=? AND action_id=? AND state=?",
                    (
                        state,
                        now,
                        max(0, min(int(duration_ms or 0), 86_400_000)),
                        _safe_token(error_category, "unknown") if error_category else None,
                        output_json,
                        workflow_id,
                        action_id,
                        RUNNING_ACTION,
                    ),
                )
                event = "action_completed" if state == COMPLETED_ACTION else "action_failed"
                self._append_event_locked(
                    db,
                    workflow_id,
                    owner,
                    event,
                    action_type=action["action_type"],
                    state=state,
                    duration_ms=duration_ms,
                    now=now,
                )
                self._prune_locked(db, now)
                self._check_capacity_locked(db)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def settle_unstarted_action(
        self,
        workflow_id: str,
        owner: str,
        action_id: str,
        execution_id: str,
        *,
        state: str,
        error_category: str,
    ) -> bool:
        if state not in {BLOCKED_ACTION, CANCELLED_ACTION}:
            raise WorkflowStoreError("Unsupported unstarted workflow action state.")
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                action = db.execute(
                    "SELECT action_type,state FROM workflow_actions WHERE workflow_id=? AND action_id=?",
                    (workflow_id, action_id),
                ).fetchone()
                if (
                    not row
                    or not action
                    or row["status"] != RUNNING
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                    or action["state"] != PENDING_ACTION
                ):
                    db.rollback()
                    return False
                db.execute(
                    "UPDATE workflow_actions SET state=?,completed_at=?,error_category=? "
                    "WHERE workflow_id=? AND action_id=? AND state=?",
                    (
                        state,
                        now,
                        _safe_token(error_category),
                        workflow_id,
                        action_id,
                        PENDING_ACTION,
                    ),
                )
                self._append_event_locked(
                    db,
                    workflow_id,
                    owner,
                    "action_failed",
                    action_type=action["action_type"],
                    state=state,
                    now=now,
                )
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def update_plan(self, workflow_id: str, owner: str, execution_id: str, record: dict[str, Any]) -> bool:
        plan_json = _json(record)
        if _bytes(plan_json) > self.max_result_bytes * 4:
            raise WorkflowCapacityError("Workflow plan is too large to persist safely.")
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    "UPDATE workflow_runs SET plan_json=?,approval_state=?,topic=?,updated_at=? "
                    "WHERE workflow_id=? AND owner=? AND status=? AND execution_id=? AND lease_expires_at>?",
                    (
                        plan_json,
                        _safe_token(record.get("approval_state"), "not_required"),
                        str(record.get("topic") or "")[:500],
                        now,
                        workflow_id,
                        owner,
                        RUNNING,
                        execution_id,
                        now,
                    ),
                ).rowcount == 1
                self._check_capacity_locked(db)
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def replace_paused_plan(
        self,
        workflow_id: str,
        owner: str,
        record: dict[str, Any],
    ) -> bool:
        """Compatibility update for an unclaimed paused workflow."""
        plan_json = _json(record)
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    "UPDATE workflow_runs SET plan_json=?,approval_state=?,topic=?,updated_at=?,expires_at=? "
                    "WHERE workflow_id=? AND owner=? AND status=? AND execution_id IS NULL",
                    (
                        plan_json,
                        _safe_token(record.get("approval_state"), "required"),
                        str(record.get("topic") or "")[:500],
                        now,
                        float(record.get("expires_at") or now),
                        workflow_id,
                        owner,
                        PAUSED,
                    ),
                ).rowcount == 1
                self._prune_locked(db, now)
                self._check_capacity_locked(db)
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def pause_for_approval(
        self,
        workflow_id: str,
        owner: str,
        action_id: str,
        execution_id: str,
        record: dict[str, Any],
    ) -> bool:
        now = time.time()
        approval_expires = min(float(record.get("expires_at") or now + self.approval_ttl_seconds), now + self.approval_ttl_seconds)
        plan_json = _json(record)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                action = db.execute(
                    "SELECT action_type,state FROM workflow_actions WHERE workflow_id=? AND action_id=?",
                    (workflow_id, action_id),
                ).fetchone()
                if (
                    not row
                    or not action
                    or row["status"] != RUNNING
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                    or bool(row["cancel_requested"])
                    or action["state"] not in {PENDING_ACTION, RUNNING_ACTION}
                ):
                    db.rollback()
                    return False
                db.execute(
                    "UPDATE workflow_actions SET state=?,error_category=?,execution_id=NULL "
                    "WHERE workflow_id=? AND action_id=? AND state=?",
                    (PAUSED_ACTION, "authorization_required", workflow_id, action_id, action["state"]),
                )
                db.execute(
                    "UPDATE workflow_runs SET status=?,approval_state=?,plan_json=?,updated_at=?,expires_at=?,"
                    "execution_id=NULL,lease_expires_at=0,heartbeat_at=? WHERE workflow_id=? AND owner=?",
                    (PAUSED, "required", plan_json, now, approval_expires, now, workflow_id, owner),
                )
                db.execute(
                    "INSERT INTO workflow_approvals(workflow_id,owner,approval_state,approval_required_for,created_at,expires_at,claimed_at) "
                    "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(workflow_id) DO UPDATE SET approval_state=excluded.approval_state,"
                    "approval_required_for=excluded.approval_required_for,created_at=excluded.created_at,"
                    "expires_at=excluded.expires_at,claimed_at=NULL",
                    (workflow_id, owner, "required", action_id, now, approval_expires),
                )
                self._append_event_locked(
                    db,
                    workflow_id,
                    owner,
                    "approval_required",
                    action_type=action["action_type"],
                    state=PAUSED,
                    now=now,
                )
                self._check_capacity_locked(db)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def release(self, workflow_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            changed = db.execute(
                "UPDATE workflow_runs SET status=?,execution_id=NULL,lease_expires_at=0,heartbeat_at=?,updated_at=? "
                "WHERE workflow_id=? AND owner=? AND status=? AND execution_id=?",
                (PAUSED, now, now, workflow_id, owner, RUNNING, execution_id),
            ).rowcount == 1
            return changed

    def finish_workflow(self, workflow_id: str, owner: str, execution_id: str, *, status: str) -> bool:
        if status not in TERMINAL_WORKFLOWS:
            raise WorkflowStoreError("Unsupported terminal workflow state.")
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                if (
                    not row
                    or row["status"] in TERMINAL_WORKFLOWS
                    or row["execution_id"] != execution_id
                    or float(row["lease_expires_at"] or 0) <= now
                ):
                    db.rollback()
                    return False
                final_status = CANCELLED if bool(row["cancel_requested"]) and status != COMPLETED else status
                event = {
                    COMPLETED: "workflow_completed",
                    FAILED: "workflow_failed",
                    CANCELLED: "workflow_cancelled",
                    INTERRUPTED: "workflow_interrupted",
                }[final_status]
                db.execute(
                    "UPDATE workflow_runs SET status=?,updated_at=?,expires_at=?,execution_id=NULL,"
                    "lease_expires_at=0,heartbeat_at=? WHERE workflow_id=? AND owner=?",
                    (final_status, now, now + self.retention_seconds, now, workflow_id, owner),
                )
                self._append_event_locked(db, workflow_id, owner, event, state=final_status, now=now)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def request_cancel(self, workflow_id: str, owner: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(db, workflow_id, owner)
                if not row:
                    db.rollback()
                    return False
                if row["status"] == CANCELLED:
                    db.commit()
                    return True
                if row["status"] in TERMINAL_WORKFLOWS:
                    db.rollback()
                    return False
                immediate = row["status"] in {PLANNED, PAUSED} and not row["execution_id"]
                next_status = CANCELLED if immediate else row["status"]
                db.execute(
                    "UPDATE workflow_runs SET cancel_requested=1,status=?,updated_at=?,expires_at=? "
                    "WHERE workflow_id=? AND owner=?",
                    (next_status, now, now + self.retention_seconds, workflow_id, owner),
                )
                if immediate:
                    db.execute(
                        "UPDATE workflow_actions SET state=?,completed_at=?,error_category=? "
                        "WHERE workflow_id=? AND state IN (?,?)",
                        (CANCELLED_ACTION, now, "cancelled", workflow_id, PENDING_ACTION, PAUSED_ACTION),
                    )
                    self._append_event_locked(db, workflow_id, owner, "workflow_cancelled", state=CANCELLED, now=now)
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def prune(self) -> int:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._prune_locked(db)
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def delete_all_for_tests(self) -> None:
        """Clear this dedicated test database; application code never calls this."""
        with self._open() as db:
            db.execute("DELETE FROM workflow_runs")


def public_workflow_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the authenticated recovery DTO without plans, outputs, or arguments."""
    return {
        "workflow_id": snapshot["workflow_id"],
        "status": snapshot["status"],
        "intent": snapshot["intent"],
        "approval_required": bool(snapshot.get("approval_required")),
        "created_at": snapshot["created_at"],
        "updated_at": snapshot["updated_at"],
        "expires_at": snapshot["expires_at"],
        "cancel_requested": bool(snapshot.get("cancel_requested")),
        "actions": [
            {
                "id": action["action_id"],
                "type": action["action_type"],
                "state": action["state"],
            }
            for action in snapshot.get("actions", [])
        ],
    }
