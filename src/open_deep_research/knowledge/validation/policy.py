"""Hard-rule candidate policy used before any lifecycle promotion."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
)
from open_deep_research.knowledge.models import AuthorityClass
from open_deep_research.knowledge.retrieval.models import RetrievalRecord
from open_deep_research.knowledge.validation.models import (
    CandidateValidationDecision,
    CandidateValidationStatus,
    ValidationRuleResult,
)


_WORD = re.compile(r"\w+", flags=re.UNICODE)
_AUTHORITY_RANK = {
    AuthorityClass.UNKNOWN: 0,
    AuthorityClass.SELF_REPORTED: 1,
    AuthorityClass.SECONDARY: 2,
    AuthorityClass.PRIMARY: 3,
    AuthorityClass.OFFICIAL: 4,
}


def _overlap(requirement_text: str, candidate_text: str) -> float:
    requested = Counter(_WORD.findall(requirement_text.casefold()))
    available = Counter(_WORD.findall(candidate_text.casefold()))
    if not requested:
        return 0.0
    matched = sum(min(count, available.get(token, 0)) for token, count in requested.items())
    return matched / sum(requested.values())


class CandidateValidationPolicy:
    """Conservative, deterministic validation shared by every candidate origin."""

    def __init__(
        self,
        *,
        policy_version: str,
        min_content_chars: int = 40,
        min_confidence: float = 0.7,
        min_source_authority: AuthorityClass = AuthorityClass.SECONDARY,
        max_evidence_age_days: int | None = None,
        min_requirement_overlap: float = 0.2,
        retrieval_clock_skew_seconds: int = 300,
    ) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version cannot be blank")
        if min_content_chars < 1:
            raise ValueError("min_content_chars must be positive")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between zero and one")
        if max_evidence_age_days is not None and max_evidence_age_days < 0:
            raise ValueError("max_evidence_age_days cannot be negative")
        if not 0 <= min_requirement_overlap <= 1:
            raise ValueError("min_requirement_overlap must be between zero and one")
        if retrieval_clock_skew_seconds < 0:
            raise ValueError("retrieval_clock_skew_seconds cannot be negative")
        self.policy_version = policy_version
        self.min_content_chars = min_content_chars
        self.min_confidence = min_confidence
        self.min_source_authority = min_source_authority
        self.max_evidence_age_days = max_evidence_age_days
        self.min_requirement_overlap = min_requirement_overlap
        self.retrieval_clock_skew = timedelta(seconds=retrieval_clock_skew_seconds)

    def evaluate(
        self,
        record: RetrievalRecord,
        *,
        requirement_id: str,
        requirement_text: str,
        as_of: datetime | None = None,
    ) -> CandidateValidationDecision:
        """Evaluate a complete immutable chain without mutating a Repository."""
        if record.evidence is None:
            raise ValueError("candidate validation requires an Evidence object")
        instant = as_of or datetime.now(UTC)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("candidate validation as_of must be timezone-aware")
        instant = instant.astimezone(UTC)
        evidence = record.evidence
        version = record.version
        overlap = _overlap(requirement_text, f"{evidence.excerpt}\n{record.chunk.text}")
        proposed_relation = (
            EvidenceRelation.SUPPORTS
            if evidence.relation is EvidenceRelation.CONTEXT
            and overlap >= self.min_requirement_overlap
            else evidence.relation
        )
        proposed_directness = (
            EvidenceDirectness.DIRECT
            if evidence.directness is EvidenceDirectness.UNKNOWN
            and overlap >= self.min_requirement_overlap
            else evidence.directness
        )
        proposed_confidence = max(evidence.confidence, min(1.0, overlap))

        deleted = any(
            item.soft_deleted_at is not None
            for item in (
                record.source,
                record.document,
                version,
                record.chunk,
                evidence,
            )
        )
        resolvable = bool(
            record.source.canonical_uri
            or record.source.internal_storage_ref
            or record.source.public_display_uri
        )
        current = not (
            version.retrieved_at > instant + self.retrieval_clock_skew
            or (version.published_at is not None and version.published_at > instant)
            or (version.valid_from is not None and instant < version.valid_from)
            or (version.valid_to is not None and instant > version.valid_to)
        )
        fresh = True
        if self.max_evidence_age_days is not None:
            timestamp = version.published_at or version.retrieved_at
            fresh = (
                timestamp >= instant - timedelta(days=self.max_evidence_age_days)
                and timestamp <= instant + self.retrieval_clock_skew
            )
        metadata = {**version.metadata, **record.chunk.metadata}
        no_conflict = not bool(metadata.get("conflict") or metadata.get("contradicted"))
        safe = not bool(metadata.get("sensitive") or metadata.get("untrusted"))
        authority_ok = _AUTHORITY_RANK[record.source.authority_class] >= _AUTHORITY_RANK[
            self.min_source_authority
        ]
        results = (
            ValidationRuleResult(
                rule="source_resolvable",
                passed=resolvable,
                detail="source has a stable retrieval reference" if resolvable else "source has no resolvable reference",
            ),
            ValidationRuleResult(
                rule="chain_not_deleted",
                passed=not deleted,
                detail="chain is visible" if not deleted else "chain contains a soft-deleted entity",
            ),
            ValidationRuleResult(
                rule="minimum_content",
                passed=len(record.chunk.text.strip()) >= self.min_content_chars,
                detail=f"content_chars={len(record.chunk.text.strip())}; minimum={self.min_content_chars}",
            ),
            ValidationRuleResult(
                rule="requirement_directness",
                passed=(
                    proposed_relation is EvidenceRelation.SUPPORTS
                    and proposed_directness is EvidenceDirectness.DIRECT
                    and overlap >= self.min_requirement_overlap
                ),
                detail=f"overlap={overlap:.3f}; relation={proposed_relation.value}; directness={proposed_directness.value}",
            ),
            ValidationRuleResult(
                rule="confidence",
                passed=proposed_confidence >= self.min_confidence,
                detail=f"confidence={proposed_confidence:.3f}; minimum={self.min_confidence:.3f}",
            ),
            ValidationRuleResult(
                rule="source_authority",
                passed=authority_ok,
                detail=f"authority={record.source.authority_class.value}; minimum={self.min_source_authority.value}",
            ),
            ValidationRuleResult(
                rule="temporal_current",
                passed=current and fresh,
                detail=f"current={current}; fresh={fresh}; as_of={instant.isoformat()}",
            ),
            ValidationRuleResult(
                rule="conflict_free",
                passed=no_conflict,
                detail="no conflict marker" if no_conflict else "candidate has an unresolved conflict",
            ),
            ValidationRuleResult(
                rule="sensitivity",
                passed=safe,
                detail="no quarantine marker" if safe else "candidate is sensitive or untrusted",
            ),
        )
        accepted = all(item.passed for item in results)
        return CandidateValidationDecision(
            policy_version=self.policy_version,
            requirement_id=requirement_id,
            evidence_id=evidence.evidence_id,
            version_id=version.version_id,
            status=(
                CandidateValidationStatus.ACCEPTED
                if accepted
                else CandidateValidationStatus.QUARANTINED
            ),
            rule_results=results,
            proposed_relation=proposed_relation,
            proposed_directness=proposed_directness,
            proposed_confidence=proposed_confidence,
            evaluated_at=instant,
        )
