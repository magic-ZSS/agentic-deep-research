"""Static HTML snapshot parser with deterministic anchors and no fetching."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import ClassVar

from bs4 import BeautifulSoup, Tag

from open_deep_research.knowledge.ids import canonicalize_uri
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


_HEADING_TAGS = {f"h{level}" for level in range(1, 7)}
_CONTENT_TAGS = _HEADING_TAGS | {"p", "li", "pre", "blockquote", "table", "dt", "dd"}


@dataclass
class _HtmlSection:
    anchor: str
    heading_path: tuple[str, ...]
    lines: list[str]
    generated_anchor: bool
    anchor_occurrence: int


def _slug(value: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", value.casefold(), flags=re.UNICODE).strip("-_")
    return slug or "section"


class HtmlSnapshotParser:
    """Parse only supplied HTML bytes; links and canonical URLs are never fetched."""

    name: ClassVar[str] = "html_snapshot"
    version: ClassVar[str] = "1"
    _media_types: ClassVar[frozenset[str]] = frozenset(
        {"text/html", "application/xhtml+xml"}
    )

    def supports(self, media_type: str, suffix: str) -> bool:
        return media_type.partition(";")[0].strip().lower() in self._media_types or suffix.lower() in {".html", ".htm"}

    def parse(
        self,
        document: DocumentInput,
        chunking: ChunkingConfig | None = None,
    ) -> ParsedDocument:
        text = decode_utf8(document, self.name)
        soup = BeautifulSoup(text, "html.parser")
        removed = 0
        for tag in soup.find_all(["script", "style", "noscript", "template"]):
            tag.decompose()
            removed += 1
        title = (
            soup.title.get_text(" ", strip=True)
            if soup.title and soup.title.get_text(" ", strip=True)
            else document.display_name
        )
        canonical_uri = document.canonical_uri or self._document_canonical_uri(soup)
        sections = self._sections(soup, title)
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
                            type=ParserLocatorType.HTML_ANCHOR,
                            anchor=section.anchor,
                            heading_path=section.heading_path,
                        ),
                        metadata={
                            "anchor_generated": section.generated_anchor,
                            "anchor_occurrence": section.anchor_occurrence,
                            "section_chunk_index": section_chunk_index,
                            "canonical_uri": canonical_uri,
                            "heading_path": list(section.heading_path),
                        },
                    )
                )
        if not chunks:
            raise DocumentParseError(
                ParseErrorCode.NO_TEXT, self.name, "HTML snapshot contains no readable text"
            )
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            media_type=document.normalized_media_type,
            title=title,
            canonical_uri=canonical_uri,
            chunks=tuple(chunks),
            metadata={
                "canonical_uri": canonical_uri,
                "removed_non_content_tags": removed,
                "source_input_ref": document.input_ref,
            },
        )

    @staticmethod
    def _document_canonical_uri(soup: BeautifulSoup) -> str | None:
        link = soup.find("link", rel=lambda value: value and "canonical" in value)
        if not isinstance(link, Tag):
            return None
        href = link.get("href")
        if not isinstance(href, str):
            return None
        try:
            return canonicalize_uri(href)
        except ValueError:
            return None

    @staticmethod
    def _sections(soup: BeautifulSoup, fallback_heading: str) -> tuple[_HtmlSection, ...]:
        container = soup.body or soup
        stack: list[str] = []
        anchor_counts: Counter[str] = Counter()
        current = _HtmlSection(
            anchor="document",
            heading_path=(fallback_heading,),
            lines=[],
            generated_anchor=True,
            anchor_occurrence=1,
        )
        sections: list[_HtmlSection] = []
        for tag in container.find_all(list(_CONTENT_TAGS)):
            if not isinstance(tag, Tag):
                continue
            if tag.name not in _HEADING_TAGS and tag.find_parent(_CONTENT_TAGS - _HEADING_TAGS):
                continue
            value = tag.get_text(" ", strip=True)
            if not value:
                continue
            if tag.name not in _HEADING_TAGS:
                current.lines.append(value)
                continue
            if current.lines:
                sections.append(current)
            level = int(tag.name[1])
            stack = stack[: level - 1]
            stack.append(value)
            explicit_anchor = tag.get("id")
            generated = not isinstance(explicit_anchor, str) or not explicit_anchor.strip()
            base_anchor = _slug(value) if generated else explicit_anchor.strip()
            anchor_counts[base_anchor] += 1
            occurrence = anchor_counts[base_anchor]
            anchor = (
                f"{base_anchor}-{occurrence}"
                if generated and occurrence > 1
                else base_anchor
            )
            current = _HtmlSection(
                anchor=anchor,
                heading_path=tuple(stack),
                lines=[value],
                generated_anchor=generated,
                anchor_occurrence=occurrence,
            )
        if current.lines:
            sections.append(current)
        if not sections:
            fallback_text = container.get_text(" ", strip=True)
            if fallback_text:
                sections.append(
                    _HtmlSection(
                        anchor="document",
                        heading_path=(fallback_heading,),
                        lines=[fallback_text],
                        generated_anchor=True,
                        anchor_occurrence=1,
                    )
                )
        return tuple(sections)
