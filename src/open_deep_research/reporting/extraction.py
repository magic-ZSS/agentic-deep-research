"""Deterministic draft parsing and fake-friendly atomic claim extraction."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Sequence
from typing import Protocol

from open_deep_research.reporting.models import (
    AtomicClaim,
    CitationKey,
    ClaimType,
    DraftReport,
    ReportSection,
)

EVIDENCE_MARKER = re.compile(r"\[\[evidence:([A-Za-z0-9_.:-]+)\]\]")
CITATION_MARKER = re.compile(
    r"\[\[citation:([A-Za-z0-9_.:-]+)\|([A-Za-z0-9_.:-]+)\]\]"
)
LEGACY_MARKER = re.compile(r"(?:\bSOURCE\s+\d+\b|(?<!\[)\[\d+\](?!\]))")
DIAGNOSTIC_LINE = re.compile(
    r"^\s*(?:think_tool|tool_error|error ToolMessage|ResearchComplete)\s*:",
    re.IGNORECASE,
)


class ClaimExtractionAdapter(Protocol):
    """Optional structured extractor boundary; tests inject a fake."""

    def extract(
        self, draft: DraftReport, requirement_ids: tuple[str, ...]
    ) -> Sequence[AtomicClaim] | Awaitable[Sequence[AtomicClaim]]:
        """Extract checkpoint-safe claims from a parsed draft."""
        ...


def parse_draft(text: str) -> DraftReport:
    """Split Markdown into stable sections and remove diagnostic contamination."""
    lines = text.splitlines()
    sections: list[ReportSection] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        cleaned = "\n".join(
            line for line in body if not DIAGNOSTIC_LINE.match(line)
        ).strip()
        if cleaned or heading:
            sections.append(
                ReportSection(ordinal=len(sections), heading=heading, text=cleaned)
            )
        body = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            heading = f"{match.group(1)} {match.group(2)}"
        else:
            body.append(line)
    flush()
    if not sections:
        sections = [ReportSection(ordinal=0, heading="", text="")]
    return DraftReport(raw_text=text, sections=tuple(sections))


class DeterministicClaimExtractor:
    """Extract sentence-level claims while keeping their local marker bindings."""

    version = "deterministic-v1"

    async def extract(
        self, draft: DraftReport, requirement_ids: tuple[str, ...] = ()
    ) -> tuple[AtomicClaim, ...]:
        """Extract claims independently within each section boundary."""
        claims: list[AtomicClaim] = []
        for section in draft.sections:
            for start, _end, raw in _sentence_spans(section.text):
                evidence_ids = tuple(sorted(set(EVIDENCE_MARKER.findall(raw))))
                citation_keys = tuple(
                    CitationKey(source_id=source, version_id=version)
                    for source, version in sorted(set(CITATION_MARKER.findall(raw)))
                )
                cleaned = _normalize_visible_text(
                    LEGACY_MARKER.sub(
                        "", CITATION_MARKER.sub("", EVIDENCE_MARKER.sub("", raw))
                    )
                )
                if not cleaned or _is_sources_line(cleaned):
                    continue
                for fragment, offset in _atomic_fragments(cleaned):
                    # Marker removal can alter offsets; the span remains section-local
                    # and deterministic and repair is hash guarded.
                    claim_start = start + max(0, raw.find(fragment.split()[0])) + offset
                    claims.append(
                        AtomicClaim(
                            requirement_ids=requirement_ids,
                            section_id=section.section_id,
                            text=fragment,
                            span_start=claim_start,
                            span_end=claim_start + len(fragment),
                            claim_type=_claim_type(fragment),
                            cited_evidence_ids=evidence_ids,
                            cited_citation_keys=citation_keys,
                            extraction_version=self.version,
                        )
                    )
        return tuple(
            sorted(claims, key=lambda item: (item.section_id, item.span_start, item.claim_id))
        )


def strip_untrusted_citation_syntax(text: str) -> str:
    """Remove legacy/model-owned numbers and internal evidence placeholders."""
    value = LEGACY_MARKER.sub("", text)
    value = EVIDENCE_MARKER.sub("", value)
    value = CITATION_MARKER.sub("", value)
    return _normalize_visible_text(value)


def _normalize_visible_text(text: str) -> str:
    """Remove marker-created whitespace without changing substantive words."""
    value = re.sub(r"[ \t]+([,.;:!?，。；：！？])", r"\1", text)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r" {2,}", " ", value)
    return value.strip(" \t\r\n-")


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^\n.!?。！？]+(?:[.!?。！？]+|$)", text):
        raw = match.group(0).strip()
        if raw:
            left = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
            spans.append((left, match.end(), raw))
    return spans


def _atomic_fragments(text: str) -> list[tuple[str, int]]:
    """Split semicolon/list conjunctions only when both sides are substantive."""
    parts = [part.strip() for part in re.split(r"[;；]", text) if part.strip()]
    if len(parts) <= 1:
        return [(text, 0)]
    output: list[tuple[str, int]] = []
    cursor = 0
    for part in parts:
        offset = text.find(part, cursor)
        output.append((part, offset))
        cursor = offset + len(part)
    return output


def _claim_type(text: str) -> ClaimType:
    lower = text.lower()
    if re.search(r"(?<!\w)[+-]?\d+(?:\.\d+)?%?", text):
        return ClaimType.NUMERIC
    if any(token in lower for token in (" claims ", "states that", "宣称", "声称")):
        return ClaimType.CORPORATE_ATTRIBUTION
    if any(token in lower for token in ("in my view", "subjectively", "我认为")):
        return ClaimType.SUBJECTIVE
    return ClaimType.FACTUAL


def _is_sources_line(text: str) -> bool:
    return bool(re.match(r"^\[?\d+\]?\s+https?://", text))
