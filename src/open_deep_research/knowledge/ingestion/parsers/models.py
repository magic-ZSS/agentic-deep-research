"""Pure input and output contracts for deterministic local document parsers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_deep_research.knowledge.ids import canonicalize_text, canonicalize_uri
from open_deep_research.knowledge.models import (
    ChunkInput,
    ChunkLocatorType,
    SourceKind,
)


PARSER_SCHEMA_VERSION = "1.0"


class ParserModel(BaseModel):
    """Strict, immutable base model for the parser boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentInput(ParserModel):
    """An already-authorized immutable byte snapshot supplied to a parser.

    ``input_ref`` is identity/audit metadata only. Parsers must never open it.
    The application service is responsible for allowed-root checks and reading bytes.
    """

    source_kind: SourceKind
    media_type: str = Field(min_length=1)
    input_ref: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    canonical_uri: str | None = None
    raw_bytes: bytes
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_bytes", mode="before")
    @classmethod
    def require_exact_bytes(cls, value: object) -> bytes:
        if not isinstance(value, bytes):
            raise ValueError("raw_bytes must be bytes supplied by the import service")
        return value

    @model_validator(mode="after")
    def normalize_identity_metadata(self) -> Self:
        media_type = self.media_type.strip().lower()
        input_ref = canonicalize_text(self.input_ref).strip()
        display_name = canonicalize_text(self.display_name).strip()
        if not media_type or not input_ref or not display_name:
            raise ValueError("media_type, input_ref, and display_name cannot be blank")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "input_ref", input_ref)
        object.__setattr__(self, "display_name", display_name)
        if self.canonical_uri:
            object.__setattr__(self, "canonical_uri", canonicalize_uri(self.canonical_uri))
        return self

    @property
    def normalized_media_type(self) -> str:
        """Return the MIME type without optional parameters."""
        return self.media_type.partition(";")[0].strip()

    @property
    def suffix(self) -> str:
        """Return a lexical suffix without touching the filesystem."""
        path = urlsplit(self.input_ref).path.replace("\\", "/")
        return PurePosixPath(path).suffix.lower()


class ChunkingConfig(ParserModel):
    """Deterministic character-window chunking settings."""

    max_chars: int = Field(default=4_000, ge=1, le=1_000_000)
    overlap: int = Field(default=200, ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.overlap >= self.max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        return self


class ParserLocatorType(StrEnum):
    """Locator vocabulary emitted by Phase 2 parsers."""

    PAGE = "page"
    HEADING = "heading"
    HTML_ANCHOR = "html_anchor"
    QUERY_RECORD = "query_record"


class ParserLocator(ParserModel):
    """A parser-native locator before persistence as a Phase 1 ChunkInput."""

    type: ParserLocatorType
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    heading_path: tuple[str, ...] = ()
    anchor: str | None = None
    record_id: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        headings = tuple(canonicalize_text(value).strip() for value in self.heading_path)
        anchor = canonicalize_text(self.anchor).strip() if self.anchor else None
        record_id = canonicalize_text(self.record_id).strip() if self.record_id else None
        if any(not value for value in headings):
            raise ValueError("heading_path entries cannot be blank")
        if self.type is ParserLocatorType.PAGE:
            if self.page_start is None:
                raise ValueError("page locator requires page_start")
            page_end = self.page_end or self.page_start
            if page_end < self.page_start:
                raise ValueError("page_end cannot precede page_start")
            if headings or anchor or record_id:
                raise ValueError("page locator cannot contain other locator fields")
            object.__setattr__(self, "page_end", page_end)
        elif self.type is ParserLocatorType.HEADING:
            if not headings:
                raise ValueError("heading locator requires heading_path")
            if self.page_start is not None or self.page_end is not None or anchor or record_id:
                raise ValueError("heading locator cannot contain other locator fields")
        elif self.type is ParserLocatorType.HTML_ANCHOR:
            if not anchor:
                raise ValueError("HTML locator requires anchor")
            if self.page_start is not None or self.page_end is not None or record_id:
                raise ValueError("HTML locator cannot contain page or record fields")
        elif self.type is ParserLocatorType.QUERY_RECORD:
            if not record_id:
                raise ValueError("query record locator requires record_id")
            if self.page_start is not None or self.page_end is not None or headings or anchor:
                raise ValueError("query record locator cannot contain other locator fields")
        object.__setattr__(self, "heading_path", headings)
        object.__setattr__(self, "anchor", anchor)
        object.__setattr__(self, "record_id", record_id)
        return self


class ParserChunk(ParserModel):
    """Text plus its explicit source locator, before repository ID assignment."""

    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    locator: ParserLocator
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = canonicalize_text(value).strip()
        if not normalized:
            raise ValueError("chunk text cannot be blank")
        return normalized

    def to_chunk_input(self) -> ChunkInput:
        """Map the rich parser locator to the backward-compatible Phase 1 model."""
        metadata = dict(self.metadata)
        metadata["parser_locator_type"] = self.locator.type.value
        common: dict[str, Any] = {
            "ordinal": self.ordinal,
            "text": self.text,
            "metadata": metadata,
        }
        if self.locator.type is ParserLocatorType.PAGE:
            return ChunkInput(
                **common,
                locator_type=ChunkLocatorType.PAGE,
                page_start=self.locator.page_start,
                page_end=self.locator.page_end,
            )
        if self.locator.type is ParserLocatorType.HEADING:
            return ChunkInput(
                **common,
                locator_type=ChunkLocatorType.HEADING,
                heading_path=self.locator.heading_path,
            )
        if self.locator.type is ParserLocatorType.HTML_ANCHOR:
            metadata["heading_path"] = list(self.locator.heading_path)
            return ChunkInput(
                **common,
                locator_type=ChunkLocatorType.ANCHOR,
                anchor=self.locator.anchor,
            )
        metadata["record_id"] = self.locator.record_id
        return ChunkInput(**common, locator_type=ChunkLocatorType.TEXT)


class ParsedDocument(ParserModel):
    """Deterministic parser result independent of repositories and PaperQA."""

    schema_version: Literal["1.0"] = PARSER_SCHEMA_VERSION
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    title: str | None = None
    canonical_uri: str | None = None
    chunks: tuple[ParserChunk, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_chunks(self) -> Self:
        if not self.chunks:
            raise ValueError("parsed document must contain at least one chunk")
        ordinals = tuple(chunk.ordinal for chunk in self.chunks)
        if ordinals != tuple(range(len(self.chunks))):
            raise ValueError("parsed chunk ordinals must be contiguous and zero-based")
        title = canonicalize_text(self.title).strip() if self.title else None
        object.__setattr__(self, "title", title)
        if self.canonical_uri:
            object.__setattr__(self, "canonical_uri", canonicalize_uri(self.canonical_uri))
        return self

    def chunk_inputs(self) -> tuple[ChunkInput, ...]:
        """Return Phase 1 persistence inputs without losing parser metadata."""
        return tuple(chunk.to_chunk_input() for chunk in self.chunks)
