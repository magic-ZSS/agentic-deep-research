"""Small deterministic character chunking helpers shared by local parsers."""

from __future__ import annotations

from dataclasses import dataclass

from open_deep_research.knowledge.ids import canonicalize_text
from open_deep_research.knowledge.ingestion.parsers.models import ChunkingConfig


def character_windows(text: str, config: ChunkingConfig) -> tuple[tuple[int, int, str], ...]:
    """Split normalized text into stable overlapping character windows."""
    normalized = canonicalize_text(text).strip()
    if not normalized:
        return ()
    windows: list[tuple[int, int, str]] = []
    start = 0
    while start < len(normalized):
        end = min(start + config.max_chars, len(normalized))
        value = normalized[start:end].strip()
        if value:
            windows.append((start, end, value))
        if end == len(normalized):
            break
        start = end - config.overlap
    return tuple(windows)


@dataclass(frozen=True)
class PageSpan:
    """Character offsets belonging to a one-indexed PDF page."""

    page_number: int
    start: int
    end: int


def page_windows(
    pages: tuple[tuple[int, str], ...], config: ChunkingConfig
) -> tuple[tuple[str, int, int], ...]:
    """Chunk page text while retaining the inclusive page range of each window."""
    pieces: list[str] = []
    spans: list[PageSpan] = []
    offset = 0
    for page_number, raw_text in pages:
        text = canonicalize_text(raw_text).strip()
        if not text:
            continue
        if pieces:
            pieces.append("\n")
            offset += 1
        start = offset
        pieces.append(text)
        offset += len(text)
        spans.append(PageSpan(page_number=page_number, start=start, end=offset))
    combined = "".join(pieces)
    output: list[tuple[str, int, int]] = []
    for start, end, text in character_windows(combined, config):
        intersected = [
            span.page_number
            for span in spans
            if span.end > start and span.start < end
        ]
        if intersected:
            output.append((text, min(intersected), max(intersected)))
    return tuple(output)
