"""Programmatic citation marker and source-table rendering."""

from __future__ import annotations

import re

from open_deep_research.reporting.extraction import strip_untrusted_citation_syntax
from open_deep_research.reporting.models import (
    AtomicClaim,
    ReportSection,
    SourceRegistryEntry,
    ValidationResult,
    ValidationStatus,
)


class ReportRenderer:
    """Render only validated links using registry-owned numbers."""

    def render(
        self,
        sections: tuple[ReportSection, ...],
        claims: tuple[AtomicClaim, ...],
        results: tuple[ValidationResult, ...],
        registry: tuple[SourceRegistryEntry, ...],
    ) -> str:
        """Return a report with zero orphan or unused source entries."""
        numbers = {
            entry.citation_key: entry.display_number for entry in registry
        }
        result_by_claim = {result.claim_id: result for result in results}
        claims_by_section: dict[str, list[AtomicClaim]] = {}
        for claim in claims:
            claims_by_section.setdefault(claim.section_id, []).append(claim)

        rendered_sections: list[str] = []
        for section in sorted(sections, key=lambda item: item.ordinal):
            text = strip_untrusted_citation_syntax(section.text)
            for claim in sorted(
                claims_by_section.get(section.section_id, []),
                key=lambda item: item.span_start,
                reverse=True,
            ):
                result = result_by_claim.get(claim.claim_id)
                if result is None or result.status not in {
                    ValidationStatus.FULLY_SUPPORTED,
                    ValidationStatus.PARTIALLY_SUPPORTED,
                }:
                    continue
                cited = sorted(
                    {
                        numbers[link.citation_key]
                        for link in result.links
                        if link.accepted and link.citation_key in numbers
                    }
                )
                if cited and claim.text in text:
                    marker = "".join(f"[{number}]" for number in cited)
                    text = _replace_last(text, claim.text, f"{claim.text}{marker}")
            block = "\n\n".join(part for part in (section.heading, text) if part)
            if block:
                rendered_sections.append(block)
        body = "\n\n".join(rendered_sections).strip()
        if registry:
            sources = "\n".join(_render_entry(entry) for entry in registry)
            body += f"\n\n### Sources\n\n{sources}"
        validate_registry_consistency(body, registry)
        return body


def validate_registry_consistency(
    report: str, registry: tuple[SourceRegistryEntry, ...]
) -> None:
    """Reject orphan markers, missing entries, gaps, and duplicate numbers."""
    body, separator, table = report.partition("### Sources")
    markers = {int(value) for value in re.findall(r"\[(\d+)\]", body)}
    entry_numbers = [entry.display_number for entry in registry]
    expected = list(range(1, len(registry) + 1))
    if entry_numbers != expected:
        raise ValueError("source registry numbers must be contiguous and ordered")
    if markers != set(entry_numbers):
        raise ValueError("body markers and source registry are not bidirectionally equal")
    if registry and not separator:
        raise ValueError("source table is missing")
    table_numbers = {int(value) for value in re.findall(r"^\[(\d+)\]", table, re.MULTILINE)}
    if table_numbers != set(entry_numbers):
        raise ValueError("rendered source table does not match registry")


def _render_entry(entry: SourceRegistryEntry) -> str:
    uri = entry.canonical_uri or f"source://{entry.citation_key.source_id}"
    locators = ", ".join(entry.locators_used)
    suffix = f" — {locators}" if locators else ""
    return f"[{entry.display_number}] {entry.title} — {uri}{suffix}"


def _replace_last(text: str, old: str, new: str) -> str:
    index = text.rfind(old)
    if index < 0:
        return text
    return text[:index] + new + text[index + len(old) :]
