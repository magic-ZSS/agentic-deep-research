"""Deterministic local-only document parsers for candidate ingestion."""

from open_deep_research.knowledge.ingestion.parsers.base import (
    DocumentParseError,
    DocumentParser,
    ParseErrorCode,
)
from open_deep_research.knowledge.ingestion.parsers.html import HtmlSnapshotParser
from open_deep_research.knowledge.ingestion.parsers.markdown import MarkdownParser
from open_deep_research.knowledge.ingestion.parsers.models import (
    ChunkingConfig,
    DocumentInput,
    ParsedDocument,
    ParserChunk,
    ParserLocator,
    ParserLocatorType,
)
from open_deep_research.knowledge.ingestion.parsers.past_query import PastQueryParser
from open_deep_research.knowledge.ingestion.parsers.pdf import PdfParser

__all__ = [
    "ChunkingConfig",
    "DocumentInput",
    "DocumentParseError",
    "DocumentParser",
    "HtmlSnapshotParser",
    "MarkdownParser",
    "ParseErrorCode",
    "ParsedDocument",
    "ParserChunk",
    "ParserLocator",
    "ParserLocatorType",
    "PastQueryParser",
    "PdfParser",
]
