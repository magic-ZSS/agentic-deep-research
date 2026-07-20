"""Versioned, scope-aware knowledge domain models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_deep_research.knowledge.ids import (
    blob_id_for,
    canonicalize_local_ref,
    canonicalize_text,
    canonicalize_uri,
    chunk_id_for,
    document_id_for,
    scope_id_for,
    sha256_bytes,
    source_id_for,
    validate_sha256,
    version_id_for,
)


DOMAIN_SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class DomainModel(BaseModel):
    """Strict base contract for durable domain data."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = DOMAIN_SCHEMA_VERSION


class Visibility(StrEnum):
    """Visibility boundaries understood by repository authorization."""

    PROJECT = "project"
    PRIVATE = "private"


class SourceKind(StrEnum):
    """Stable broad source categories; parsing remains a later phase."""

    WEB = "web"
    LOCAL_FILE = "local_file"
    PAST_QUERY = "past_query"
    OTHER = "other"


class AuthorityClass(StrEnum):
    """Descriptive authority labels, not automatic lifecycle policy."""

    OFFICIAL = "official"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    SELF_REPORTED = "self_reported"
    UNKNOWN = "unknown"


class VersionLifecycleStatus(StrEnum):
    """The only lifecycle state machine vocabulary in Phase 1."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class ChunkLocatorType(StrEnum):
    """Structured locator shapes supported before parser integration."""

    PAGE = "page"
    HEADING = "heading"
    ANCHOR = "anchor"
    TEXT = "text"


class KnowledgeScope(DomainModel):
    """Durable tenant/project/owner boundary for every repository operation."""

    scope_id: str = ""
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    owner_user_id: str | None = None
    visibility: Visibility = Visibility.PROJECT
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def populate_and_validate(self) -> Self:
        tenant_id = self.tenant_id.strip()
        project_id = self.project_id.strip()
        owner_user_id = self.owner_user_id.strip() if self.owner_user_id else None
        if not tenant_id or not project_id:
            raise ValueError("tenant_id and project_id cannot be blank")
        if self.visibility is Visibility.PRIVATE and not owner_user_id:
            raise ValueError("private scope requires owner_user_id")
        expected = scope_id_for(
            tenant_id, project_id, owner_user_id, self.visibility.value
        )
        if self.scope_id and self.scope_id != expected:
            raise ValueError("scope_id does not match scope identity")
        object.__setattr__(self, "scope_id", expected)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "owner_user_id", owner_user_id)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at))
        return self


class KnowledgeAccessContext(DomainModel):
    """Trusted identity supplied outside model/tool arguments."""

    trusted_tenant_id: str = Field(min_length=1)
    trusted_user_id: str | None = None
    trusted_project_id: str = Field(min_length=1)
    allowed_visibilities: tuple[Visibility, ...] = (
        Visibility.PROJECT,
        Visibility.PRIVATE,
    )
    auth_source: str = Field(min_length=1)
    request_id: str = Field(min_length=1)

    @field_validator("allowed_visibilities")
    @classmethod
    def normalize_visibilities(
        cls, value: tuple[Visibility, ...]
    ) -> tuple[Visibility, ...]:
        if not value:
            raise ValueError("at least one visibility must be allowed")
        return tuple(sorted(set(value), key=lambda item: item.value))

    @model_validator(mode="after")
    def normalize_identity(self) -> Self:
        values = {
            "trusted_tenant_id": self.trusted_tenant_id.strip(),
            "trusted_project_id": self.trusted_project_id.strip(),
            "trusted_user_id": (
                self.trusted_user_id.strip() if self.trusted_user_id else None
            ),
            "auth_source": self.auth_source.strip(),
            "request_id": self.request_id.strip(),
        }
        if any(not values[name] for name in ("trusted_tenant_id", "trusted_project_id", "auth_source", "request_id")):
            raise ValueError("access context identity fields cannot be blank")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        return self


class SourcePublicView(DomainModel):
    """Safe representation for reports and logs."""

    source_id: str
    kind: SourceKind
    public_display_uri: str | None
    display_name: str
    publisher: str | None
    authority_class: AuthorityClass


class Source(DomainModel):
    """Source identity separated from content versions and private storage."""

    source_id: str = ""
    scope_id: str
    kind: SourceKind
    identity_key: str = ""
    canonical_uri: str | None = None
    internal_storage_ref: str | None = Field(default=None, repr=False)
    public_display_uri: str | None = None
    display_name: str = Field(min_length=1)
    publisher: str | None = None
    authority_class: AuthorityClass = AuthorityClass.UNKNOWN
    created_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def canonicalize_identity(self) -> Self:
        canonical_uri = (
            canonicalize_uri(self.canonical_uri) if self.canonical_uri else None
        )
        internal_ref = (
            canonicalize_local_ref(self.internal_storage_ref)
            if self.internal_storage_ref
            else None
        )
        public_uri = (
            canonicalize_uri(self.public_display_uri)
            if self.public_display_uri
            else None
        )
        identity_key = canonical_uri or internal_ref or public_uri
        if not identity_key:
            raise ValueError("source requires a canonical URI or internal storage ref")
        expected = source_id_for(self.scope_id, self.kind.value, identity_key)
        if self.identity_key and self.identity_key != identity_key:
            raise ValueError("identity_key does not match canonical source identity")
        if self.source_id and self.source_id != expected:
            raise ValueError("source_id does not match source identity")
        object.__setattr__(self, "canonical_uri", canonical_uri)
        object.__setattr__(self, "internal_storage_ref", internal_ref)
        object.__setattr__(self, "public_display_uri", public_uri)
        object.__setattr__(self, "identity_key", identity_key)
        object.__setattr__(self, "source_id", expected)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at))
        object.__setattr__(self, "soft_deleted_at", _aware_utc(self.soft_deleted_at))
        return self

    def public_view(self) -> SourcePublicView:
        """Return a representation that can never expose an internal path."""
        return SourcePublicView(
            source_id=self.source_id,
            kind=self.kind,
            public_display_uri=self.public_display_uri,
            display_name=self.display_name,
            publisher=self.publisher,
            authority_class=self.authority_class,
        )


class Document(DomainModel):
    """Logical document attached to one source."""

    document_id: str = ""
    scope_id: str
    source_id: str
    logical_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        logical_key = canonicalize_text(self.logical_key).strip()
        if not logical_key:
            raise ValueError("logical_key cannot be blank")
        expected = document_id_for(self.scope_id, self.source_id, logical_key)
        if self.document_id and self.document_id != expected:
            raise ValueError("document_id does not match document identity")
        object.__setattr__(self, "logical_key", logical_key)
        object.__setattr__(self, "document_id", expected)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at))
        object.__setattr__(self, "soft_deleted_at", _aware_utc(self.soft_deleted_at))
        return self


class ContentBlob(DomainModel):
    """Immutable content-addressed original bytes within one scope."""

    blob_id: str = ""
    scope_id: str
    content_sha256: str
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    storage_ref: str = Field(min_length=1, repr=False)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        digest = validate_sha256(self.content_sha256)
        expected = blob_id_for(self.scope_id, digest)
        if self.blob_id and self.blob_id != expected:
            raise ValueError("blob_id does not match scope/content identity")
        storage_ref = self.storage_ref.replace("\\", "/")
        if PureWindowsPath(storage_ref).is_absolute() or PurePosixPath(
            storage_ref
        ).is_absolute():
            raise ValueError("ContentBlob.storage_ref must be root-relative")
        if ".." in PurePosixPath(storage_ref).parts:
            raise ValueError("ContentBlob.storage_ref cannot traverse its root")
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "blob_id", expected)
        object.__setattr__(self, "storage_ref", storage_ref)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at))
        return self

    @classmethod
    def from_bytes(
        cls,
        *,
        scope_id: str,
        content: bytes,
        media_type: str,
        storage_ref: str,
    ) -> ContentBlob:
        return cls(
            scope_id=scope_id,
            content_sha256=sha256_bytes(content),
            byte_size=len(content),
            media_type=media_type,
            storage_ref=storage_ref,
        )


class DocumentVersion(DomainModel):
    """Immutable snapshot metadata for a logical document."""

    version_id: str = ""
    scope_id: str
    document_id: str
    blob_id: str
    content_sha256: str
    version_number: int = Field(ge=1)
    retrieved_at: datetime
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_version_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    lifecycle_status: VersionLifecycleStatus = VersionLifecycleStatus.CANDIDATE
    created_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        digest = validate_sha256(self.content_sha256)
        expected = version_id_for(self.scope_id, self.document_id, digest)
        if self.version_id and self.version_id != expected:
            raise ValueError("version_id does not match document/content identity")
        for name in (
            "retrieved_at",
            "published_at",
            "valid_from",
            "valid_to",
            "created_at",
            "soft_deleted_at",
        ):
            object.__setattr__(self, name, _aware_utc(getattr(self, name)))
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "version_id", expected)
        return self


class ChunkInput(DomainModel):
    """Caller-supplied immutable chunk contents before ID assignment."""

    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    locator_type: ChunkLocatorType = ChunkLocatorType.TEXT
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    heading_path: tuple[str, ...] = ()
    anchor: str | None = None
    token_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        headings = tuple(canonicalize_text(item).strip() for item in self.heading_path)
        if any(not item for item in headings):
            raise ValueError("heading_path entries cannot be empty")
        anchor = canonicalize_text(self.anchor).strip() if self.anchor else None
        if self.locator_type is ChunkLocatorType.PAGE:
            if self.page_start is None:
                raise ValueError("page locator requires page_start")
            page_end = self.page_end or self.page_start
            if page_end < self.page_start:
                raise ValueError("page_end cannot precede page_start")
            if headings:
                raise ValueError("page locator cannot include heading_path")
            object.__setattr__(self, "page_end", page_end)
        elif self.locator_type is ChunkLocatorType.HEADING:
            if not headings:
                raise ValueError("heading locator requires heading_path")
            if self.page_start is not None or self.page_end is not None:
                raise ValueError("heading locator cannot include page fields")
        elif self.locator_type is ChunkLocatorType.ANCHOR:
            if not anchor:
                raise ValueError("anchor locator requires anchor")
            if headings or self.page_start is not None or self.page_end is not None:
                raise ValueError("anchor locator cannot include page/heading fields")
        elif any(
            (
                headings,
                anchor,
                self.page_start is not None,
                self.page_end is not None,
            )
        ):
            raise ValueError("text locator cannot include page/heading/anchor fields")
        object.__setattr__(self, "text", canonicalize_text(self.text))
        object.__setattr__(self, "heading_path", headings)
        object.__setattr__(self, "anchor", anchor)
        return self

    def locator_key(self) -> str:
        """Return an unambiguous canonical representation for stable IDs."""
        return json.dumps(
            {
                "anchor": self.anchor,
                "heading_path": self.heading_path,
                "page_end": self.page_end,
                "page_start": self.page_start,
                "type": self.locator_type.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class Chunk(ChunkInput):
    """Immutable, addressable location inside one DocumentVersion."""

    chunk_id: str = ""
    scope_id: str
    version_id: str
    text_sha256: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None

    @model_validator(mode="after")
    def populate_chunk_identity(self) -> Self:
        digest = sha256_bytes(self.text.encode("utf-8"))
        if self.text_sha256 and validate_sha256(self.text_sha256) != digest:
            raise ValueError("text_sha256 does not match canonical chunk text")
        expected = chunk_id_for(
            self.scope_id,
            self.version_id,
            self.ordinal,
            digest,
            self.locator_key(),
        )
        if self.chunk_id and self.chunk_id != expected:
            raise ValueError("chunk_id does not match chunk identity")
        object.__setattr__(self, "text_sha256", digest)
        object.__setattr__(self, "chunk_id", expected)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at))
        object.__setattr__(self, "soft_deleted_at", _aware_utc(self.soft_deleted_at))
        return self
