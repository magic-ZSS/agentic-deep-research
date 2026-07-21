"""Deterministic in-memory implementation of the MemoryRepository contract."""
# ruff: noqa: D101,D102,D107

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from open_deep_research.memory.models import (
    GateOutcome,
    MemoryAuditEvent,
    MemoryRecord,
    MemoryStatus,
    MemoryWriteProposal,
    stable_memory_id,
    utc_now,
)
from open_deep_research.memory.repositories import MemoryRepositoryError


def _audit_id(entity_id: str, action: str, count: int) -> str:
    return "maudit_" + hashlib.sha256(f"{entity_id}|{action}|{count}".encode()).hexdigest()


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self.proposals: dict[tuple[tuple[str, ...], str], MemoryWriteProposal] = {}
        self.records: dict[tuple[tuple[str, ...], str], MemoryRecord] = {}
        self.audits: list[MemoryAuditEvent] = []

    def _audit(self, namespace, entity_id, action, actor, details=None):
        self.audits.append(MemoryAuditEvent(
            audit_id=_audit_id(entity_id, action, len(self.audits)), namespace=namespace,
            entity_id=entity_id, action=action, actor=actor, details=details or {}
        ))

    async def propose(self, proposal: MemoryWriteProposal, *, actor: str) -> MemoryWriteProposal:
        key = (proposal.namespace, proposal.proposal_id)
        existing = self.proposals.get(key)
        if existing == proposal:
            return existing
        if existing is not None:
            immutable = ("namespace", "memory_type", "content")
            if any(getattr(existing, name) != getattr(proposal, name) for name in immutable):
                raise MemoryRepositoryError("stable proposal identity conflicts with payload")
            proposal = existing.model_copy(update={
                "origin_run_ids": tuple(sorted(set(existing.origin_run_ids + proposal.origin_run_ids))),
                "evidence_ids": tuple(sorted(set(existing.evidence_ids + proposal.evidence_ids))),
                "source_ids": tuple(sorted(set(existing.source_ids + proposal.source_ids))),
                "importance": max(existing.importance, proposal.importance),
                "confidence": max(existing.confidence, proposal.confidence),
                "success_count": max(existing.success_count, proposal.success_count),
                "regression_passed": existing.regression_passed or proposal.regression_passed,
                "approved": existing.approved or proposal.approved,
            })
            self.proposals[key] = proposal
            self._audit(proposal.namespace, proposal.proposal_id, "proposal_merged", actor)
            return proposal
        self.proposals.setdefault(key, proposal)
        self._audit(proposal.namespace, proposal.proposal_id, "proposal_created", actor)
        return self.proposals[key]

    async def get_proposal(self, namespace, proposal_id):
        try:
            return self.proposals[(namespace, proposal_id)]
        except KeyError as exc:
            raise MemoryRepositoryError("proposal not found") from exc

    async def apply_decision(self, proposal, decision, *, actor):
        if decision.proposal_id != proposal.proposal_id:
            raise MemoryRepositoryError("decision/proposal mismatch")
        if any(
            event.entity_id == proposal.proposal_id
            and event.action == "gate_decision"
            and event.details.get("decision_id") == decision.decision_id
            for event in self.audits
        ):
            memory_id = stable_memory_id(
                proposal.namespace, proposal.memory_type.value, proposal.content
            )
            return self.records.get((proposal.namespace, memory_id))
        self._audit(proposal.namespace, proposal.proposal_id, "gate_decision", actor, decision.model_dump(mode="json"))
        if decision.outcome is not GateOutcome.PROMOTE:
            return None
        memory_id = stable_memory_id(proposal.namespace, proposal.memory_type.value, proposal.content)
        key = (proposal.namespace, memory_id)
        existing = self.records.get(key)
        origins = tuple(sorted(set((existing.origin_run_ids if existing else ()) + proposal.origin_run_ids)))
        now = utc_now()
        record = MemoryRecord(
            memory_id=memory_id, namespace=proposal.namespace, memory_type=proposal.memory_type,
            status=MemoryStatus.ACTIVE, content=proposal.content,
            content_hash=hashlib.sha256(proposal.content.encode()).hexdigest(),
            confidence=max(proposal.confidence, existing.confidence if existing else 0),
            sensitivity=proposal.sensitivity, evidence_ids=proposal.evidence_ids,
            source_ids=proposal.source_ids, origin_run_ids=origins,
            usage_count=(existing.usage_count if existing else 0), valid_from=proposal.valid_from,
            valid_to=proposal.valid_to, created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.records[key] = record
        self._audit(proposal.namespace, memory_id, "promoted", actor)
        return record

    async def get(self, namespace, memory_id, *, include_deleted=False):
        record = self.records.get((namespace, memory_id))
        if record is None or (record.soft_deleted_at and not include_deleted):
            raise MemoryRepositoryError("memory not found")
        return record

    async def search(self, namespace, *, query, memory_types=(), statuses=(MemoryStatus.ACTIVE,), as_of=None, limit=10):
        now = as_of or datetime.now(UTC)
        terms = query.casefold().split()
        result = []
        for (ns, _), record in self.records.items():
            if ns != namespace or record.status not in statuses or record.soft_deleted_at:
                continue
            if memory_types and record.memory_type not in memory_types:
                continue
            if record.valid_from and record.valid_from > now:
                continue
            if record.valid_to and record.valid_to <= now:
                continue
            if terms and not all(term in record.content.casefold() for term in terms):
                continue
            result.append(record)
        return sorted(result, key=lambda item: (-item.confidence, item.memory_id))[:limit]

    async def mark_status(self, namespace, memory_id, status, *, actor, reason):
        record = await self.get(namespace, memory_id, include_deleted=True)
        updated = record.model_copy(update={"status": status, "updated_at": utc_now()})
        self.records[(namespace, memory_id)] = updated
        self._audit(namespace, memory_id, f"status:{status.value}", actor, {"reason": reason})
        return updated

    async def soft_delete(self, namespace, memory_id, *, actor, reason):
        record = await self.get(namespace, memory_id, include_deleted=True)
        updated = record.model_copy(update={"soft_deleted_at": utc_now(), "updated_at": utc_now()})
        self.records[(namespace, memory_id)] = updated
        self._audit(namespace, memory_id, "soft_deleted", actor, {"reason": reason})
        return updated

    async def list_audit(self, namespace, entity_id):
        return [item for item in self.audits if item.namespace == namespace and item.entity_id == entity_id]
