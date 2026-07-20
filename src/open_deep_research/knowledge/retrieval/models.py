"""Strict public models for local knowledge inspection and retrieval."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkLocatorType,
    Document,
    DocumentVersion,
    Source,
    SourceKind,
    SourcePublicView,
    VersionLifecycleStatus,
)


_STABLE_ID = re.compile(r"^(?:chk|evd)_[0-9a-f]{64}$")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)


class RetrievalModel(BaseModel):
    """Immutable and strict boundary model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChunkLocatorView(RetrievalModel):
    """Public structured locator copied from an authoritative Chunk."""

    locator_type: ChunkLocatorType
    page_start: int | None = None
    page_end: int | None = None
    heading_path: tuple[str, ...] = ()
    anchor: str | None = None

    @classmethod
    def from_chunk(cls, chunk: Chunk) -> ChunkLocatorView:
        return cls(
            locator_type=chunk.locator_type,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            heading_path=chunk.heading_path,
            anchor=chunk.anchor,
        )


class RetrievalFilters(RetrievalModel):
    """Programmatic filters applied before and after a retrieval backend."""

    source_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    version_ids: tuple[str, ...] = ()
    source_kinds: tuple[SourceKind, ...] = ()
    media_types: tuple[str, ...] = ()
    lifecycle_statuses: tuple[VersionLifecycleStatus, ...] = ()
    validation_statuses: tuple[EvidenceValidationStatus, ...] = ()

    @field_validator(
        "source_ids", "document_ids", "version_ids", "media_types", mode="after"
    )
    @classmethod
    def normalize_strings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        return normalized

    @field_validator(
        "source_kinds",
        "lifecycle_statuses",
        "validation_statuses",
        mode="after",
    )
    @classmethod
    def normalize_enums(cls, value: tuple) -> tuple:
        return tuple(sorted(set(value), key=lambda item: item.value))


class KnowledgeSearchRequest(RetrievalModel):
    """Internal search request; candidate access is separately capability-gated."""

    query: str = Field(min_length=1, max_length=4_000)
    limit: int = Field(default=10, ge=1, le=100)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    as_of: datetime | None = None
    include_candidate: bool = False
    contextualize: bool = False

    @model_validator(mode="after")
    def normalize_request(self) -> Self:
        query = self.query.strip()
        if not query:
            raise ValueError("query cannot be blank")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "as_of", _as_utc(self.as_of))
        return self


class KnowledgeReadRequest(RetrievalModel):
    """Read one imported chunk or evidence by a stable Repository ID only."""

    stable_id: str
    as_of: datetime | None = None
    include_candidate: bool = False

    @field_validator("stable_id")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if not _STABLE_ID.fullmatch(value):
            raise ValueError("stable_id must be a full chk_ or evd_ SHA-256 ID")
        return value

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)


class RetrievalRecord(RetrievalModel):
    """Authorized aggregate projection used to feed retrieval backends."""

    source: Source
    document: Document
    version: DocumentVersion
    chunk: Chunk
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def validate_chain(self) -> Self:
        if not (
            self.source.scope_id
            == self.document.scope_id
            == self.version.scope_id
            == self.chunk.scope_id
        ):
            raise ValueError("retrieval record crosses knowledge scopes")
        if not (
            self.document.source_id == self.source.source_id
            and self.version.document_id == self.document.document_id
            and self.chunk.version_id == self.version.version_id
        ):
            raise ValueError("retrieval record relationship chain is invalid")
        if self.evidence is not None and not (
            self.evidence.scope_id == self.chunk.scope_id
            and self.evidence.chunk_id == self.chunk.chunk_id
        ):
            raise ValueError("retrieval evidence does not belong to the chunk")
        return self


class EvidenceHit(RetrievalModel):
    """Safe retrieval result retaining only project-owned stable identities."""

    evidence_id: str | None = None
    chunk_id: str
    version_id: str
    document_id: str
    source_id: str
    scope_id: str
    source: SourcePublicView
    document_title: str
    media_type: str
    text: str
    contextual_summary: str | None = None
    score: float = Field(ge=0)
    rank: int = Field(ge=1)
    locator: ChunkLocatorView
    content_sha256: str
    published_at: datetime | None = None
    retrieved_at: datetime
    lifecycle_status: VersionLifecycleStatus
    validation_status: EvidenceValidationStatus | None = None
    retrieval_method: str
    citable: bool = False
    inspection_only: bool = False

    @field_validator("chunk_id")
    @classmethod
    def validate_chunk_id(cls, value: str) -> str:
        if not re.fullmatch(r"chk_[0-9a-f]{64}", value):
            raise ValueError("chunk_id must be a project stable ID")
        return value

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"evd_[0-9a-f]{64}", value):
            raise ValueError("evidence_id must be a project stable ID")
        return value


class KnowledgeSearchResult(RetrievalModel):
    """Search artifact. Empty retrieval remains an explicit empty tuple."""

    query: str
    hits: tuple[EvidenceHit, ...] = ()
    backend: str
    empty_reason: str | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_empty_reason(self) -> Self:
        if self.hits and self.empty_reason is not None:
            raise ValueError("non-empty results cannot carry empty_reason")
        if not self.hits and self.empty_reason is None:
            object.__setattr__(self, "empty_reason", "no_matching_knowledge")
        return self


class KnowledgeReadResult(RetrievalModel):
    """Stable-ID read artifact."""

    hit: EvidenceHit
    backend: str = "repository-read"
