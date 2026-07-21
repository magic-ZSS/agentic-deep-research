"""Hash-guarded, section-local report repair."""

from __future__ import annotations

from collections import defaultdict

from open_deep_research.reporting.extraction import strip_untrusted_citation_syntax
from open_deep_research.reporting.models import (
    AtomicClaim,
    DraftReport,
    RepairPatch,
    ReportSection,
    RequiredAction,
    ValidationResult,
    text_hash,
)


class ReportRepairer:
    """Apply deterministic dispositions without rewriting passing sections."""

    def create_patches(
        self,
        draft: DraftReport,
        claims: tuple[AtomicClaim, ...],
        results: tuple[ValidationResult, ...],
    ) -> tuple[RepairPatch, ...]:
        """Create at most one idempotent patch per affected section."""
        claim_by_id = {claim.claim_id: claim for claim in claims}
        results_by_section: dict[str, list[ValidationResult]] = defaultdict(list)
        for result in results:
            claim = claim_by_id[result.claim_id]
            if result.required_action is not RequiredAction.KEEP:
                results_by_section[claim.section_id].append(result)
        patches: list[RepairPatch] = []
        for section in draft.sections:
            failures = results_by_section.get(section.section_id, [])
            if not failures:
                continue
            replacement = strip_untrusted_citation_syntax(section.text)
            target_ids: list[str] = []
            for result in sorted(
                failures,
                key=lambda item: claim_by_id[item.claim_id].span_start,
                reverse=True,
            ):
                claim = claim_by_id[result.claim_id]
                target_ids.append(claim.claim_id)
                if result.required_action is RequiredAction.QUALIFY:
                    new_text = f"Evidence only partially supports the following: {claim.text}"
                elif result.required_action is RequiredAction.MARK_INSUFFICIENT:
                    new_text = "[Evidence insufficient for this claim.]"
                else:
                    new_text = ""
                replacement = _replace_once(replacement, claim.text, new_text)
            preserved = tuple(
                claim.claim_id
                for claim in claims
                if claim.section_id == section.section_id
                and claim.claim_id not in target_ids
            )
            patches.append(
                RepairPatch(
                    section_id=section.section_id,
                    original_hash=section.canonical_hash,
                    target_claim_ids=tuple(sorted(target_ids)),
                    replacement_text=_normalize_blank_lines(replacement),
                    preserved_claim_ids=preserved,
                    reason="citation_validation_disposition",
                )
            )
        return tuple(sorted(patches, key=lambda item: item.section_id))

    def apply(
        self, draft: DraftReport, patches: tuple[RepairPatch, ...]
    ) -> tuple[ReportSection, ...]:
        """Apply patches only when the original section hash still matches."""
        by_section = {patch.section_id: patch for patch in patches}
        output: list[ReportSection] = []
        for section in draft.sections:
            patch = by_section.get(section.section_id)
            if patch is None:
                output.append(section)
                continue
            if text_hash(section.text) != patch.original_hash:
                raise ValueError("repair patch original_hash mismatch")
            output.append(
                ReportSection(
                    section_id=section.section_id,
                    ordinal=section.ordinal,
                    heading=section.heading,
                    text=patch.replacement_text,
                )
            )
        return tuple(output)


def _replace_once(text: str, old: str, new: str) -> str:
    index = text.find(old)
    if index < 0:
        raise ValueError("claim text no longer exists inside target section")
    return text[:index] + new + text[index + len(old) :]


def _normalize_blank_lines(text: str) -> str:
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()
