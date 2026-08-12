"""Durable single-host dispatch claims for external mutations.

The outbox stores equality fingerprints and coordination metadata only. It does
not store external-action payloads or authorization credentials and never
dispatches work during initialization or recovery.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from app.logger import logger
from app.logic.capability_policy import CAPABILITY_REGISTRY, CapabilityEffect, CapabilitySource
from app.observability import increment_counter, start_span


ACTION_OUTBOX_SCHEMA_VERSION = 1

PREPARED = "prepared"
CLAIMED = "claimed"
DISPATCHING = "dispatching"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
UNKNOWN_EXTERNAL_RESULT = "unknown_external_result"
EXPIRED = "expired"

TERMINAL_PRUNABLE = frozenset({SUCCEEDED, FAILED, CANCELLED, EXPIRED})
TERMINAL_STATES = frozenset({*TERMINAL_PRUNABLE, UNKNOWN_EXTERNAL_RESULT})
SOURCES = frozenset({"http", "workflow"})
OUTCOMES = frozenset({SUCCEEDED, FAILED, CANCELLED, UNKNOWN_EXTERNAL_RESULT, EXPIRED})
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")
_SAFE_RECEIPT_RE = re.compile(r"^[a-z0-9_.:-]{1,80}$")


class ActionOutboxError(RuntimeError):
    """Base controlled external-action persistence failure."""


class ActionOutboxConflict(ActionOutboxError):
    """An idempotency key was reused with a different payload fingerprint."""


class ActionOutboxCapacityError(ActionOutboxError):
    """Safe terminal pruning could not reclaim enough outbox capacity."""


class ActionOutboxStateError(ActionOutboxError):
    """The requested transition is not valid for the durable state."""


class ActionOutboxStore(Protocol):
    def prepare(self, **kwargs: Any) -> dict[str, Any]: ...
    def get(self, outbox_id: str, owner: str) -> dict[str, Any] | None: ...
    def claim(self, outbox_id: str, owner: str, execution_id: str) -> bool: ...
    def mark_dispatch_started(self, outbox_id: str, owner: str, execution_id: str) -> bool: ...
    def renew_lease(self, outbox_id: str, owner: str, execution_id: str) -> bool: ...
    def mark_succeeded(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool: ...
    def mark_failed(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool: ...
    def mark_unknown(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool: ...
    def cancel(self, outbox_id: str, owner: str) -> bool: ...
    def recover_expired(self) -> int: ...
    def prune(self) -> int: ...


def pseudonymous_owner_scope(owner: object) -> str:
    normalized = str(owner or "").strip().casefold()
    if not normalized:
        raise ActionOutboxError("External action owner is required.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def idempotency_digest(value: object) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or len(cleaned.encode("utf-8")) > 512:
        raise ActionOutboxError("A bounded idempotency key is required.")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def validate_payload_fingerprint(value: object) -> str:
    cleaned = str(value or "").strip().lower()
    if not _FINGERPRINT_RE.fullmatch(cleaned):
        raise ActionOutboxError("A SHA-256 payload fingerprint is required.")
    return cleaned


def _safe_source(value: object) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned not in SOURCES:
        raise ActionOutboxError("Unsupported external action source.")
    return cleaned


def _external_capability(capability_id: object) -> str:
    cleaned = str(capability_id or "").strip()
    spec = CAPABILITY_REGISTRY.get(cleaned)
    if spec is None:
        raise ActionOutboxError("Unknown external action capability.")
    if spec.effect != CapabilityEffect.EXTERNAL_MUTATION:
        raise ActionOutboxError("Capability is not an external mutation.")
    return spec.capability_id


def initialize_action_outbox_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_action_schema(
          id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO external_action_schema(id,version) VALUES(1,1);
        CREATE TABLE IF NOT EXISTS external_action_outbox(
          outbox_id TEXT PRIMARY KEY,
          owner_scope TEXT NOT NULL,
          capability_id TEXT NOT NULL,
          source TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          payload_fingerprint TEXT NOT NULL,
          workflow_id TEXT,
          workflow_action_id TEXT,
          job_id TEXT,
          state TEXT NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          expires_at REAL NOT NULL,
          execution_id TEXT,
          lease_expires_at REAL NOT NULL DEFAULT 0,
          heartbeat_at REAL NOT NULL DEFAULT 0,
          dispatch_started_at REAL,
          completed_at REAL,
          outcome TEXT,
          receipt_reference TEXT,
          error_category TEXT,
          schema_version INTEGER NOT NULL,
          UNIQUE(owner_scope,capability_id,idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS external_action_owner ON external_action_outbox(owner_scope);
        CREATE INDEX IF NOT EXISTS external_action_capability ON external_action_outbox(capability_id);
        CREATE INDEX IF NOT EXISTS external_action_state ON external_action_outbox(state);
        CREATE INDEX IF NOT EXISTS external_action_created ON external_action_outbox(created_at);
        CREATE INDEX IF NOT EXISTS external_action_updated ON external_action_outbox(updated_at);
        CREATE INDEX IF NOT EXISTS external_action_expiry ON external_action_outbox(expires_at);
        CREATE INDEX IF NOT EXISTS external_action_workflow ON external_action_outbox(workflow_id);
        CREATE INDEX IF NOT EXISTS external_action_workflow_action
          ON external_action_outbox(workflow_id,workflow_action_id);
        """
    )
    row = db.execute("SELECT version FROM external_action_schema WHERE id=1").fetchone()
    if not row or int(row[0]) != ACTION_OUTBOX_SCHEMA_VERSION:
        raise ActionOutboxError("Unsupported external action outbox schema version.")


class SQLiteActionOutboxStore:
    """SQLite-WAL dispatch ledger shared by local FastAPI workers."""

    _LOGICAL_BYTES_SQL = " + ".join(
        f"length(CAST(COALESCE({column},'') AS BLOB))"
        for column in (
            "outbox_id", "owner_scope", "capability_id", "source", "idempotency_key",
            "payload_fingerprint", "workflow_id", "workflow_action_id", "job_id", "state",
            "execution_id", "outcome", "receipt_reference", "error_category",
        )
    ) + " + 512"

    def __init__(
        self,
        db_file: str | Path | None = None,
        *,
        retention_seconds: int | None = None,
        lease_seconds: float | None = None,
        lease_renew_seconds: float | None = None,
        max_retained: int | None = None,
        max_storage_bytes: int | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        configured = db_file or os.getenv("WORKFLOW_DB_FILE") or root / ".runtime" / "workflows.db"
        path = Path(configured)
        self.db_file = path if path.is_absolute() else root / path
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = max(60, int(
            retention_seconds if retention_seconds is not None
            else os.getenv("ACTION_OUTBOX_RETENTION_SECONDS", "2592000")
        ))
        self.lease_seconds = max(0.2, float(
            lease_seconds if lease_seconds is not None
            else os.getenv("ACTION_OUTBOX_LEASE_SECONDS", "30")
        ))
        configured_renew = float(
            lease_renew_seconds if lease_renew_seconds is not None
            else os.getenv("ACTION_OUTBOX_LEASE_RENEW_SECONDS", "5")
        )
        self.lease_renew_seconds = max(0.05, min(configured_renew, self.lease_seconds / 2))
        self.max_retained = max(1, int(
            max_retained if max_retained is not None
            else os.getenv("ACTION_OUTBOX_MAX_RETAINED", "10000")
        ))
        self.max_storage_bytes = max(1024, int(
            max_storage_bytes if max_storage_bytes is not None
            else os.getenv("ACTION_OUTBOX_MAX_STORAGE_BYTES", str(64 * 1024 * 1024))
        ))
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        for attempt in range(8):
            db = sqlite3.connect(
                self.db_file, timeout=10, isolation_level=None, check_same_thread=False,
            )
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA busy_timeout=10000")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("PRAGMA foreign_keys=ON")
                return db
            except sqlite3.OperationalError as exc:
                db.close()
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise ActionOutboxError("External action database connection could not be initialized.")

    @contextmanager
    def _open(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                initialize_action_outbox_schema(db)
                self._recover_expired_locked(db, time.time())
                self._prune_locked(db, time.time())
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _emit_transition(record: dict[str, Any], state: str, *, prepared: bool = False) -> None:
        if prepared:
            increment_counter("helper.external_action.prepared", {
                "capability": record["capability_id"], "source": record["source"], "state": state,
            })
        if state in {SUCCEEDED, FAILED}:
            increment_counter("helper.external_action.dispatches", {
                "capability": record["capability_id"], "source": record["source"], "outcome": state,
            })
        elif state == UNKNOWN_EXTERNAL_RESULT:
            increment_counter("helper.external_action.unknown_results", {
                "capability": record["capability_id"], "source": record["source"], "outcome": state,
            })
        logger.info(
            "[ActionTrace] capability=%s state=%s source=%s",
            record["capability_id"], state, record["source"],
        )

    def _logical_usage_locked(self, db: sqlite3.Connection) -> int:
        return int(db.execute(
            f"SELECT COALESCE(SUM({self._LOGICAL_BYTES_SQL}),0) FROM external_action_outbox"
        ).fetchone()[0])

    def logical_usage_bytes(self) -> int:
        with self._open() as db:
            return self._logical_usage_locked(db)

    def _recover_expired_locked(self, db: sqlite3.Connection, now: float) -> int:
        reclaimed = db.execute(
            "UPDATE external_action_outbox SET state=?,execution_id=NULL,lease_expires_at=0,"
            "heartbeat_at=?,updated_at=? WHERE state=? AND dispatch_started_at IS NULL "
            "AND lease_expires_at>0 AND lease_expires_at<=?",
            (PREPARED, now, now, CLAIMED, now),
        ).rowcount
        uncertain = db.execute(
            "UPDATE external_action_outbox SET state=?,outcome=?,error_category=?,completed_at=?,"
            "execution_id=NULL,lease_expires_at=0,heartbeat_at=?,updated_at=?,expires_at=? "
            "WHERE state=? AND lease_expires_at>0 AND lease_expires_at<=?",
            (
                UNKNOWN_EXTERNAL_RESULT, UNKNOWN_EXTERNAL_RESULT, "dispatch_lease_expired",
                now, now, now, now + self.retention_seconds, DISPATCHING, now,
            ),
        ).rowcount
        return max(0, reclaimed) + max(0, uncertain)

    def _prune_locked(self, db: sqlite3.Connection, now: float) -> int:
        changed = self._recover_expired_locked(db, now)
        placeholders = ",".join("?" for _ in TERMINAL_PRUNABLE)
        params = (*sorted(TERMINAL_PRUNABLE), now)
        changed += max(0, db.execute(
            f"DELETE FROM external_action_outbox WHERE state IN ({placeholders}) AND expires_at<=?",
            params,
        ).rowcount)
        terminal = db.execute(
            f"SELECT outbox_id FROM external_action_outbox WHERE state IN ({placeholders}) "
            "ORDER BY updated_at DESC,outbox_id DESC",
            tuple(sorted(TERMINAL_PRUNABLE)),
        ).fetchall()
        for row in terminal[self.max_retained :]:
            changed += max(0, db.execute(
                "DELETE FROM external_action_outbox WHERE outbox_id=?", (row["outbox_id"],)
            ).rowcount)
        while self._logical_usage_locked(db) > self.max_storage_bytes:
            victim = db.execute(
                f"SELECT outbox_id FROM external_action_outbox WHERE state IN ({placeholders}) "
                "ORDER BY updated_at,outbox_id LIMIT 1",
                tuple(sorted(TERMINAL_PRUNABLE)),
            ).fetchone()
            if not victim:
                break
            changed += max(0, db.execute(
                "DELETE FROM external_action_outbox WHERE outbox_id=?", (victim["outbox_id"],)
            ).rowcount)
        return changed

    def _check_capacity_locked(self, db: sqlite3.Connection) -> None:
        if self._logical_usage_locked(db) > self.max_storage_bytes:
            raise ActionOutboxCapacityError("External action storage is temporarily full.")

    def _prepare_locked(
        self,
        db: sqlite3.Connection,
        *,
        owner: str,
        capability_id: str,
        source: str,
        idempotency_key: str,
        payload_fingerprint: str,
        workflow_id: str | None = None,
        workflow_action_id: str | None = None,
        job_id: str | None = None,
        now: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        current = time.time() if now is None else now
        owner_scope = pseudonymous_owner_scope(owner)
        capability = _external_capability(capability_id)
        safe_source = _safe_source(source)
        source_enum = CapabilitySource.HTTP if safe_source == "http" else CapabilitySource.WORKFLOW
        if source_enum not in CAPABILITY_REGISTRY.get(capability).allowed_sources:
            raise ActionOutboxError("External action source is not allowed for this capability.")
        key_digest = idempotency_digest(idempotency_key)
        fingerprint = validate_payload_fingerprint(payload_fingerprint)
        self._prune_locked(db, current)
        existing = db.execute(
            "SELECT * FROM external_action_outbox WHERE owner_scope=? AND capability_id=? AND idempotency_key=?",
            (owner_scope, capability, key_digest),
        ).fetchone()
        if existing:
            if str(existing["payload_fingerprint"]) != fingerprint:
                raise ActionOutboxConflict("Idempotency key conflicts with an existing external action.")
            return dict(existing), False
        outbox_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO external_action_outbox(outbox_id,owner_scope,capability_id,source,idempotency_key,"
            "payload_fingerprint,workflow_id,workflow_action_id,job_id,state,created_at,updated_at,expires_at,"
            "schema_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                outbox_id, owner_scope, capability, safe_source, key_digest, fingerprint,
                str(workflow_id or "")[:120] or None,
                str(workflow_action_id or "")[:120] or None,
                str(job_id or "")[:120] or None,
                PREPARED, current, current, current + self.retention_seconds,
                ACTION_OUTBOX_SCHEMA_VERSION,
            ),
        )
        self._prune_locked(db, current)
        self._check_capacity_locked(db)
        row = db.execute("SELECT * FROM external_action_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
        if not row:
            raise ActionOutboxCapacityError("External action storage could not retain the new intent.")
        return dict(row), True

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        with start_span("helper.external_action.prepare", {
            "helper.external_action.capability": "email.deliver",
            "helper.external_action.source": str(kwargs.get("source") or "unknown"),
        }) as span:
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    record, created = self._prepare_locked(db, **kwargs)
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
            if span is not None:
                span.set_attribute("helper.external_action.id", record["outbox_id"])
            if created:
                self._emit_transition(record, PREPARED, prepared=True)
            return record

    def record_legacy_success(self, **kwargs: Any) -> dict[str, Any]:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                record, _ = self._prepare_locked(db, **kwargs)
                if record["state"] != SUCCEEDED:
                    now = time.time()
                    db.execute(
                        "UPDATE external_action_outbox SET state=?,outcome=?,receipt_reference=?,completed_at=?,"
                        "updated_at=?,expires_at=?,execution_id=NULL,lease_expires_at=0,heartbeat_at=? "
                        "WHERE outbox_id=? AND state!=?",
                        (
                            SUCCEEDED, SUCCEEDED, "legacy", now, now,
                            now + self.retention_seconds, now, record["outbox_id"], SUCCEEDED,
                        ),
                    )
                    record = dict(db.execute(
                        "SELECT * FROM external_action_outbox WHERE outbox_id=?", (record["outbox_id"],)
                    ).fetchone())
                db.commit()
                return record
            except Exception:
                db.rollback()
                raise

    def get(self, outbox_id: str, owner: str) -> dict[str, Any] | None:
        with self._open() as db:
            return self._record(db.execute(
                "SELECT * FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
                (outbox_id, pseudonymous_owner_scope(owner)),
            ).fetchone())

    def claim(self, outbox_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired_locked(db, now)
                row = db.execute(
                    "SELECT capability_id,source FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
                    (outbox_id, pseudonymous_owner_scope(owner)),
                ).fetchone()
                changed = db.execute(
                    "UPDATE external_action_outbox SET state=?,attempt=attempt+1,execution_id=?,"
                    "lease_expires_at=?,heartbeat_at=?,updated_at=? WHERE outbox_id=? AND owner_scope=? "
                    "AND state=? AND dispatch_started_at IS NULL",
                    (
                        CLAIMED, execution_id, now + self.lease_seconds, now, now,
                        outbox_id, pseudonymous_owner_scope(owner), PREPARED,
                    ),
                ).rowcount == 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        if changed:
            self._emit_transition(dict(row), CLAIMED)
        return changed

    def mark_dispatch_started(self, outbox_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT capability_id,source FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
                    (outbox_id, pseudonymous_owner_scope(owner)),
                ).fetchone()
                changed = db.execute(
                    "UPDATE external_action_outbox SET state=?,dispatch_started_at=?,updated_at=?,heartbeat_at=? "
                    "WHERE outbox_id=? AND owner_scope=? AND state=? AND execution_id=? AND lease_expires_at>?",
                    (
                        DISPATCHING, now, now, now, outbox_id, pseudonymous_owner_scope(owner),
                        CLAIMED, execution_id, now,
                    ),
                ).rowcount == 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        if changed:
            self._emit_transition(dict(row), DISPATCHING)
        return changed

    def renew_lease(self, outbox_id: str, owner: str, execution_id: str) -> bool:
        now = time.time()
        with self._open() as db:
            return db.execute(
                "UPDATE external_action_outbox SET lease_expires_at=?,heartbeat_at=?,updated_at=? "
                "WHERE outbox_id=? AND owner_scope=? AND state IN (?,?) AND execution_id=? AND lease_expires_at>?",
                (
                    now + self.lease_seconds, now, now, outbox_id, pseudonymous_owner_scope(owner),
                    CLAIMED, DISPATCHING, execution_id, now,
                ),
            ).rowcount == 1

    def _mark_terminal_locked(
        self,
        db: sqlite3.Connection,
        *,
        outbox_id: str,
        owner: str,
        execution_id: str | None,
        state: str,
        receipt_reference: str | None = None,
        error_category: str | None = None,
        allow_existing: bool = False,
    ) -> bool:
        if state not in {SUCCEEDED, FAILED, UNKNOWN_EXTERNAL_RESULT}:
            raise ActionOutboxStateError("Unsupported external action terminal state.")
        now = time.time()
        row = db.execute(
            "SELECT * FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
            (outbox_id, pseudonymous_owner_scope(owner)),
        ).fetchone()
        if not row:
            return False
        if allow_existing and row["state"] == state:
            return True
        if row["state"] != DISPATCHING or row["execution_id"] != execution_id or float(row["lease_expires_at"] or 0) <= now:
            return False
        safe_receipt = str(receipt_reference or "").strip().lower()
        if safe_receipt and not _SAFE_RECEIPT_RE.fullmatch(safe_receipt):
            safe_receipt = "confirmed"
        safe_error = str(error_category or "").strip().lower()
        if safe_error not in {
            "validation_failed", "provider_rejected", "transport_ambiguous", "dispatch_lease_expired",
            "storage_failure", "cancelled_after_dispatch", "unknown",
        }:
            safe_error = "unknown" if safe_error else None
        changed = db.execute(
            "UPDATE external_action_outbox SET state=?,outcome=?,receipt_reference=?,error_category=?,"
            "completed_at=?,updated_at=?,expires_at=?,execution_id=NULL,lease_expires_at=0,heartbeat_at=? "
            "WHERE outbox_id=? AND owner_scope=? AND state=? AND execution_id=?",
            (
                state, state, safe_receipt or None, safe_error, now, now,
                now + self.retention_seconds, now, outbox_id, pseudonymous_owner_scope(owner),
                DISPATCHING, execution_id,
            ),
        ).rowcount == 1
        if changed:
            self._prune_locked(db, now)
            self._check_capacity_locked(db)
        return changed

    def _mark_terminal(self, outbox_id: str, owner: str, execution_id: str, state: str, **kwargs: Any) -> bool:
        record: sqlite3.Row | None = None
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                record = db.execute(
                    "SELECT capability_id,source FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
                    (outbox_id, pseudonymous_owner_scope(owner)),
                ).fetchone()
                changed = self._mark_terminal_locked(
                    db, outbox_id=outbox_id, owner=owner, execution_id=execution_id,
                    state=state, **kwargs,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        if changed and record:
            self._emit_transition(dict(record), state)
        return changed

    def mark_succeeded(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool:
        return self._mark_terminal(outbox_id, owner, execution_id, SUCCEEDED, **kwargs)

    def mark_failed(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool:
        return self._mark_terminal(outbox_id, owner, execution_id, FAILED, **kwargs)

    def mark_unknown(self, outbox_id: str, owner: str, execution_id: str, **kwargs: Any) -> bool:
        return self._mark_terminal(outbox_id, owner, execution_id, UNKNOWN_EXTERNAL_RESULT, **kwargs)

    def _cancel_locked(
        self,
        db: sqlite3.Connection,
        *,
        outbox_id: str,
        owner: str,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        current = time.time() if now is None else now
        owner_scope = pseudonymous_owner_scope(owner)
        row = db.execute(
            "SELECT state FROM external_action_outbox WHERE outbox_id=? AND owner_scope=?",
            (outbox_id, owner_scope),
        ).fetchone()
        if not row:
            return False, None
        if row["state"] == SUCCEEDED:
            return False, SUCCEEDED
        if row["state"] in TERMINAL_STATES:
            return row["state"] == CANCELLED, str(row["state"])
        next_state = UNKNOWN_EXTERNAL_RESULT if row["state"] == DISPATCHING else CANCELLED
        error = "cancelled_after_dispatch" if next_state == UNKNOWN_EXTERNAL_RESULT else None
        db.execute(
            "UPDATE external_action_outbox SET state=?,outcome=?,error_category=?,completed_at=?,"
            "updated_at=?,expires_at=?,execution_id=NULL,lease_expires_at=0,heartbeat_at=? "
            "WHERE outbox_id=? AND owner_scope=?",
            (
                next_state, next_state, error, current, current,
                current + self.retention_seconds, current, outbox_id, owner_scope,
            ),
        )
        return True, next_state

    def cancel(self, outbox_id: str, owner: str) -> bool:
        now = time.time()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed, _ = self._cancel_locked(
                    db, outbox_id=outbox_id, owner=owner, now=now,
                )
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def recover_expired(self) -> int:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._recover_expired_locked(db, time.time())
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def prune(self) -> int:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._prune_locked(db, time.time())
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise
