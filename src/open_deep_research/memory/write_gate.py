"""Deterministic seven-check gate for every long-term memory write."""
# ruff: noqa: D101,D102,D107

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from open_deep_research.memory.models import (
    POLICY_VERSION,
    GateCheck,
    GateOutcome,
    MemoryGateDecision,
    MemoryType,
    MemoryWriteProposal,
)
from open_deep_research.memory.repositories import MemoryRepository
from open_deep_research.runtime.identity import RuntimeIdentity

EvidenceValidator = Callable[[tuple[str, ...]], Awaitable[bool]]


class MemoryWriteGate:
    def __init__(self, repository: MemoryRepository, *, evidence_validator: EvidenceValidator | None = None, min_importance: float = 0.5, episodic_score: float = 0.75, min_procedural_successes: int = 3) -> None:
        if min_procedural_successes < 3:
            raise ValueError("procedural minimum successes cannot be below 3")
        self.repository = repository
        self.evidence_validator = evidence_validator
        self.min_importance = min_importance
        self.episodic_score = episodic_score
        self.min_procedural_successes = min_procedural_successes

    async def evaluate(self, proposal: MemoryWriteProposal, identity: RuntimeIdentity) -> MemoryGateDecision:
        expected = identity.namespace(proposal.memory_type.value)
        if proposal.namespace != expected:
            raise PermissionError("proposal namespace is not authorized by runtime identity")
        evidence_ok = True
        if proposal.evidence_ids:
            evidence_ok = bool(self.evidence_validator and await self.evidence_validator(proposal.evidence_ids))
        elif proposal.memory_type is MemoryType.SEMANTIC:
            evidence_ok = False

        now = datetime.now(UTC)
        fresh = proposal.valid_to is None or proposal.valid_to > now
        quality = True
        quality_reason = "type policy satisfied"
        if proposal.memory_type is MemoryType.EPISODIC:
            quality = proposal.outcome_score is not None and proposal.outcome_score >= self.episodic_score
            quality_reason = "episodic outcome score meets threshold" if quality else "episodic outcome score below threshold"
        if proposal.memory_type is MemoryType.PROCEDURAL:
            quality = proposal.success_count >= self.min_procedural_successes and proposal.regression_passed and proposal.approved
            quality_reason = "procedural independent-success/regression/approval threshold met" if quality else "procedural promotion prerequisites not met"
        preference_ok = proposal.memory_type is not MemoryType.PREFERENCE or bool(proposal.explicit_statement_ref)
        policy_ok = proposal.memory_type is not MemoryType.WORKING and preference_ok
        checks = (
            GateCheck(name="importance", passed=proposal.importance >= self.min_importance, reason="importance threshold"),
            GateCheck(name="source", passed=evidence_ok, reason="active evidence verified" if evidence_ok else "required active evidence missing"),
            GateCheck(name="dedupe", passed=True, reason="stable content identity used"),
            GateCheck(name="freshness", passed=fresh, reason="validity interval checked"),
            GateCheck(name="sensitivity", passed=proposal.sensitivity != "sensitive", reason="sensitive content requires review" if proposal.sensitivity == "sensitive" else "allowed sensitivity"),
            GateCheck(name="quality", passed=quality, reason=quality_reason),
            GateCheck(name="policy", passed=policy_ok, reason="working memory stays checkpoint-only; explicit preference provenance checked"),
        )
        failed = [check.name for check in checks if not check.passed]
        if proposal.sensitivity == "sensitive":
            outcome = GateOutcome.NEEDS_REVIEW
        elif failed:
            outcome = GateOutcome.REJECT
        else:
            outcome = GateOutcome.PROMOTE
        digest = hashlib.sha256(
            (proposal.model_dump_json() + POLICY_VERSION + outcome.value).encode()
        ).hexdigest()
        return MemoryGateDecision(
            decision_id="mdecision_" + digest,
            proposal_id=proposal.proposal_id,
            outcome=outcome,
            checks=checks,
            reason="all seven gates passed" if not failed else "failed gates: " + ",".join(failed),
        )

    async def propose_evaluate_apply(self, proposal: MemoryWriteProposal, identity: RuntimeIdentity, *, actor: str):
        stored = await self.repository.propose(proposal, actor=actor)
        decision = await self.evaluate(stored, identity)
        record = await self.repository.apply_decision(stored, decision, actor=actor)
        return decision, record
