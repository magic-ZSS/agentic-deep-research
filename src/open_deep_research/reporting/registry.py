"""Deterministic source/version registry construction."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath, PureWindowsPath

from open_deep_research.evidence.validation.resolver import ResolvedEvidence
from open_deep_research.knowledge.models import SourceKind
from open_deep_research.reporting.models import (
    CitationKey,
    SourceRegistryEntry,
    ValidationResult,
    ValidationStatus,
)


class SourceRegistryBuilder:
    """Assign contiguous numbers after every claim has been validated."""

    def build(
        self,
        results: tuple[ValidationResult, ...],
        resolved_by_id: dict[str, ResolvedEvidence],
    ) -> tuple[SourceRegistryEntry, ...]:
        """Deduplicate by source/version while merging public locators."""
        locators: dict[CitationKey, set[str]] = defaultdict(set)
        used: dict[CitationKey, ResolvedEvidence] = {}
        for result in results:
            if result.status not in {
                ValidationStatus.FULLY_SUPPORTED,
                ValidationStatus.PARTIALLY_SUPPORTED,
            }:
                continue
            for link in result.links:
                if not link.accepted:
                    continue
                resolved = resolved_by_id[link.evidence_id]
                used[link.citation_key] = resolved
                locators[link.citation_key].add(link.locator)
        entries: list[SourceRegistryEntry] = []
        for number, key in enumerate(
            sorted(used, key=lambda item: (item.source_id, item.version_id)),
            start=1,
        ):
            resolved = used[key]
            entries.append(
                SourceRegistryEntry(
                    citation_key=key,
                    display_number=number,
                    title=_safe_title(resolved),
                    publisher=resolved.source.publisher,
                    canonical_uri=resolved.public_uri(),
                    published_at=resolved.version.published_at,
                    retrieved_at=resolved.version.retrieved_at,
                    locators_used=tuple(sorted(locators[key])),
                )
            )
        return tuple(entries)


def _safe_title(resolved: ResolvedEvidence) -> str:
    title = resolved.document.title or resolved.source.display_name
    if resolved.source.kind is SourceKind.LOCAL_FILE:
        title = PureWindowsPath(title).name
        title = PurePosixPath(title).name
    return title or resolved.source.source_id
