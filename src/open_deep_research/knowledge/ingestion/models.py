"""Durable, scope-aware document import job models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from open_deep_research.knowledge.ids import (
    canonicalize_local_ref,
    canonicalize_text,
    stable_id,
    validate_sha256,
)
from open_deep_research.knowledge.models import DomainModel, utc_now


class ImportInputKind(StrEnum):
    """The four local candidate input kinds accepted in Phase 2."""

    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML_SNAPSHOT = "html_snapshot"
    PAST_QUERY = "past_query"


class ImportJobStatus(StrEnum):
    """Operational import states, independent of knowledge lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ImportIndexStatus(StrEnum):
    """Derived-index states that never promote a DocumentVersion."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ImportJobError(DomainModel):
    """Sanitized, structured failure information suitable for retry logic."""

    code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool

    @model_validator(mode="after")
    def normalize_strings(self) -> Self:
        for name in ("code", "stage", "message"):
            value = canonicalize_text(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} cannot be blank")
            object.__setattr__(self, name, value)
        return self


def chunk_config_sha256(chunk_config: dict[str, Any]) -> str:
    """Hash a canonical JSON chunk configuration."""
    payload = json.dumps(
        chunk_config,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ImportJob(DomainModel):
    """Persistent recovery boundary for one deterministic import attempt."""

    job_id: str = ""
    scope_id: str
    input_kind: ImportInputKind
    input_ref: str = Field(min_length=1)
    content_sha256: str
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    chunk_config: dict[str, Any] = Field(default_factory=dict)
    chunk_config_sha256: str = ""
    status: ImportJobStatus = ImportJobStatus.PENDING
    index_status: ImportIndexStatus = ImportIndexStatus.NOT_REQUESTED
    attempt_count: int = Field(default=0, ge=0)
    blob_id: str | None = None
    source_id: str | None = None
    document_id: str | None = None
    version_id: str | None = None
    error: ImportJobError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def populate_identity_and_validate_state(self) -> Self:
        input_ref = canonicalize_local_ref(self.input_ref)
        if not input_ref:
            raise ValueError("input_ref cannot be blank")
        parser_name = canonicalize_text(self.parser_name).strip()
        parser_version = canonicalize_text(self.parser_version).strip()
        if not parser_name or not parser_version:
            raise ValueError("parser identity cannot be blank")
        content_digest = validate_sha256(self.content_sha256)
        config_digest = chunk_config_sha256(self.chunk_config)
        if self.chunk_config_sha256:
            if validate_sha256(self.chunk_config_sha256) != config_digest:
                raise ValueError("chunk_config_sha256 does not match chunk_config")
        expected_id = stable_id(
            "imp",
            self.scope_id,
            self.input_kind.value,
            input_ref,
            content_digest,
            parser_name,
            parser_version,
            config_digest,
        )
        if self.job_id and self.job_id != expected_id:
            raise ValueError("job_id does not match import identity")
        has_failure = (
            self.status is ImportJobStatus.FAILED
            or self.index_status is ImportIndexStatus.FAILED
        )
        if has_failure and self.error is None:
            raise ValueError("failed import or index state requires structured error")
        if not has_failure and self.error is not None:
            raise ValueError("only failed import or index states may retain an error")
        references = (self.blob_id, self.source_id, self.document_id, self.version_id)
        if self.status is ImportJobStatus.SUCCEEDED and any(
            value is None for value in references
        ):
            raise ValueError("succeeded import job requires the complete entity chain")
        if (
            self.index_status is not ImportIndexStatus.NOT_REQUESTED
            and self.status is not ImportJobStatus.SUCCEEDED
        ):
            raise ValueError("index work cannot begin before import succeeds")
        for name in ("created_at", "updated_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("timestamps must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        object.__setattr__(self, "job_id", expected_id)
        object.__setattr__(self, "input_ref", input_ref)
        object.__setattr__(self, "content_sha256", content_digest)
        object.__setattr__(self, "parser_name", parser_name)
        object.__setattr__(self, "parser_version", parser_version)
        object.__setattr__(self, "chunk_config_sha256", config_digest)
        return self

    def transition(
        self,
        *,
        status: ImportJobStatus,
        index_status: ImportIndexStatus | None = None,
        error: ImportJobError | None = None,
        blob_id: str | None = None,
        source_id: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        at: datetime | None = None,
    ) -> ImportJob:
        """Return a validated next state without mutating this snapshot."""
        allowed_status = {
            ImportJobStatus.PENDING: {
                ImportJobStatus.RUNNING,
                ImportJobStatus.FAILED,
            },
            ImportJobStatus.RUNNING: {
                ImportJobStatus.SUCCEEDED,
                ImportJobStatus.FAILED,
            },
            ImportJobStatus.FAILED: {ImportJobStatus.RUNNING},
            ImportJobStatus.SUCCEEDED: {ImportJobStatus.SUCCEEDED},
        }
        if status not in allowed_status[self.status]:
            raise ValueError(
                f"invalid import job transition: {self.status.value}->{status.value}"
            )
        next_index = index_status or self.index_status
        allowed_index = {
            ImportIndexStatus.NOT_REQUESTED: {
                ImportIndexStatus.NOT_REQUESTED,
                ImportIndexStatus.PENDING,
                ImportIndexStatus.FAILED,
            },
            ImportIndexStatus.PENDING: {
                ImportIndexStatus.PENDING,
                ImportIndexStatus.READY,
                ImportIndexStatus.FAILED,
            },
            ImportIndexStatus.FAILED: {
                ImportIndexStatus.FAILED,
                ImportIndexStatus.PENDING,
            },
            ImportIndexStatus.READY: {ImportIndexStatus.READY},
        }
        if next_index not in allowed_index[self.index_status]:
            raise ValueError(
                "invalid import index transition: "
                f"{self.index_status.value}->{next_index.value}"
            )
        instant = at or max(utc_now(), self.updated_at)
        is_terminal = status in {
            ImportJobStatus.SUCCEEDED,
            ImportJobStatus.FAILED,
        }
        payload = self.model_dump()
        payload.update(
            {
                "status": status,
                "index_status": next_index,
                "attempt_count": self.attempt_count
                + (1 if status is ImportJobStatus.RUNNING else 0),
                "blob_id": blob_id or self.blob_id,
                "source_id": source_id or self.source_id,
                "document_id": document_id or self.document_id,
                "version_id": version_id or self.version_id,
                "error": (
                    error
                    if status is ImportJobStatus.FAILED
                    or next_index is ImportIndexStatus.FAILED
                    else None
                ),
                "updated_at": instant,
                "finished_at": (
                    self.finished_at or instant if is_terminal else None
                ),
            }
        )
        return ImportJob.model_validate(payload)
