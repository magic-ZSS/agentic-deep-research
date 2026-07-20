"""Evidence, requirement, and append-only audit domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from pydantic import Field, model_validator

from open_deep_research.knowledge.ids import (
    canonicalize_text,
    evidence_id_for,
    requirement_id_for,
)
from open_deep_research.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    DomainModel,
    Source,
    VersionLifecycleStatus,
    utc_now,
)


class EvidenceRelation(StrEnum):
    """How an evidence excerpt relates to a requirement or future claim."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class EvidenceDirectness(StrEnum):
    """Whether the excerpt directly establishes the represented statement."""

    DIRECT = "direct"
    INDIRECT = "indirect"
    UNKNOWN = "unknown"


class EvidenceValidationStatus(StrEnum):
    """Validation status independent of DocumentVersion lifecycle."""

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


class RequirementStatus(StrEnum):
    """Phase-1 persistence states without completion policy."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"


class Requirement(DomainModel):
    """Stable research requirement; extraction remains a later phase."""

    requirement_id: str = ""
    scope_id: str
    run_id: str | None = None
    template_id: str | None = None
    text: str = Field(min_length=1)
    acceptance_hint: str | None = None
    priority: int = Field(default=0, ge=0)
    parent_id: str | None = None
    status: RequirementStatus = RequirementStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        text = canonicalize_text(self.text).strip()
        if not text:
            raise ValueError("requirement text cannot be blank")
        expected = requirement_id_for(
            self.scope_id, self.run_id, self.template_id, text, self.parent_id
        )
        if self.requirement_id and self.requirement_id != expected:
            raise ValueError("requirement_id does not match requirement identity")
        if self.parent_id == expected:
            raise ValueError("requirement cannot be its own parent")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "requirement_id", expected)
        for name in ("created_at", "updated_at", "soft_deleted_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("timestamps must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        return self


class Evidence(DomainModel):
    """Traceable evidence excerpt linked to an immutable chunk."""

    evidence_id: str = ""
    scope_id: str
    chunk_id: str
    requirement_id: str | None = None
    excerpt: str = Field(min_length=1)
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS
    directness: EvidenceDirectness = EvidenceDirectness.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    valid_at: datetime | None = None
    retrieval_method: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    validation_status: EvidenceValidationStatus = EvidenceValidationStatus.PENDING
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        excerpt = canonicalize_text(self.excerpt).strip()
        if not excerpt:
            raise ValueError("evidence excerpt cannot be blank")
        expected = evidence_id_for(
            self.scope_id,
            self.chunk_id,
            self.requirement_id,
            excerpt,
            self.relation.value,
        )
        if self.evidence_id and self.evidence_id != expected:
            raise ValueError("evidence_id does not match evidence identity")
        object.__setattr__(self, "excerpt", excerpt)
        object.__setattr__(self, "evidence_id", expected)
        for name in ("valid_at", "created_at", "soft_deleted_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("timestamps must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        return self


class AuditEvent(DomainModel):
    """Append-only description of a repository mutation or proposal."""

    event_id: str = Field(default_factory=lambda: f"audit_{uuid4().hex}")
    scope_id: str
    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    actor_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    before_status: str | None = None
    after_status: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    correlation_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_timestamp(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        return self


def is_evidence_citable(
    evidence: Evidence,
    chunk: Chunk,
    version: DocumentVersion,
    document: Document,
    source: Source,
    *,
    at: datetime | None = None,
) -> bool:
    """Derive citation eligibility without creating another lifecycle state."""
    instant = (at or datetime.now(UTC)).astimezone(UTC)
    if evidence.validation_status is not EvidenceValidationStatus.VALIDATED:
        return False
    if version.lifecycle_status is not VersionLifecycleStatus.ACTIVE:
        return False
    if not (
        evidence.scope_id
        == chunk.scope_id
        == version.scope_id
        == document.scope_id
        == source.scope_id
        and evidence.chunk_id == chunk.chunk_id
        and chunk.version_id == version.version_id
        and version.document_id == document.document_id
        and document.source_id == source.source_id
    ):
        return False
    if any(
        item.soft_deleted_at is not None
        for item in (source, document, version, chunk, evidence)
    ):
        return False
    if version.valid_from and instant < version.valid_from:
        return False
    if version.valid_to and instant > version.valid_to:
        return False
    return True
