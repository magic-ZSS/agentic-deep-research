"""Thin policy-facing facade over atomic lifecycle repository operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalAction,
    LifecycleTargetType,
)
from open_deep_research.knowledge.models import (
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.repositories import KnowledgeEvidenceRepository


class KnowledgeLifecycleService:
    """Expose proposals separately from policy-owned state transitions."""

    def __init__(self, repository: KnowledgeEvidenceRepository) -> None:
        self.repository = repository

    async def propose(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        target_entity_type: LifecycleTargetType,
        target_id: str,
        action: LifecycleProposalAction,
        reason: str,
        proposed_by: str,
        run_id: str | None,
        correlation_id: str,
    ) -> LifecycleProposal:
        proposal = LifecycleProposal(
            scope_id=scope.scope_id,
            target_entity_type=target_entity_type,
            target_id=target_id,
            action=action,
            reason=reason,
            proposed_by=proposed_by,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        return await self.repository.create_lifecycle_proposal(access, scope, proposal)

    async def transition_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        expected_status: VersionLifecycleStatus,
        status: VersionLifecycleStatus,
        actor_type: str,
        reason: str,
        policy_version: str,
        rule_results: Sequence[str],
        run_id: str | None,
        proposal_id: str | None,
        correlation_id: str,
    ) -> DocumentVersion:
        return await self.repository.transition_version_lifecycle(
            access,
            scope,
            version_id,
            expected_status=expected_status,
            status=status,
            actor_type=actor_type,
            reason=reason,
            policy_version=policy_version,
            rule_results=tuple(rule_results),
            run_id=run_id,
            proposal_id=proposal_id,
            correlation_id=correlation_id,
        )

    async def validate_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        expected_status: EvidenceValidationStatus,
        status: EvidenceValidationStatus,
        relation: EvidenceRelation,
        directness: EvidenceDirectness,
        confidence: float,
        valid_at: datetime | None,
        actor_type: str,
        reason: str,
        policy_version: str,
        rule_results: Sequence[str],
        run_id: str | None,
        proposal_id: str | None,
        correlation_id: str,
    ) -> Evidence:
        return await self.repository.transition_evidence_validation(
            access,
            scope,
            evidence_id,
            expected_status=expected_status,
            status=status,
            relation=relation,
            directness=directness,
            confidence=confidence,
            valid_at=valid_at,
            actor_type=actor_type,
            reason=reason,
            policy_version=policy_version,
            rule_results=tuple(rule_results),
            run_id=run_id,
            proposal_id=proposal_id,
            correlation_id=correlation_id,
        )
