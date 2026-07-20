"""Markdown parser that preserves an explicit ATX heading hierarchy."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import ClassVar

from open_deep_research.knowledge.ingestion.parsers.base import (
    DocumentParseError,
    ParseErrorCode,
    decode_utf8,
)
from open_deep_research.knowledge.ingestion.parsers.chunking import character_windows
from open_deep_research.knowledge.ingestion.parsers.models import (
    ChunkingConfig,
    DocumentInput,
    ParsedDocument,
    ParserChunk,
    ParserLocator,
    ParserLocatorType,
)


_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass
class _Section:
    heading_path: tuple[str, ...]
    lines: list[str]
    heading_level: int | None
    occurrence: int
    synthetic_heading: bool = False


class MarkdownParser:
    """Parse Markdown without rendering it or treating fenced code as headings."""

    name: ClassVar[str] = "markdown_heading"
    version: ClassVar[str] = "1"
    _media_types: ClassVar[frozenset[str]] = frozenset(
        {"text/markdown", "text/x-markdown"}
    )

    def supports(self, media_type: str, suffix: str) -> bool:
        return media_type.partition(";")[0].strip().lower() in self._media_types or suffix.lower() in {".md", ".markdown"}

    def parse(
        self,
        document: DocumentInput,
        chunking: ChunkingConfig | None = None,
    ) -> ParsedDocument:
        text = decode_utf8(document, self.name).replace("\r\n", "\n").replace("\r", "\n")
        sections = self._sections(text, document.display_name)
        config = chunking or ChunkingConfig()
        chunks: list[ParserChunk] = []
        for section in sections:
            section_text = "\n".join(section.lines).strip()
            for section_chunk_index, (_, _, value) in enumerate(
                character_windows(section_text, config)
            ):
                chunks.append(
                    ParserChunk(
                        ordinal=len(chunks),
                        text=value,
                        locator=ParserLocator(
                            type=ParserLocatorType.HEADING,
                            heading_path=section.heading_path,
                        ),
                        metadata={
                            "heading_level": section.heading_level,
                            "heading_occurrence": section.occurrence,
                            "section_chunk_index": section_chunk_index,
                            "synthetic_heading": section.synthetic_heading,
                        },
                    )
                )
        if not chunks:
            raise DocumentParseError(
                ParseErrorCode.NO_TEXT, self.name, "Markdown snapshot contains no text"
            )
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            media_type=document.normalized_media_type,
            title=document.display_name,
            canonical_uri=document.canonical_uri,
            chunks=tuple(chunks),
            metadata={
                "heading_syntax": "atx_h1_h6",
                "source_input_ref": document.input_ref,
            },
        )

    @staticmethod
    def _sections(text: str, fallback_heading: str) -> tuple[_Section, ...]:
        sections: list[_Section] = []
        stack: list[str] = []
        occurrences: Counter[tuple[str, ...]] = Counter()
        current = _Section(
            heading_path=(fallback_heading,),
            lines=[],
            heading_level=None,
            occurrence=1,
            synthetic_heading=True,
        )
        fence_marker: str | None = None
        fence_length = 0
        for line in text.split("\n"):
            fence_match = _FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence_marker is None:
                    fence_marker, fence_length = marker[0], len(marker)
                elif marker[0] == fence_marker and len(marker) >= fence_length:
                    fence_marker, fence_length = None, 0
                current.lines.append(line)
                continue
            heading_match = None if fence_marker else _HEADING.match(line)
            if not heading_match:
                current.lines.append(line)
                continue
            if any(part.strip() for part in current.lines):
                sections.append(current)
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            stack = stack[: level - 1]
            stack.append(heading)
            path = tuple(stack)
            occurrences[path] += 1
            current = _Section(
                heading_path=path,
                lines=[line],
                heading_level=level,
                occurrence=occurrences[path],
            )
        if any(part.strip() for part in current.lines):
            sections.append(current)
        return tuple(sections)
