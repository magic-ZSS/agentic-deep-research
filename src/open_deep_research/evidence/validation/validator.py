"""Deterministic hard checks plus injectable claim entailment validation."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.evidence.models import EvidenceDirectness
from open_deep_research.evidence.validation.resolver import ResolvedEvidence
from open_deep_research.knowledge.models import AuthorityClass
from open_deep_research.reporting.models import (
    AtomicClaim,
    AuthorityStatus,
    CitationKey,
    ClaimEvidenceLink,
    ClaimType,
    LinkOrigin,
    LinkRelation,
    RequiredAction,
    TemporalStatus,
    ValidationResult,
    ValidationStatus,
)


class EntailmentDecision(BaseModel):
    """Normalized result returned by an entailment evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    score: float = Field(ge=0, le=1)
    relation: LinkRelation
    rationale: str


class EntailmentEvaluator(Protocol):
    """Small injectable boundary used by fake or configured model adapters."""

    def evaluate(
        self, claim: str, evidence: str
    ) -> EntailmentDecision | Awaitable[EntailmentDecision]:
        """Evaluate whether one evidence excerpt entails one claim."""
        ...


class DeterministicEntailmentEvaluator:
    """Conservative token-overlap fallback; it never calls a model."""

    def evaluate(self, claim: str, evidence: str) -> EntailmentDecision:
        """Return a conservative token-overlap entailment decision."""
        claim_tokens = _tokens(claim)
        evidence_tokens = _tokens(evidence)
        if not claim_tokens:
            return EntailmentDecision(
                score=0, relation=LinkRelation.CONTEXT, rationale="empty_claim"
            )
        overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
        relation = (
            LinkRelation.SUPPORTS if overlap >= 0.65 else LinkRelation.CONTEXT
        )
        return EntailmentDecision(
            score=overlap,
            relation=relation,
            rationale=f"deterministic_token_overlap:{overlap:.3f}",
        )


class CitationValidator:
    """Validate one claim without inheriting links from adjacent claims."""

    def __init__(
        self,
        *,
        evaluator: EntailmentEvaluator | None = None,
        min_entailment: float = 0.75,
        min_authority: AuthorityClass = AuthorityClass.SECONDARY,
        require_temporal_validity: bool = True,
        policy_version: str = "citation-policy-v1",
        unsupported_action: str = "remove",
    ) -> None:
        """Configure independent hard checks and the entailment boundary."""
        self.evaluator = evaluator or DeterministicEntailmentEvaluator()
        self.min_entailment = min_entailment
        self.min_authority = min_authority
        self.require_temporal_validity = require_temporal_validity
        self.policy_version = policy_version
        self.unsupported_action = unsupported_action

    async def validate(
        self,
        claim: AtomicClaim,
        candidates: Sequence[tuple[ResolvedEvidence, LinkOrigin]],
        *,
        as_of: datetime,
    ) -> ValidationResult:
        """Return a five-way result and preserve every failed explicit link."""
        links = tuple(
            [await self._validate_link(claim, evidence, origin, as_of=as_of) for evidence, origin in candidates]
        )
        explicit_required = bool(
            claim.cited_evidence_ids or claim.cited_citation_keys
        )
        explicit_links = tuple(
            link
            for link in links
            if link.origin is LinkOrigin.EXPLICIT_DRAFT_CITATION
        )
        accepted_explicit = tuple(link for link in explicit_links if link.accepted)
        accepted = tuple(link for link in links if link.accepted)
        contradicted = tuple(
            link for link in links if link.relation is LinkRelation.CONTRADICTS
        )
        failed_checks = sorted(
            {
                reason
                for link in links
                for reason in _failed_reasons(link)
            }
        )

        if claim.claim_type is ClaimType.SUBJECTIVE:
            status = ValidationStatus.NOT_CHECKABLE
        elif contradicted:
            status = ValidationStatus.CONTRADICTED
        elif explicit_required and not accepted_explicit:
            status = ValidationStatus.UNSUPPORTED
            failed_checks.append("explicit_citation_not_supporting")
            if any(
                link.accepted
                and link.origin is LinkOrigin.SUPPLEMENTAL_RETRIEVAL
                for link in links
            ):
                failed_checks.append("supplemental_cannot_override_explicit_failure")
        elif accepted:
            status = (
                ValidationStatus.FULLY_SUPPORTED
                if all(link.entailment_score >= self.min_entailment for link in accepted)
                else ValidationStatus.PARTIALLY_SUPPORTED
            )
        elif links:
            status = ValidationStatus.UNSUPPORTED
        else:
            status = (
                ValidationStatus.UNSUPPORTED
                if claim.claim_type in {ClaimType.NUMERIC, ClaimType.FACTUAL}
                else ValidationStatus.NOT_CHECKABLE
            )
            failed_checks.append("no_resolvable_evidence")

        action = self._action(status, links, explicit_required)
        confidence = max((link.entailment_score for link in accepted), default=0.0)
        return ValidationResult(
            claim_id=claim.claim_id,
            status=status,
            links=links,
            failed_checks=tuple(sorted(set(failed_checks))),
            required_action=action,
            confidence=confidence,
            policy_version=self.policy_version,
        )

    async def _validate_link(
        self,
        claim: AtomicClaim,
        resolved: ResolvedEvidence,
        origin: LinkOrigin,
        *,
        as_of: datetime,
    ) -> ClaimEvidenceLink:
        relation = LinkRelation(resolved.evidence.relation.value)
        decision = self.evaluator.evaluate(claim.text, resolved.evidence.excerpt)
        if inspect.isawaitable(decision):
            decision = await decision
        if relation is LinkRelation.CONTRADICTS:
            decision = EntailmentDecision(
                score=decision.score,
                relation=LinkRelation.CONTRADICTS,
                rationale=decision.rationale,
            )
        temporal_status = _temporal_status(resolved, claim, as_of)
        authority_status = _authority_status(
            resolved.source.authority_class,
            self.min_authority,
            claim.claim_type,
        )
        numeric_ok = _numbers(claim.text) <= _numbers(resolved.evidence.excerpt)
        direct = resolved.evidence.directness is EvidenceDirectness.DIRECT
        accepted = all(
            (
                resolved.eligible,
                decision.relation is LinkRelation.SUPPORTS,
                decision.score >= self.min_entailment * 0.5,
                direct,
                numeric_ok,
                authority_status is AuthorityStatus.SUFFICIENT,
                not self.require_temporal_validity
                or temporal_status is TemporalStatus.CURRENT,
            )
        )
        rationale = ";".join(
            (
                decision.rationale,
                resolved.eligibility_reason,
                "numeric_match" if numeric_ok else "numeric_mismatch",
            )
        )
        return ClaimEvidenceLink(
            claim_id=claim.claim_id,
            evidence_id=resolved.evidence.evidence_id,
            chunk_id=resolved.chunk.chunk_id,
            citation_key=CitationKey(
                source_id=resolved.source.source_id,
                version_id=resolved.version.version_id,
            ),
            relation=decision.relation,
            origin=origin,
            entailment_score=decision.score,
            directness=resolved.evidence.directness.value,
            temporal_status=temporal_status,
            authority_status=authority_status,
            locator=resolved.locator(),
            rationale=rationale,
            validator_version=self.policy_version,
            accepted=accepted,
        )

    def _action(
        self,
        status: ValidationStatus,
        links: Sequence[ClaimEvidenceLink],
        explicit_required: bool,
    ) -> RequiredAction:
        if status is ValidationStatus.FULLY_SUPPORTED:
            return RequiredAction.KEEP
        if status is ValidationStatus.PARTIALLY_SUPPORTED:
            return RequiredAction.QUALIFY
        if (
            explicit_required
            and any(
                link.accepted
                and link.origin is LinkOrigin.SUPPLEMENTAL_RETRIEVAL
                for link in links
            )
        ):
            return RequiredAction.REPAIR_REBIND
        if self.unsupported_action == "mark":
            return RequiredAction.MARK_INSUFFICIENT
        return RequiredAction.REMOVE


_AUTHORITY_RANK = {
    AuthorityClass.UNKNOWN: 0,
    AuthorityClass.SELF_REPORTED: 1,
    AuthorityClass.SECONDARY: 2,
    AuthorityClass.PRIMARY: 3,
    AuthorityClass.OFFICIAL: 4,
}


def _authority_status(
    actual: AuthorityClass,
    minimum: AuthorityClass,
    claim_type: ClaimType,
) -> AuthorityStatus:
    if actual is AuthorityClass.SELF_REPORTED:
        return (
            AuthorityStatus.SUFFICIENT
            if claim_type is ClaimType.CORPORATE_ATTRIBUTION
            else AuthorityStatus.SELF_REPORTED_ONLY
        )
    if actual is AuthorityClass.UNKNOWN:
        return AuthorityStatus.UNKNOWN
    return (
        AuthorityStatus.SUFFICIENT
        if _AUTHORITY_RANK[actual] >= _AUTHORITY_RANK[minimum]
        else AuthorityStatus.INSUFFICIENT
    )


def _temporal_status(
    evidence: ResolvedEvidence, claim: AtomicClaim, as_of: datetime
) -> TemporalStatus:
    instant = claim.temporal_scope or as_of
    if instant.tzinfo is None or instant.utcoffset() is None:
        instant = instant.replace(tzinfo=UTC)
    version = evidence.version
    if version.valid_from and instant < version.valid_from:
        return TemporalStatus.FUTURE
    if version.valid_to and instant > version.valid_to:
        return TemporalStatus.STALE
    if not version.valid_from and not version.valid_to and claim.temporal_scope:
        return TemporalStatus.UNKNOWN
    return TemporalStatus.CURRENT


def _failed_reasons(link: ClaimEvidenceLink) -> tuple[str, ...]:
    reasons: list[str] = []
    if not link.accepted:
        if link.relation is LinkRelation.CONTRADICTS:
            reasons.append("contradicted")
        if link.directness != EvidenceDirectness.DIRECT.value:
            reasons.append("not_direct")
        if link.temporal_status is not TemporalStatus.CURRENT:
            reasons.append(f"temporal:{link.temporal_status.value}")
        if link.authority_status is not AuthorityStatus.SUFFICIENT:
            reasons.append(f"authority:{link.authority_status.value}")
        if "numeric_mismatch" in link.rationale:
            reasons.append("numeric_mismatch")
        if link.entailment_score == 0:
            reasons.append("not_entailed")
    return tuple(reasons)


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", value)
        if len(token) > 1
    }


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"(?<!\w)[+-]?\d+(?:\.\d+)?%?", value))
