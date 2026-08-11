"""Privacy-safe, best-effort provider usage accounting."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logger import logger
from app.logic.telemetry_metadata import (
    safe_cost_source_label,
    safe_error_category,
    safe_error_category_label,
    safe_operation_label,
    safe_provider_label,
    safe_source_label,
    safe_status_label,
    safe_telemetry_model_label,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
WINDOW_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def _enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


def pseudonymous_owner_scope(owner: str | None) -> str | None:
    normalized = str(owner or "").strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    occurred_at: float
    owner: str | None
    operation: str
    provider: str
    request_model: str
    status: str
    job_id: str | None = None
    workflow_id: str | None = None
    response_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_source: str = "unknown"
    duration_ms: float | None = None
    time_to_first_chunk_ms: float | None = None
    attempt: int = 1
    error_category: str | None = None
    source: str = "provider"


class UsageLedger:
    """Dedicated bounded SQLite ledger. Every public operation is fail-open."""

    def __init__(
        self,
        db_file: str | Path | None = None,
        *,
        enabled: bool | None = None,
        retention_days: int | None = None,
        max_events: int | None = None,
        max_storage_bytes: int | None = None,
        busy_timeout_ms: int = 100,
        write_retries: int = 3,
    ) -> None:
        configured = db_file or os.getenv("USAGE_DB_FILE", ".runtime/usage.db")
        path = Path(configured)
        self.db_file = path if path.is_absolute() else ROOT_DIR / path
        self.enabled = _enabled(os.getenv("HELPER_USAGE_LEDGER_ENABLED"), True) if enabled is None else enabled
        self.retention_days = max(1, retention_days) if retention_days is not None else _positive_int("USAGE_RETENTION_DAYS", 30, 1)
        self.max_events = max(1, max_events) if max_events is not None else _positive_int("USAGE_MAX_EVENTS", 100000, 1)
        self.max_storage_bytes = max(256, max_storage_bytes) if max_storage_bytes is not None else _positive_int("USAGE_MAX_STORAGE_BYTES", 67108864, 4096)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self.write_retries = max(1, int(write_retries))
        self._schema_lock = threading.Lock()
        self._initialized = False
        self._healthy = bool(self.enabled)
        if self.enabled:
            self.initialize()

    def _open(self) -> sqlite3.Connection:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_file, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> bool:
        if not self.enabled:
            return False
        with self._schema_lock:
            if self._initialized:
                return True
            try:
                with closing(self._open()) as db:
                    db.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS usage_schema (
                            version INTEGER PRIMARY KEY
                        );
                        CREATE TABLE IF NOT EXISTS usage_events (
                            event_id TEXT PRIMARY KEY,
                            occurred_at REAL NOT NULL,
                            owner_scope TEXT,
                            job_id TEXT,
                            workflow_id TEXT,
                            operation TEXT NOT NULL,
                            provider TEXT NOT NULL,
                            request_model TEXT NOT NULL,
                            response_model TEXT,
                            status TEXT NOT NULL,
                            input_tokens INTEGER,
                            output_tokens INTEGER,
                            cost_usd REAL,
                            cost_source TEXT NOT NULL,
                            duration_ms REAL,
                            time_to_first_chunk_ms REAL,
                            attempt INTEGER NOT NULL,
                            error_category TEXT,
                            source TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS ix_usage_occurred_at ON usage_events(occurred_at);
                        CREATE INDEX IF NOT EXISTS ix_usage_owner_scope ON usage_events(owner_scope);
                        CREATE INDEX IF NOT EXISTS ix_usage_provider ON usage_events(provider);
                        CREATE INDEX IF NOT EXISTS ix_usage_request_model ON usage_events(request_model);
                        CREATE INDEX IF NOT EXISTS ix_usage_job_id ON usage_events(job_id);
                        CREATE INDEX IF NOT EXISTS ix_usage_workflow_id ON usage_events(workflow_id);
                        """
                    )
                    row = db.execute("SELECT version FROM usage_schema LIMIT 1").fetchone()
                    if row is None:
                        db.execute("INSERT OR IGNORE INTO usage_schema(version) VALUES(?)", (SCHEMA_VERSION,))
                    elif int(row["version"]) != SCHEMA_VERSION:
                        raise RuntimeError("unsupported_usage_schema")
                self._initialized = self._healthy = True
                return True
            except Exception as exc:
                self._healthy = False
                logger.warning("[Telemetry] usage_ledger_init_failed error_type=%s", type(exc).__name__)
                return False

    @staticmethod
    def _event_bytes_sql() -> str:
        columns = (
            "event_id", "owner_scope", "job_id", "workflow_id", "operation", "provider",
            "request_model", "response_model", "status", "cost_source", "error_category", "source",
        )
        return " + ".join(
            f"length(CAST(COALESCE({column},'') AS BLOB))" for column in columns
        ) + " + 96"

    def logical_usage_bytes(self) -> int:
        if not self.enabled or not self._initialized:
            return 0
        try:
            with closing(self._open()) as db:
                return int(db.execute(
                    f"SELECT COALESCE(SUM({self._event_bytes_sql()}),0) FROM usage_events"
                ).fetchone()[0])
        except Exception:
            return 0

    def _prune_locked(self, db: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.retention_days * 86400
        db.execute("DELETE FROM usage_events WHERE occurred_at < ?", (cutoff,))
        count = int(db.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])
        overflow = count - self.max_events
        if overflow > 0:
            db.execute(
                "DELETE FROM usage_events WHERE event_id IN "
                "(SELECT event_id FROM usage_events ORDER BY occurred_at,event_id LIMIT ?)",
                (overflow,),
            )
        usage = int(db.execute(f"SELECT COALESCE(SUM({self._event_bytes_sql()}),0) FROM usage_events").fetchone()[0])
        while usage > self.max_storage_bytes:
            removed = db.execute(
                "DELETE FROM usage_events WHERE event_id IN "
                "(SELECT event_id FROM usage_events ORDER BY occurred_at,event_id LIMIT 1)"
            ).rowcount
            if not removed:
                break
            usage = int(db.execute(f"SELECT COALESCE(SUM({self._event_bytes_sql()}),0) FROM usage_events").fetchone()[0])

    def record(self, event: UsageEvent) -> bool:
        if not self.enabled:
            return False
        if not self._initialized and not self.initialize():
            return False
        try:
            values = (
                event.event_id,
                float(event.occurred_at),
                pseudonymous_owner_scope(event.owner),
                event.job_id,
                event.workflow_id,
                safe_operation_label(event.operation),
                safe_provider_label(event.provider),
                safe_telemetry_model_label(event.request_model, provider=event.provider),
                safe_telemetry_model_label(event.response_model, provider=event.provider) if event.response_model else None,
                safe_status_label(event.status),
                event.input_tokens,
                event.output_tokens,
                event.cost_usd,
                safe_cost_source_label(event.cost_source),
                event.duration_ms,
                event.time_to_first_chunk_ms,
                max(1, int(event.attempt)),
                safe_error_category(event.error_category) if isinstance(event.error_category, BaseException)
                else safe_error_category_label(event.error_category),
                safe_source_label(event.source),
            )
        except Exception as exc:
            self._healthy = False
            logger.warning("[Telemetry] usage_ledger_event_dropped error_type=%s", type(exc).__name__)
            return False
        for retry in range(self.write_retries):
            try:
                with closing(self._open()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "INSERT OR IGNORE INTO usage_events("
                        "event_id,occurred_at,owner_scope,job_id,workflow_id,operation,provider,request_model,"
                        "response_model,status,input_tokens,output_tokens,cost_usd,cost_source,duration_ms,"
                        "time_to_first_chunk_ms,attempt,error_category,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        values,
                    )
                    self._prune_locked(db, time.time())
                    db.commit()
                self._healthy = True
                return True
            except sqlite3.OperationalError as exc:
                self._healthy = False
                if retry + 1 < self.write_retries:
                    time.sleep(0.01 * (retry + 1))
                    continue
                logger.warning("[Telemetry] usage_ledger_write_dropped error_type=%s", type(exc).__name__)
            except Exception as exc:
                self._healthy = False
                logger.warning("[Telemetry] usage_ledger_write_dropped error_type=%s", type(exc).__name__)
                break
        return False

    def summary(self, owner: str, window: str = "7d") -> dict[str, Any]:
        if window not in WINDOW_SECONDS:
            raise ValueError("unsupported_usage_window")
        empty = {
            "window": window,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "known_cost_usd": 0.0,
            "unknown_cost_calls": 0,
            "average_duration_ms": None,
            "providers": [],
        }
        if not self.enabled or not self._initialized:
            return empty
        try:
            cutoff = time.time() - WINDOW_SECONDS[window]
            scope = pseudonymous_owner_scope(owner)
            with closing(self._open()) as db:
                row = db.execute(
                    "SELECT COUNT(*) calls,COALESCE(SUM(input_tokens),0) input_tokens,"
                    "COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(cost_usd),0) known_cost,"
                    "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) unknown_cost_calls,"
                    "AVG(duration_ms) average_duration_ms FROM usage_events "
                    "WHERE owner_scope=? AND occurred_at>=?",
                    (scope, cutoff),
                ).fetchone()
                providers = db.execute(
                    "SELECT provider,COUNT(*) calls FROM usage_events WHERE owner_scope=? AND occurred_at>=? "
                    "GROUP BY provider ORDER BY calls DESC,provider ASC",
                    (scope, cutoff),
                ).fetchall()
            return {
                "window": window,
                "model_calls": int(row["calls"] or 0),
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "known_cost_usd": round(float(row["known_cost"] or 0.0), 8),
                "unknown_cost_calls": int(row["unknown_cost_calls"] or 0),
                "average_duration_ms": round(float(row["average_duration_ms"]), 2) if row["average_duration_ms"] is not None else None,
                "providers": [{"provider": item["provider"], "calls": int(item["calls"])} for item in providers],
            }
        except Exception as exc:
            self._healthy = False
            logger.warning("[Telemetry] usage_ledger_summary_failed error_type=%s", type(exc).__name__)
            return empty

    def count(self) -> int:
        if not self.enabled or not self._initialized:
            return 0
        try:
            with closing(self._open()) as db:
                return int(db.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])
        except Exception:
            return 0

    def healthy(self) -> bool:
        if not self.enabled:
            return False
        try:
            with closing(self._open()) as db:
                db.execute("SELECT version FROM usage_schema LIMIT 1").fetchone()
            self._healthy = True
        except Exception:
            self._healthy = False
        return self._healthy

    def close(self) -> None:
        return None


_ledger_lock = threading.Lock()
_usage_ledger: UsageLedger | None = None


def get_usage_ledger() -> UsageLedger:
    global _usage_ledger
    with _ledger_lock:
        if _usage_ledger is None:
            _usage_ledger = UsageLedger()
        return _usage_ledger


def reset_usage_ledger_for_tests() -> None:
    global _usage_ledger
    with _ledger_lock:
        _usage_ledger = None
