"""Durable, owner-scoped confirmation for one exact external action."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from app.logger import logger
from app.logic.action_outbox import pseudonymous_owner_scope
from app.logic.capability_policy import (
    CAPABILITY_POLICY_VERSION,
    CAPABILITY_REGISTRY,
    ApprovalRequirement,
    CapabilityEffect,
    CapabilityRisk,
    CapabilitySource,
    PolicyDecision,
    PolicyDecisionType,
)
from app.observability import increment_counter, start_span


HUMAN_APPROVAL_SCHEMA_VERSION = 1
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CLAIMED = "claimed"
CONSUMED = "consumed"
CANCELLED = "cancelled"
EXPIRED = "expired"
SUPERSEDED = "superseded"
ACTIVE_STATES = frozenset({PENDING, APPROVED, CLAIMED})
TERMINAL_STATES = frozenset({REJECTED, CONSUMED, CANCELLED, EXPIRED, SUPERSEDED})
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES

MAX_TITLE_CHARS = 120
MAX_SUMMARY_CHARS = 500
MAX_DETAIL_FIELDS = 12
MAX_FIELD_LABEL_CHARS = 80
MAX_FIELD_VALUE_CHARS = 500
MAX_MANIFEST_BYTES = 8 * 1024
MAX_CANONICAL_ACTION_BYTES = 64 * 1024
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")
_LINK_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_SECRET_RE = re.compile(
    r"(?i)(?:\bauthorization\s*:|\bbearer\s+[A-Za-z0-9._~+/-]{8,}|"
    r"\b(?:api[_ -]?key|admin[_ -]?key|smtp[_ -]?(?:password|pwd)|password)\s*[:=]|"
    r"\bcookie\s*:|data:[^,]{0,80};base64,)"
)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{120,}={0,2}$")
_LOGICAL_BYTES_SQL = " + ".join(
    f"length(CAST(COALESCE({column},'') AS BLOB))"
    for column in (
        "approval_id", "owner_scope", "capability_id", "source", "request_key_digest",
        "payload_fingerprint", "status", "risk_snapshot", "workflow_id",
        "workflow_action_id", "job_id", "display_json", "claim_id",
    )
) + " + 80 + 128"


class ApprovalStatus(str, Enum):
    PENDING = PENDING
    APPROVED = APPROVED
    REJECTED = REJECTED
    CLAIMED = CLAIMED
    CONSUMED = CONSUMED
    CANCELLED = CANCELLED
    EXPIRED = EXPIRED
    SUPERSEDED = SUPERSEDED


class HumanApprovalError(RuntimeError):
    """Base controlled approval-store failure."""


class HumanApprovalConflict(HumanApprovalError):
    """A decision or request no longer matches current durable state."""


class HumanApprovalCapacityError(HumanApprovalError):
    """Safe terminal pruning could not make room for an approval request."""


class HumanApprovalValidationError(HumanApprovalError):
    """Trusted action metadata did not satisfy the approval contract."""


@dataclass(frozen=True)
class ApprovalDisplayField:
    label: str
    value: str


@dataclass(frozen=True)
class ApprovalManifest:
    """Owner-visible preview data; never an authorization input."""

    title: str
    summary: str
    details: tuple[ApprovalDisplayField, ...] = ()


@dataclass(frozen=True)
class ApprovalRequestMaterial:
    """Trusted action-specific fingerprint and owner-visible preview."""

    payload_fingerprint: str
    manifest: ApprovalManifest


class HumanApprovalRequestBuilder(Protocol):
    """Code-owned serializer for one action type's exact canonical payload."""

    def build(
        self,
        *,
        action: Any,
        plan: Any,
        results: Mapping[str, Any],
        draft: Any,
    ) -> ApprovalRequestMaterial: ...


@dataclass(frozen=True, slots=True)
class HumanApprovalGrant:
    """Transient claim proof. It is deliberately not serializable or public."""

    approval_id: str = field(repr=False)
    owner: str = field(repr=False)
    capability_id: str = field(repr=False)
    payload_fingerprint: str = field(repr=False)
    request_key_digest: str = field(repr=False)
    claim_id: str = field(repr=False)
    expires_at: float = field(repr=False)
    _store: "SQLiteHumanApprovalStore" = field(repr=False, compare=False)

    def is_valid_for(
        self,
        *,
        owner: str | None,
        capability_id: str,
        payload_fingerprint: str | None,
        request_key: str | None,
    ) -> bool:
        if not owner or not request_key or not payload_fingerprint:
            return False
        try:
            matches = (
                pseudonymous_owner_scope(owner) == pseudonymous_owner_scope(self.owner)
                and self.capability_id == capability_id
                and self.payload_fingerprint == validate_payload_fingerprint(payload_fingerprint)
                and self.request_key_digest == request_key_digest(request_key)
            )
        except (HumanApprovalError, ValueError):
            return False
        return matches and self._store._grant_is_current(self)


class HumanApprovalStore(Protocol):
    def request(self, **kwargs: Any) -> dict[str, Any]: ...
    def get(self, approval_id: str, owner: str) -> dict[str, Any] | None: ...
    def list_for_owner(self, owner: str, *, limit: int = 50) -> list[dict[str, Any]]: ...
    def approve(self, approval_id: str, owner: str, expected_version: int) -> dict[str, Any] | None: ...
    def reject(self, approval_id: str, owner: str, expected_version: int) -> dict[str, Any] | None: ...
    def claim(self, approval_id: str, owner: str, **kwargs: Any) -> HumanApprovalGrant | None: ...
    def renew_claim(self, grant: HumanApprovalGrant) -> HumanApprovalGrant | None: ...
    def release_claim(self, grant: HumanApprovalGrant) -> bool: ...
    def consume(self, grant: HumanApprovalGrant) -> bool: ...
    def cancel(self, approval_id: str, owner: str) -> bool: ...
    def recover_expired(self) -> int: ...
    def prune(self) -> int: ...


def validate_payload_fingerprint(value: object) -> str:
    fingerprint = str(value or "").strip().lower()
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise HumanApprovalValidationError("A SHA-256 action fingerprint is required.")
    return fingerprint


def canonical_action_fingerprint(canonical_payload: Mapping[str, Any]) -> str:
    """Hash JSON-safe canonical data returned by a trusted action serializer."""
    if not isinstance(canonical_payload, Mapping) or not canonical_payload:
        raise HumanApprovalValidationError("A canonical action payload is required.")
    try:
        encoded = json.dumps(
            dict(canonical_payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise HumanApprovalValidationError("Canonical action data must be bounded JSON.") from exc
    if len(encoded) > MAX_CANONICAL_ACTION_BYTES:
        raise HumanApprovalValidationError("Canonical action data is too large.")
    return hashlib.sha256(encoded).hexdigest()


def request_key_digest(value: object) -> str:
    key = str(value or "").strip()
    if not key or len(key.encode("utf-8")) > 512:
        raise HumanApprovalValidationError("A bounded application request key is required.")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _bounded_link(value: object | None, label: str) -> str | None:
    if value is None or value == "":
        return None
    candidate = str(value)
    if not _LINK_RE.fullmatch(candidate):
        raise HumanApprovalValidationError(f"Invalid {label} reference.")
    return candidate


def _display_text(value: object, *, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise HumanApprovalValidationError("Approval display data is invalid.")
    text = value.strip()
    if not text or len(text) > maximum or "\x00" in text:
        raise HumanApprovalValidationError("Approval display data is outside its limit.")
    if _SECRET_RE.search(text) or _BASE64_RE.fullmatch(text):
        raise HumanApprovalValidationError("Approval display data contains a prohibited value.")
    if label == "field label" and re.search(r"(?i)api|admin|authorization|cookie|password|token|secret", text):
        raise HumanApprovalValidationError("Approval display field label is prohibited.")
    return text


def normalize_manifest(manifest: ApprovalManifest) -> tuple[ApprovalManifest, str]:
    if not isinstance(manifest, ApprovalManifest):
        raise HumanApprovalValidationError("A code-built approval manifest is required.")
    title = _display_text(manifest.title, maximum=MAX_TITLE_CHARS, label="title")
    summary = _display_text(manifest.summary, maximum=MAX_SUMMARY_CHARS, label="summary")
    details = tuple(manifest.details or ())
    if len(details) > MAX_DETAIL_FIELDS:
        raise HumanApprovalValidationError("Approval display has too many fields.")
    normalized_details: list[ApprovalDisplayField] = []
    for item in details:
        if not isinstance(item, ApprovalDisplayField):
            raise HumanApprovalValidationError("Approval display field is invalid.")
        normalized_details.append(ApprovalDisplayField(
            _display_text(item.label, maximum=MAX_FIELD_LABEL_CHARS, label="field label"),
            _display_text(item.value, maximum=MAX_FIELD_VALUE_CHARS, label="field value"),
        ))
    normalized = ApprovalManifest(title, summary, tuple(normalized_details))
    encoded = json.dumps(
        {
            "title": normalized.title,
            "summary": normalized.summary,
            "details": [
                {"label": item.label, "value": item.value}
                for item in normalized.details
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise HumanApprovalValidationError("Approval display data is too large.")
    return normalized, encoded


def initialize_human_approval_schema(db: sqlite3.Connection) -> None:
    """Idempotently initialize schema under the caller's SQLite write lock."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS human_approval_schema("
        "id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)"
    )
    db.execute(
        "INSERT OR IGNORE INTO human_approval_schema(id,version) VALUES(1,?)",
        (HUMAN_APPROVAL_SCHEMA_VERSION,),
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS human_approvals(
          approval_id TEXT PRIMARY KEY,
          owner_scope TEXT NOT NULL,
          capability_id TEXT NOT NULL,
          source TEXT NOT NULL,
          request_key_digest TEXT NOT NULL,
          payload_fingerprint TEXT NOT NULL,
          status TEXT NOT NULL,
          risk_snapshot TEXT NOT NULL,
          policy_version INTEGER NOT NULL,
          workflow_id TEXT,
          workflow_action_id TEXT,
          job_id TEXT,
          display_json TEXT NOT NULL,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          expires_at REAL NOT NULL,
          decision_at REAL,
          claim_id TEXT,
          claim_expires_at REAL,
          claimed_at REAL,
          consumed_at REAL,
          version INTEGER NOT NULL,
          schema_version INTEGER NOT NULL
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS human_approvals_owner_created "
        "ON human_approvals(owner_scope,created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS human_approvals_request "
        "ON human_approvals(owner_scope,capability_id,request_key_digest,created_at DESC)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS human_approvals_status ON human_approvals(status,expires_at)")
    row = db.execute("SELECT version FROM human_approval_schema WHERE id=1").fetchone()
    if not row or int(row[0]) != HUMAN_APPROVAL_SCHEMA_VERSION:
        raise HumanApprovalError("Unsupported human approval schema version.")


class SQLiteHumanApprovalStore:
    """Durable single-host approval state in the shared workflow database."""

    def __init__(
        self,
        db_file: str | Path | None = None,
        *,
        approval_ttl_seconds: int | None = None,
        claim_seconds: int | None = None,
        terminal_retention_seconds: int | None = None,
        max_retained: int | None = None,
        max_storage_bytes: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        configured = db_file or os.getenv("WORKFLOW_DB_FILE") or root / ".runtime" / "workflows.db"
        path = Path(configured)
        self.db_file = path if path.is_absolute() else root / path
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.approval_ttl_seconds = _bounded_env_int(
            approval_ttl_seconds if approval_ttl_seconds is not None else os.getenv("HUMAN_APPROVAL_TTL_SECONDS"),
            900, 60, 86_400,
        )
        self.claim_seconds = _bounded_env_int(
            claim_seconds if claim_seconds is not None else os.getenv("HUMAN_APPROVAL_CLAIM_SECONDS"),
            30, 1, 300,
        )
        self.terminal_retention_seconds = _bounded_env_int(
            terminal_retention_seconds if terminal_retention_seconds is not None else os.getenv("HUMAN_APPROVAL_RETENTION_SECONDS"),
            30 * 24 * 60 * 60, 60, 365 * 24 * 60 * 60,
        )
        self.max_retained = _bounded_env_int(
            max_retained if max_retained is not None else os.getenv("HUMAN_APPROVAL_MAX_RETAINED"),
            10_000, 1, 100_000,
        )
        self.max_storage_bytes = _bounded_env_int(
            max_storage_bytes if max_storage_bytes is not None else os.getenv("HUMAN_APPROVAL_MAX_STORAGE_BYTES"),
            32 * 1024 * 1024, 4096, 1024 * 1024 * 1024,
        )
        self._clock = clock
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        for attempt in range(8):
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
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise HumanApprovalError("Approval database could not be initialized.")

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
                initialize_human_approval_schema(db)
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _record(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        record = dict(row)
        try:
            parsed = json.loads(record["display_json"])
            record["manifest"] = parsed if isinstance(parsed, dict) else {}
        except (KeyError, TypeError, ValueError):
            record["manifest"] = {}
        return record

    def _logical_usage_locked(self, db: sqlite3.Connection) -> int:
        return int(db.execute(
            f"SELECT COALESCE(SUM({_LOGICAL_BYTES_SQL}),0) FROM human_approvals"
        ).fetchone()[0])

    def logical_usage_bytes(self) -> int:
        with self._open() as db:
            return self._logical_usage_locked(db)

    def _reconcile_locked(self, db: sqlite3.Connection, now: float) -> list[tuple[str, str, str]]:
        rows = db.execute(
            "SELECT approval_id,capability_id,source,status,expires_at,claim_expires_at "
            "FROM human_approvals WHERE status IN (?,?,?)",
            (PENDING, APPROVED, CLAIMED),
        ).fetchall()
        changed: list[tuple[str, str, str]] = []
        for row in rows:
            status = str(row["status"])
            approval_expired = float(row["expires_at"]) <= now
            claim_expired = (
                status == CLAIMED
                and float(row["claim_expires_at"] or 0) <= now
            )
            if status == CLAIMED and claim_expired and not approval_expired:
                next_status = APPROVED
            elif approval_expired:
                next_status = EXPIRED
            else:
                continue
            db.execute(
                "UPDATE human_approvals SET status=?,updated_at=?,claim_id=NULL,"
                "claim_expires_at=NULL,claimed_at=NULL,version=version+1 "
                "WHERE approval_id=? AND status=?",
                (next_status, now, row["approval_id"], status),
            )
            changed.append((str(row["capability_id"]), str(row["source"]), next_status))
        return changed

    def _prune_locked(self, db: sqlite3.Connection, now: float) -> int:
        deleted = db.execute(
            "DELETE FROM human_approvals WHERE status IN (?,?,?,?,?) AND updated_at<=?",
            (
                REJECTED, CONSUMED, CANCELLED, EXPIRED, SUPERSEDED,
                now - self.terminal_retention_seconds,
            ),
        ).rowcount
        count = int(db.execute("SELECT COUNT(*) FROM human_approvals").fetchone()[0])
        excess = max(0, count - self.max_retained)
        while excess:
            victim = db.execute(
                "SELECT approval_id FROM human_approvals WHERE status IN (?,?,?,?,?) "
                "ORDER BY updated_at,approval_id LIMIT 1",
                (REJECTED, CONSUMED, CANCELLED, EXPIRED, SUPERSEDED),
            ).fetchone()
            if not victim:
                break
            deleted += db.execute(
                "DELETE FROM human_approvals WHERE approval_id=?", (victim["approval_id"],)
            ).rowcount
            excess -= 1
        while self._logical_usage_locked(db) > self.max_storage_bytes:
            victim = db.execute(
                "SELECT approval_id FROM human_approvals WHERE status IN (?,?,?,?,?) "
                "ORDER BY updated_at,approval_id LIMIT 1",
                (REJECTED, CONSUMED, CANCELLED, EXPIRED, SUPERSEDED),
            ).fetchone()
            if not victim:
                break
            deleted += db.execute(
                "DELETE FROM human_approvals WHERE approval_id=?", (victim["approval_id"],)
            ).rowcount
        return max(0, deleted)

    def _check_capacity_locked(self, db: sqlite3.Connection) -> None:
        count = int(db.execute("SELECT COUNT(*) FROM human_approvals").fetchone()[0])
        if count > self.max_retained or self._logical_usage_locked(db) > self.max_storage_bytes:
            raise HumanApprovalCapacityError("Approval storage is temporarily full.")

    @staticmethod
    def _validate_request(
        *,
        owner: str,
        capability_id: str,
        source: CapabilitySource,
        request_key: str,
        payload_fingerprint: str,
        manifest: ApprovalManifest,
        decision: PolicyDecision,
        workflow_id: str | None = None,
        workflow_action_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        clean_owner = str(owner or "").strip()
        if not clean_owner:
            raise HumanApprovalValidationError("Approval owner is required.")
        try:
            source_enum = source if isinstance(source, CapabilitySource) else CapabilitySource(str(source))
        except ValueError as exc:
            raise HumanApprovalValidationError("Approval source is invalid.") from exc
        capability = CAPABILITY_REGISTRY.get(str(capability_id or ""))
        if (
            capability is None
            or capability.effect != CapabilityEffect.EXTERNAL_MUTATION
            or capability.approval != ApprovalRequirement.EXPLICIT_CONFIRMATION
            or source_enum not in capability.allowed_sources
        ):
            raise HumanApprovalValidationError("Capability does not allow explicit confirmation.")
        if (
            not isinstance(decision, PolicyDecision)
            or decision.decision != PolicyDecisionType.REQUIRE_APPROVAL
            or decision.capability is None
            or decision.capability.capability_id != capability.capability_id
        ):
            raise HumanApprovalValidationError("A matching policy approval decision is required.")
        _normalized_manifest, display_json = normalize_manifest(manifest)
        key_digest = request_key_digest(request_key)
        return {
            "owner": clean_owner,
            "owner_scope": pseudonymous_owner_scope(clean_owner),
            "capability_id": capability.capability_id,
            "source": source_enum.value,
            "request_key_digest": key_digest,
            "payload_fingerprint": validate_payload_fingerprint(payload_fingerprint),
            "risk_snapshot": capability.risk.value,
            "policy_version": CAPABILITY_POLICY_VERSION,
            "workflow_id": _bounded_link(workflow_id, "workflow"),
            "workflow_action_id": _bounded_link(workflow_action_id, "workflow action"),
            "job_id": _bounded_link(job_id, "job"),
            "display_json": display_json,
        }

    def _request_locked(self, db: sqlite3.Connection, *, now: float | None = None, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        current = self._clock() if now is None else now
        values = self._validate_request(**kwargs)
        self._reconcile_locked(db, current)
        self._prune_locked(db, current)
        existing = db.execute(
            "SELECT * FROM human_approvals WHERE owner_scope=? AND capability_id=? "
            "AND request_key_digest=? ORDER BY created_at DESC,rowid DESC",
            (values["owner_scope"], values["capability_id"], values["request_key_digest"]),
        ).fetchall()
        active = [row for row in existing if str(row["status"]) in ACTIVE_STATES]
        same_fingerprint = next(
            (row for row in active if row["payload_fingerprint"] == values["payload_fingerprint"]),
            None,
        )
        if same_fingerprint:
            record = self._record(same_fingerprint)
            assert record is not None
            return record, False
        for row in active:
            db.execute(
                "UPDATE human_approvals SET status=?,updated_at=?,claim_id=NULL,"
                "claim_expires_at=NULL,claimed_at=NULL,version=version+1 "
                "WHERE approval_id=? AND status IN (?,?,?)",
                (SUPERSEDED, current, row["approval_id"], PENDING, APPROVED, CLAIMED),
            )
        terminal_match = next(
            (
                row for row in existing
                if row["payload_fingerprint"] == values["payload_fingerprint"]
                and row["status"] in {REJECTED, CONSUMED, CANCELLED}
            ),
            None,
        )
        if terminal_match:
            record = self._record(terminal_match)
            assert record is not None
            return record, False

        approval_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO human_approvals(approval_id,owner_scope,capability_id,source,request_key_digest,"
            "payload_fingerprint,status,risk_snapshot,policy_version,workflow_id,workflow_action_id,job_id,"
            "display_json,created_at,updated_at,expires_at,version,schema_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id, values["owner_scope"], values["capability_id"], values["source"],
                values["request_key_digest"], values["payload_fingerprint"], PENDING,
                values["risk_snapshot"], values["policy_version"], values["workflow_id"],
                values["workflow_action_id"], values["job_id"], values["display_json"],
                current, current, current + self.approval_ttl_seconds, 1,
                HUMAN_APPROVAL_SCHEMA_VERSION,
            ),
        )
        self._prune_locked(db, current)
        self._check_capacity_locked(db)
        row = db.execute("SELECT * FROM human_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            raise HumanApprovalCapacityError("Approval storage could not retain the new request.")
        record = self._record(row)
        assert record is not None
        return record, True

    def request(self, **kwargs: Any) -> dict[str, Any]:
        capability_id = str(kwargs.get("capability_id") or "unknown")
        spec = CAPABILITY_REGISTRY.get(capability_id)
        source = kwargs.get("source")
        try:
            safe_source = (source if isinstance(source, CapabilitySource) else CapabilitySource(str(source))).value
        except ValueError:
            safe_source = "unknown"
        attrs = {
            "helper.approval.capability": spec.capability_id if spec else "unknown",
            "helper.approval.source": safe_source,
            "helper.approval.risk": spec.risk.value if spec else "unknown",
        }
        with start_span("helper.approval.request", attrs):
            now = self._clock()
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    record, created = self._request_locked(db, now=now, **kwargs)
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        if created:
            self._emit(record, "request", status=PENDING)
        return record

    def _emit(self, record: dict[str, Any], event: str, *, status: str) -> None:
        capability = str(record.get("capability_id") or "unknown")
        source = str(record.get("source") or "unknown")
        risk = str(record.get("risk_snapshot") or "unknown")
        if event == "request":
            increment_counter("helper.approval.requests", {
                "capability": capability, "source": source, "risk": risk, "status": status,
            })
        elif event == "decision":
            increment_counter("helper.approval.decisions", {
                "capability": capability, "source": source, "risk": risk, "decision": status,
            })
        elif event == "expired":
            increment_counter("helper.approval.expired", {
                "capability": capability, "source": source, "risk": risk, "status": status,
            })
        logger.info(
            "[ApprovalTrace] capability=%s source=%s risk=%s event=%s status=%s",
            capability, source, risk, event, status,
        )

    def get(self, approval_id: str, owner: str) -> dict[str, Any] | None:
        try:
            canonical_id = str(uuid.UUID(str(approval_id)))
            owner_scope = pseudonymous_owner_scope(owner)
        except (ValueError, HumanApprovalError):
            return None
        now = self._clock()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._reconcile_locked(db, now)
                self._prune_locked(db, now)
                row = db.execute(
                    "SELECT * FROM human_approvals WHERE approval_id=? AND owner_scope=?",
                    (canonical_id, owner_scope),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._emit_reconciled(changed)
        return self._record(row)

    def list_for_owner(self, owner: str, *, limit: int = 50) -> list[dict[str, Any]]:
        owner_scope = pseudonymous_owner_scope(owner)
        now = self._clock()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._reconcile_locked(db, now)
                self._prune_locked(db, now)
                rows = db.execute(
                    "SELECT * FROM human_approvals WHERE owner_scope=? "
                    "ORDER BY created_at DESC,approval_id DESC LIMIT ?",
                    (owner_scope, max(1, min(int(limit), 100))),
                ).fetchall()
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._emit_reconciled(changed)
        return [record for row in rows if (record := self._record(row)) is not None]

    def _emit_reconciled(self, changed: list[tuple[str, str, str]]) -> None:
        for capability, source, status in changed:
            if status == EXPIRED:
                spec = CAPABILITY_REGISTRY.get(capability)
                self._emit({
                    "capability_id": capability,
                    "source": source,
                    "risk_snapshot": spec.risk.value if spec else "unknown",
                }, "expired", status=status)

    def _decide(self, approval_id: str, owner: str, expected_version: int, decision: str) -> dict[str, Any] | None:
        if decision not in {APPROVED, REJECTED}:
            raise HumanApprovalValidationError("Unsupported approval decision.")
        try:
            canonical_id = str(uuid.UUID(str(approval_id)))
            owner_scope = pseudonymous_owner_scope(owner)
        except (ValueError, HumanApprovalError):
            return None
        now = self._clock()
        conflict = False
        changed = False
        reconciled: list[tuple[str, str, str]] = []
        record = None
        with start_span("helper.approval.decision", {
            "helper.approval.decision": decision,
        }) as span:
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    reconciled = self._reconcile_locked(db, now)
                    row = db.execute(
                        "SELECT * FROM human_approvals WHERE approval_id=? AND owner_scope=?",
                        (canonical_id, owner_scope),
                    ).fetchone()
                    if row is None:
                        db.commit()
                        return None
                    if span is not None:
                        span.set_attribute("helper.approval.id", canonical_id)
                        span.set_attribute("helper.approval.capability", str(row["capability_id"]))
                    if int(row["version"]) != int(expected_version):
                        conflict = True
                    elif row["status"] == decision:
                        record = self._record(row)
                    elif row["status"] != PENDING or float(row["expires_at"]) <= now:
                        conflict = True
                    else:
                        db.execute(
                            "UPDATE human_approvals SET status=?,decision_at=?,updated_at=?,version=version+1 "
                            "WHERE approval_id=? AND owner_scope=? AND status=? AND version=?",
                            (decision, now, now, canonical_id, owner_scope, PENDING, expected_version),
                        )
                        row = db.execute(
                            "SELECT * FROM human_approvals WHERE approval_id=? AND owner_scope=?",
                            (canonical_id, owner_scope),
                        ).fetchone()
                        record = self._record(row)
                        changed = True
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        self._emit_reconciled(reconciled)
        if conflict:
            raise HumanApprovalConflict("approval_changed")
        if record and changed:
            self._emit(record, "decision", status=decision)
        return record

    def approve(self, approval_id: str, owner: str, expected_version: int) -> dict[str, Any] | None:
        return self._decide(approval_id, owner, expected_version, APPROVED)

    def reject(self, approval_id: str, owner: str, expected_version: int) -> dict[str, Any] | None:
        return self._decide(approval_id, owner, expected_version, REJECTED)

    def _new_grant(self, row: sqlite3.Row | dict[str, Any], owner: str) -> HumanApprovalGrant:
        return HumanApprovalGrant(
            approval_id=str(row["approval_id"]),
            owner=str(owner),
            capability_id=str(row["capability_id"]),
            payload_fingerprint=str(row["payload_fingerprint"]),
            request_key_digest=str(row["request_key_digest"]),
            claim_id=str(row["claim_id"]),
            expires_at=float(row["claim_expires_at"]),
            _store=self,
        )

    def _claim_locked(
        self,
        db: sqlite3.Connection,
        approval_id: str,
        owner: str,
        *,
        capability_id: str,
        payload_fingerprint: str,
        request_key: str,
        now: float | None = None,
    ) -> HumanApprovalGrant | None:
        current = self._clock() if now is None else now
        canonical_id = str(uuid.UUID(str(approval_id)))
        owner_scope = pseudonymous_owner_scope(owner)
        fingerprint = validate_payload_fingerprint(payload_fingerprint)
        key_digest = request_key_digest(request_key)
        self._reconcile_locked(db, current)
        row = db.execute(
            "SELECT * FROM human_approvals WHERE approval_id=? AND owner_scope=?",
            (canonical_id, owner_scope),
        ).fetchone()
        if (
            not row
            or row["status"] != APPROVED
            or float(row["expires_at"]) <= current
            or row["capability_id"] != capability_id
            or row["payload_fingerprint"] != fingerprint
            or row["request_key_digest"] != key_digest
        ):
            return None
        claim_id = str(uuid.uuid4())
        claim_expires_at = min(current + self.claim_seconds, float(row["expires_at"]))
        changed = db.execute(
            "UPDATE human_approvals SET status=?,claim_id=?,claim_expires_at=?,claimed_at=?,"
            "updated_at=?,version=version+1 WHERE approval_id=? AND owner_scope=? AND status=? AND version=?",
            (CLAIMED, claim_id, claim_expires_at, current, current, canonical_id, owner_scope, APPROVED, row["version"]),
        ).rowcount == 1
        if not changed:
            return None
        claimed = db.execute("SELECT * FROM human_approvals WHERE approval_id=?", (canonical_id,)).fetchone()
        return self._new_grant(claimed, owner) if claimed else None

    def claim(
        self,
        approval_id: str,
        owner: str,
        *,
        capability_id: str,
        payload_fingerprint: str,
        request_key: str,
    ) -> HumanApprovalGrant | None:
        spec = CAPABILITY_REGISTRY.get(str(capability_id or ""))
        with start_span("helper.approval.claim", {
            "helper.approval.capability": spec.capability_id if spec else "unknown",
        }):
            now = self._clock()
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    grant = self._claim_locked(
                        db, approval_id, owner,
                        capability_id=capability_id,
                        payload_fingerprint=payload_fingerprint,
                        request_key=request_key,
                        now=now,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        if grant:
            logger.info(
                "[ApprovalTrace] capability=%s source=internal risk=high event=claim status=%s",
                grant.capability_id, CLAIMED,
            )
        return grant

    def _grant_is_current(self, grant: HumanApprovalGrant, *, db: sqlite3.Connection | None = None, now: float | None = None) -> bool:
        current = self._clock() if now is None else now
        owner_scope = pseudonymous_owner_scope(grant.owner)
        def query(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                "SELECT owner_scope,capability_id,request_key_digest,payload_fingerprint,status,"
                "claim_id,claim_expires_at,expires_at FROM human_approvals WHERE approval_id=?",
                (grant.approval_id,),
            ).fetchone()
            return bool(
                row
                and row["owner_scope"] == owner_scope
                and row["capability_id"] == grant.capability_id
                and row["request_key_digest"] == grant.request_key_digest
                and row["payload_fingerprint"] == grant.payload_fingerprint
                and row["status"] == CLAIMED
                and row["claim_id"] == grant.claim_id
                and float(row["claim_expires_at"] or 0) > current
                and float(row["expires_at"]) > current
                and float(row["claim_expires_at"]) == float(grant.expires_at)
            )
        if db is not None:
            return query(db)
        with self._open() as connection:
            return query(connection)

    def _renew_claim_locked(self, db: sqlite3.Connection, grant: HumanApprovalGrant, now: float) -> HumanApprovalGrant | None:
        if not self._grant_is_current(grant, db=db, now=now):
            return None
        expires = db.execute(
            "SELECT expires_at FROM human_approvals WHERE approval_id=?", (grant.approval_id,)
        ).fetchone()
        if not expires:
            return None
        claim_expires_at = min(now + self.claim_seconds, float(expires["expires_at"]))
        db.execute(
            "UPDATE human_approvals SET claim_expires_at=?,updated_at=?,version=version+1 "
            "WHERE approval_id=? AND claim_id=? AND status=?",
            (claim_expires_at, now, grant.approval_id, grant.claim_id, CLAIMED),
        )
        row = db.execute("SELECT * FROM human_approvals WHERE approval_id=?", (grant.approval_id,)).fetchone()
        return self._new_grant(row, grant.owner) if row else None

    def renew_claim(self, grant: HumanApprovalGrant) -> HumanApprovalGrant | None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                renewed = self._renew_claim_locked(db, grant, self._clock())
                db.commit()
                return renewed
            except Exception:
                db.rollback()
                raise

    def _release_claim_locked(self, db: sqlite3.Connection, grant: HumanApprovalGrant, now: float) -> bool:
        if not self._grant_is_current(grant, db=db, now=now):
            return False
        row = db.execute("SELECT expires_at FROM human_approvals WHERE approval_id=?", (grant.approval_id,)).fetchone()
        status = EXPIRED if not row or float(row["expires_at"]) <= now else APPROVED
        return db.execute(
            "UPDATE human_approvals SET status=?,claim_id=NULL,claim_expires_at=NULL,claimed_at=NULL,"
            "updated_at=?,version=version+1 WHERE approval_id=? AND status=? AND claim_id=?",
            (status, now, grant.approval_id, CLAIMED, grant.claim_id),
        ).rowcount == 1

    def release_claim(self, grant: HumanApprovalGrant) -> bool:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._release_claim_locked(db, grant, self._clock())
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def _consume_locked(self, db: sqlite3.Connection, grant: HumanApprovalGrant, now: float) -> bool:
        if not self._grant_is_current(grant, db=db, now=now):
            return False
        return db.execute(
            "UPDATE human_approvals SET status=?,consumed_at=?,updated_at=?,claim_id=NULL,"
            "claim_expires_at=NULL,version=version+1 WHERE approval_id=? AND status=? AND claim_id=?",
            (CONSUMED, now, now, grant.approval_id, CLAIMED, grant.claim_id),
        ).rowcount == 1

    def consume(self, grant: HumanApprovalGrant) -> bool:
        with start_span("helper.approval.consume", {
            "helper.approval.id": grant.approval_id,
            "helper.approval.capability": grant.capability_id,
        }):
            now = self._clock()
            with self._open() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    changed = self._consume_locked(db, grant, now)
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        if changed:
            logger.info(
                "[ApprovalTrace] capability=%s source=internal risk=high event=consume status=%s",
                grant.capability_id, CONSUMED,
            )
        return changed

    def _cancel_locked(
        self,
        db: sqlite3.Connection,
        *,
        approval_id: str,
        owner_scope: str,
        now: float,
    ) -> bool:
        return db.execute(
            "UPDATE human_approvals SET status=?,claim_id=NULL,claim_expires_at=NULL,claimed_at=NULL,"
            "updated_at=?,version=version+1 WHERE approval_id=? AND owner_scope=? AND status IN (?,?,?)",
            (CANCELLED, now, approval_id, owner_scope, PENDING, APPROVED, CLAIMED),
        ).rowcount == 1

    def cancel(self, approval_id: str, owner: str) -> bool:
        try:
            canonical_id = str(uuid.UUID(str(approval_id)))
            owner_scope = pseudonymous_owner_scope(owner)
        except (ValueError, HumanApprovalError):
            return False
        now = self._clock()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._cancel_locked(
                    db, approval_id=canonical_id, owner_scope=owner_scope, now=now,
                )
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def cancel_for_workflow_locked(
        db: sqlite3.Connection,
        *,
        owner: str,
        workflow_id: str,
        now: float,
    ) -> int:
        owner_scope = pseudonymous_owner_scope(owner)
        return max(0, db.execute(
            "UPDATE human_approvals SET status=?,claim_id=NULL,claim_expires_at=NULL,claimed_at=NULL,"
            "updated_at=?,version=version+1 WHERE owner_scope=? AND workflow_id=? AND status IN (?,?,?)",
            (CANCELLED, now, owner_scope, workflow_id, PENDING, APPROVED, CLAIMED),
        ).rowcount)

    def recover_expired(self) -> int:
        now = self._clock()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._reconcile_locked(db, now)
                self._prune_locked(db, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._emit_reconciled(changed)
        return len(changed)

    def prune(self) -> int:
        now = self._clock()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._reconcile_locked(db, now)
                deleted = self._prune_locked(db, now)
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._emit_reconciled(changed)
        return deleted + len(changed)

    def aggregate_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in sorted(ALL_STATES)}
        with self._open() as db:
            for row in db.execute("SELECT status,COUNT(*) AS count FROM human_approvals GROUP BY status"):
                status = str(row["status"])
                if status in counts:
                    counts[status] = int(row["count"])
        return counts


def _bounded_env_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def public_approval_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """Project the private row into the owner-facing, non-authoritative DTO."""
    manifest = record.get("manifest") or {}
    details = manifest.get("details") if isinstance(manifest, dict) else []
    return {
        "approval_id": record["approval_id"],
        "capability": record["capability_id"],
        "risk": record["risk_snapshot"],
        "status": record["status"],
        "title": str(manifest.get("title") or "Action approval"),
        "summary": str(manifest.get("summary") or "Review this action before deciding."),
        "details": details if isinstance(details, list) else [],
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "version": record["version"],
    }


human_approval_store = SQLiteHumanApprovalStore()
