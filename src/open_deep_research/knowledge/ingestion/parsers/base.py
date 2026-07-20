"""Parser protocol and structured deterministic parsing errors."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from open_deep_research.knowledge.ingestion.parsers.models import (
    ChunkingConfig,
    DocumentInput,
    ParsedDocument,
)


class ParseErrorCode(StrEnum):
    """Stable error codes suitable for ImportJob diagnostics."""

    EMPTY_INPUT = "empty_input"
    INVALID_ENCODING = "invalid_encoding"
    INVALID_DOCUMENT = "invalid_document"
    NO_TEXT = "no_text"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNVERIFIED_RECORD = "unverified_record"
    MISSING_SCOPE = "missing_scope"
    MISSING_EVIDENCE = "missing_evidence"


class DocumentParseError(ValueError):
    """A structured parser failure that never fabricates fallback content."""

    def __init__(self, code: ParseErrorCode, parser_name: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.parser_name = parser_name
        self.message = message

    def as_dict(self) -> dict[str, str]:
        """Return stable data suitable for a failed ImportJob."""
        return {
            "code": self.code.value,
            "parser_name": self.parser_name,
            "message": self.message,
        }


@runtime_checkable
class DocumentParser(Protocol):
    """Synchronous, local-only parser contract."""

    name: str
    version: str

    def supports(self, media_type: str, suffix: str) -> bool: ...

    def parse(
        self,
        document: DocumentInput,
        chunking: ChunkingConfig | None = None,
    ) -> ParsedDocument: ...


def decode_utf8(document: DocumentInput, parser_name: str) -> str:
    """Decode a UTF-8 snapshot deterministically, accepting an optional BOM."""
    if not document.raw_bytes:
        raise DocumentParseError(
            ParseErrorCode.EMPTY_INPUT, parser_name, "document snapshot is empty"
        )
    try:
        return document.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            ParseErrorCode.INVALID_ENCODING,
            parser_name,
            "document snapshot is not valid UTF-8",
        ) from exc
