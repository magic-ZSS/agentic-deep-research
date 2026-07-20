"""Run-scoped transient evidence storage for governed Web retrieval.

This store is deliberately separate from the canonical knowledge repository.  A
bundle can be validated for the current run, but its document version remains a
candidate and it is never made visible to another run by this module.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.ids import stable_id
from open_deep_research.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    DomainModel,
    Source,
    VersionLifecycleStatus,
    utc_now,
)


class RunEvidenceStoreError(RuntimeError):
    """Base error for transient run evidence operations."""


class RunEvidenceNotFoundError(RunEvidenceStoreError, LookupError):
    """The requested stable ID is not visible in the trusted run context."""


class RunEvidenceConflictError(RunEvidenceStoreError):
    """A compare-and-set precondition or immutable identity was violated."""


class RunEvidenceValidationStatus(StrEnum):
    """Validation state that is meaningful only inside one research run."""

    PENDING = "pending"
    VALIDATED_FOR_RUN = "validated_for_run"
    REJECTED = "rejected"


class RunEvidenceContext(DomainModel):
    """Trusted scope and run boundary supplied by orchestration code."""

    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_identity(self) -> Self:
        scope_id = self.scope_id.strip()
        run_id = self.run_id.strip()
        if not scope_id or not run_id:
            raise ValueError("scope_id and run_id cannot be blank")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "run_id", run_id)
        return self


class RunEvidenceBundle(DomainModel):
    """Complete immutable citation chain plus run-local validation metadata."""

    scope_id: str
    run_id: str
    source: Source
    document: Document
    version: DocumentVersion
    chunk: Chunk
    evidence: Evidence
    validation_status: RunEvidenceValidationStatus = (
        RunEvidenceValidationStatus.PENDING
    )
    validation_reason: str | None = None
    validation_actor: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @classmethod
    def create(
        cls,
        *,
        context: RunEvidenceContext,
        source: Source,
        document: Document,
        version: DocumentVersion,
        chunk: Chunk,
        evidence: Evidence,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> RunEvidenceBundle:
        """Create a pending candidate bundle with an explicit positive TTL."""
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        created_at = _aware_utc(now or utc_now())
        return cls(
            scope_id=context.scope_id,
            run_id=context.run_id,
            source=source,
            document=document,
            version=version,
            chunk=chunk,
            evidence=evidence,
            created_at=created_at,
            expires_at=created_at + ttl,
        )

    @model_validator(mode="after")
    def validate_candidate_chain(self) -> Self:
        scope_ids = {
            self.scope_id,
            self.source.scope_id,
            self.document.scope_id,
            self.version.scope_id,
            self.chunk.scope_id,
            self.evidence.scope_id,
        }
        if len(scope_ids) != 1:
            raise ValueError("run evidence chain crosses scope boundaries")
        if not (
            self.document.source_id == self.source.source_id
            and self.version.document_id == self.document.document_id
            and self.chunk.version_id == self.version.version_id
            and self.evidence.chunk_id == self.chunk.chunk_id
        ):
            raise ValueError("run evidence citation chain is inconsistent")
        if self.version.lifecycle_status is not VersionLifecycleStatus.CANDIDATE:
            raise ValueError("run evidence versions must remain candidate")
        if self.evidence.validation_status is not EvidenceValidationStatus.PENDING:
            raise ValueError("canonical evidence snapshot must remain pending")
        if any(
            item.soft_deleted_at is not None
            for item in (
                self.source,
                self.document,
                self.version,
                self.chunk,
                self.evidence,
            )
        ):
            raise ValueError("soft-deleted entities cannot enter RunEvidenceStore")
        created_at = _aware_utc(self.created_at)
        expires_at = _aware_utc(self.expires_at)
        if expires_at <= created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.validation_status is RunEvidenceValidationStatus.PENDING:
            if self.validation_reason is not None or self.validation_actor is not None:
                raise ValueError("pending evidence cannot have validation metadata")
        elif not self.validation_reason or not self.validation_actor:
            raise ValueError("terminal validation requires reason and actor")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        return self

    @property
    def evidence_id(self) -> str:
        """Return the primary stable resolver key."""
        return self.evidence.evidence_id

    def stable_ids(self) -> tuple[str, ...]:
        """Return every stable ID in the contained citation chain."""
        return (
            self.source.source_id,
            self.document.document_id,
            self.version.version_id,
            self.chunk.chunk_id,
            self.evidence.evidence_id,
        )


class RunEvidenceCleanupAudit(DomainModel):
    """Durable maintenance record retained after transient evidence expires."""

    audit_id: str
    scope_id: str
    run_id: str
    evidence_id: str
    expired_at: datetime
    cleaned_at: datetime
    reason: str = "ttl_expired"

    @model_validator(mode="after")
    def normalize_timestamps(self) -> Self:
        expired_at = _aware_utc(self.expired_at)
        cleaned_at = _aware_utc(self.cleaned_at)
        if cleaned_at < expired_at:
            raise ValueError("cleanup cannot precede expiry")
        object.__setattr__(self, "expired_at", expired_at)
        object.__setattr__(self, "cleaned_at", cleaned_at)
        return self


@runtime_checkable
class RunEvidenceStore(Protocol):
    """Backend-neutral contract for isolated transient evidence."""

    async def put(
        self, context: RunEvidenceContext, bundle: RunEvidenceBundle
    ) -> RunEvidenceBundle: ...

    async def resolve(
        self, context: RunEvidenceContext, stable_id: str
    ) -> RunEvidenceBundle: ...

    async def list(
        self,
        context: RunEvidenceContext,
        *,
        validation_status: RunEvidenceValidationStatus | None = None,
    ) -> list[RunEvidenceBundle]: ...

    async def compare_and_set_validation(
        self,
        context: RunEvidenceContext,
        evidence_id: str,
        *,
        expected: RunEvidenceValidationStatus,
        status: RunEvidenceValidationStatus,
        reason: str,
        actor: str,
    ) -> RunEvidenceBundle: ...

    async def cleanup_expired(
        self, *, now: datetime | None = None
    ) -> list[RunEvidenceCleanupAudit]: ...

    async def list_cleanup_audit(
        self, context: RunEvidenceContext
    ) -> list[RunEvidenceCleanupAudit]: ...


class InMemoryRunEvidenceStore:
    """Deterministic in-memory implementation used by tests and local runs."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._bundles: dict[tuple[str, str, str], RunEvidenceBundle] = {}
        self._audits: list[RunEvidenceCleanupAudit] = []
        self._lock = asyncio.Lock()

    async def put(
        self, context: RunEvidenceContext, bundle: RunEvidenceBundle
    ) -> RunEvidenceBundle:
        _authorize_context(context, bundle)
        key = (context.scope_id, context.run_id, bundle.evidence_id)
        async with self._lock:
            existing = self._bundles.get(key)
            if existing is not None:
                return existing
            self._bundles[key] = bundle
            return bundle

    async def resolve(
        self, context: RunEvidenceContext, stable_id: str
    ) -> RunEvidenceBundle:
        async with self._lock:
            matches = [
                bundle
                for (scope_id, run_id, _), bundle in self._bundles.items()
                if scope_id == context.scope_id
                and run_id == context.run_id
                and stable_id in bundle.stable_ids()
                and not _is_expired(bundle, self._clock())
            ]
            return _one_match(matches, stable_id)

    async def list(
        self,
        context: RunEvidenceContext,
        *,
        validation_status: RunEvidenceValidationStatus | None = None,
    ) -> list[RunEvidenceBundle]:
        now = self._clock()
        async with self._lock:
            return sorted(
                (
                    bundle
                    for (scope_id, run_id, _), bundle in self._bundles.items()
                    if scope_id == context.scope_id
                    and run_id == context.run_id
                    and not _is_expired(bundle, now)
                    and (
                        validation_status is None
                        or bundle.validation_status is validation_status
                    )
                ),
                key=lambda bundle: bundle.evidence_id,
            )

    async def compare_and_set_validation(
        self,
        context: RunEvidenceContext,
        evidence_id: str,
        *,
        expected: RunEvidenceValidationStatus,
        status: RunEvidenceValidationStatus,
        reason: str,
        actor: str,
    ) -> RunEvidenceBundle:
        _validate_transition(expected, status, reason, actor)
        key = (context.scope_id, context.run_id, evidence_id)
        async with self._lock:
            current = self._bundles.get(key)
            if current is None or _is_expired(current, self._clock()):
                raise RunEvidenceNotFoundError(evidence_id)
            if current.validation_status is not expected:
                raise RunEvidenceConflictError(
                    f"expected {expected}, found {current.validation_status}"
                )
            updated = current.model_copy(
                update={
                    "validation_status": status,
                    "validation_reason": reason.strip(),
                    "validation_actor": actor.strip(),
                }
            )
            self._bundles[key] = updated
            return updated

    async def cleanup_expired(
        self, *, now: datetime | None = None
    ) -> list[RunEvidenceCleanupAudit]:
        instant = _aware_utc(now or self._clock())
        async with self._lock:
            expired = sorted(
                (
                    (key, bundle)
                    for key, bundle in self._bundles.items()
                    if _is_expired(bundle, instant)
                ),
                key=lambda item: item[0],
            )
            audits = [
                _cleanup_audit(bundle=bundle, cleaned_at=instant)
                for _, bundle in expired
            ]
            for key, _ in expired:
                del self._bundles[key]
            self._audits.extend(audits)
            return audits

    async def list_cleanup_audit(
        self, context: RunEvidenceContext
    ) -> list[RunEvidenceCleanupAudit]:
        async with self._lock:
            return sorted(
                (
                    audit
                    for audit in self._audits
                    if audit.scope_id == context.scope_id
                    and audit.run_id == context.run_id
                ),
                key=lambda audit: (audit.cleaned_at, audit.audit_id),
            )


class SQLiteRunEvidenceStore:
    """Independent SQLite store with no canonical repository tables or writes."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_evidence_bundles (
                    scope_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (scope_id, run_id, evidence_id)
                );
                CREATE INDEX IF NOT EXISTS idx_run_evidence_chain_ids
                    ON run_evidence_bundles(
                        scope_id, run_id, source_id, document_id, version_id, chunk_id
                    );
                CREATE INDEX IF NOT EXISTS idx_run_evidence_expiry
                    ON run_evidence_bundles(expires_at);
                CREATE TABLE IF NOT EXISTS run_evidence_cleanup_audit (
                    audit_id TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    cleaned_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_evidence_audit_context
                    ON run_evidence_cleanup_audit(scope_id, run_id, cleaned_at);
                """
            )

    async def put(
        self, context: RunEvidenceContext, bundle: RunEvidenceBundle
    ) -> RunEvidenceBundle:
        _authorize_context(context, bundle)
        async with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO run_evidence_bundles(
                        scope_id, run_id, evidence_id, source_id, document_id,
                        version_id, chunk_id, validation_status, expires_at,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_id, run_id, evidence_id) DO NOTHING
                    """,
                    _bundle_row(bundle),
                )
                row = connection.execute(
                    """
                    SELECT payload_json FROM run_evidence_bundles
                    WHERE scope_id = ? AND run_id = ? AND evidence_id = ?
                    """,
                    (context.scope_id, context.run_id, bundle.evidence_id),
                ).fetchone()
                if row is None:  # pragma: no cover - transaction invariant
                    raise RunEvidenceStoreError("put did not persist or find a bundle")
                return RunEvidenceBundle.model_validate_json(row["payload_json"])

    async def resolve(
        self, context: RunEvidenceContext, stable_id: str
    ) -> RunEvidenceBundle:
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM run_evidence_bundles
                    WHERE scope_id = ? AND run_id = ?
                      AND (evidence_id = ? OR chunk_id = ? OR version_id = ?
                           OR document_id = ? OR source_id = ?)
                    ORDER BY evidence_id
                    """,
                    (
                        context.scope_id,
                        context.run_id,
                        stable_id,
                        stable_id,
                        stable_id,
                        stable_id,
                        stable_id,
                    ),
                ).fetchall()
        matches = [
            bundle
            for row in rows
            if not _is_expired(
                bundle := RunEvidenceBundle.model_validate_json(row["payload_json"]),
                self._clock(),
            )
        ]
        return _one_match(matches, stable_id)

    async def list(
        self,
        context: RunEvidenceContext,
        *,
        validation_status: RunEvidenceValidationStatus | None = None,
    ) -> list[RunEvidenceBundle]:
        query = (
            "SELECT payload_json FROM run_evidence_bundles "
            "WHERE scope_id = ? AND run_id = ?"
        )
        parameters: list[str] = [context.scope_id, context.run_id]
        if validation_status is not None:
            query += " AND validation_status = ?"
            parameters.append(validation_status.value)
        query += " ORDER BY evidence_id"
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(query, parameters).fetchall()
        now = self._clock()
        return [
            bundle
            for row in rows
            if not _is_expired(
                bundle := RunEvidenceBundle.model_validate_json(row["payload_json"]),
                now,
            )
        ]

    async def compare_and_set_validation(
        self,
        context: RunEvidenceContext,
        evidence_id: str,
        *,
        expected: RunEvidenceValidationStatus,
        status: RunEvidenceValidationStatus,
        reason: str,
        actor: str,
    ) -> RunEvidenceBundle:
        _validate_transition(expected, status, reason, actor)
        async with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT payload_json FROM run_evidence_bundles
                    WHERE scope_id = ? AND run_id = ? AND evidence_id = ?
                    """,
                    (context.scope_id, context.run_id, evidence_id),
                ).fetchone()
                if row is None:
                    raise RunEvidenceNotFoundError(evidence_id)
                current = RunEvidenceBundle.model_validate_json(row["payload_json"])
                if _is_expired(current, self._clock()):
                    raise RunEvidenceNotFoundError(evidence_id)
                if current.validation_status is not expected:
                    raise RunEvidenceConflictError(
                        f"expected {expected}, found {current.validation_status}"
                    )
                updated = current.model_copy(
                    update={
                        "validation_status": status,
                        "validation_reason": reason.strip(),
                        "validation_actor": actor.strip(),
                    }
                )
                cursor = connection.execute(
                    """
                    UPDATE run_evidence_bundles
                    SET validation_status = ?, payload_json = ?
                    WHERE scope_id = ? AND run_id = ? AND evidence_id = ?
                      AND validation_status = ?
                    """,
                    (
                        status.value,
                        updated.model_dump_json(),
                        context.scope_id,
                        context.run_id,
                        evidence_id,
                        expected.value,
                    ),
                )
                if cursor.rowcount != 1:  # pragma: no cover - guarded by lock
                    raise RunEvidenceConflictError("validation compare-and-set lost")
                return updated

    async def cleanup_expired(
        self, *, now: datetime | None = None
    ) -> list[RunEvidenceCleanupAudit]:
        instant = _aware_utc(now or self._clock())
        async with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT payload_json FROM run_evidence_bundles
                    WHERE expires_at <= ? ORDER BY scope_id, run_id, evidence_id
                    """,
                    (_serialize_datetime(instant),),
                ).fetchall()
                bundles = [
                    RunEvidenceBundle.model_validate_json(row["payload_json"])
                    for row in rows
                ]
                audits = [
                    _cleanup_audit(bundle=bundle, cleaned_at=instant)
                    for bundle in bundles
                ]
                for audit in audits:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO run_evidence_cleanup_audit(
                            audit_id, scope_id, run_id, evidence_id, cleaned_at,
                            payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audit.audit_id,
                            audit.scope_id,
                            audit.run_id,
                            audit.evidence_id,
                            _serialize_datetime(audit.cleaned_at),
                            audit.model_dump_json(),
                        ),
                    )
                connection.execute(
                    "DELETE FROM run_evidence_bundles WHERE expires_at <= ?",
                    (_serialize_datetime(instant),),
                )
                return audits

    async def list_cleanup_audit(
        self, context: RunEvidenceContext
    ) -> list[RunEvidenceCleanupAudit]:
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM run_evidence_cleanup_audit
                    WHERE scope_id = ? AND run_id = ?
                    ORDER BY cleaned_at, audit_id
                    """,
                    (context.scope_id, context.run_id),
                ).fetchall()
        return [
            RunEvidenceCleanupAudit.model_validate_json(row["payload_json"])
            for row in rows
        ]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _serialize_datetime(value: datetime) -> str:
    return _aware_utc(value).isoformat(timespec="microseconds")


def _authorize_context(
    context: RunEvidenceContext, bundle: RunEvidenceBundle
) -> None:
    if context.scope_id != bundle.scope_id or context.run_id != bundle.run_id:
        raise RunEvidenceNotFoundError(bundle.evidence_id)


def _is_expired(bundle: RunEvidenceBundle, now: datetime) -> bool:
    return bundle.expires_at <= _aware_utc(now)


def _one_match(
    matches: list[RunEvidenceBundle], stable_id: str
) -> RunEvidenceBundle:
    if not matches:
        raise RunEvidenceNotFoundError(stable_id)
    if len(matches) > 1:
        raise RunEvidenceConflictError(
            f"stable ID {stable_id!r} resolves to multiple evidence bundles"
        )
    return matches[0]


def _validate_transition(
    expected: RunEvidenceValidationStatus,
    status: RunEvidenceValidationStatus,
    reason: str,
    actor: str,
) -> None:
    if expected is not RunEvidenceValidationStatus.PENDING or status not in {
        RunEvidenceValidationStatus.VALIDATED_FOR_RUN,
        RunEvidenceValidationStatus.REJECTED,
    }:
        raise RunEvidenceConflictError(
            f"illegal run validation transition: {expected} -> {status}"
        )
    if not reason.strip() or not actor.strip():
        raise ValueError("validation reason and actor cannot be blank")


def _cleanup_audit(
    *, bundle: RunEvidenceBundle, cleaned_at: datetime
) -> RunEvidenceCleanupAudit:
    return RunEvidenceCleanupAudit(
        audit_id=stable_id(
            "run_cleanup",
            bundle.scope_id,
            bundle.run_id,
            bundle.evidence_id,
            _serialize_datetime(bundle.expires_at),
        ),
        scope_id=bundle.scope_id,
        run_id=bundle.run_id,
        evidence_id=bundle.evidence_id,
        expired_at=bundle.expires_at,
        cleaned_at=cleaned_at,
    )


def _bundle_row(bundle: RunEvidenceBundle) -> tuple[str, ...]:
    return (
        bundle.scope_id,
        bundle.run_id,
        bundle.evidence_id,
        bundle.source.source_id,
        bundle.document.document_id,
        bundle.version.version_id,
        bundle.chunk.chunk_id,
        bundle.validation_status.value,
        _serialize_datetime(bundle.expires_at),
        bundle.model_dump_json(),
    )
