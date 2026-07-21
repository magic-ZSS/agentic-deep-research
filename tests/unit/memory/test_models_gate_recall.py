from datetime import UTC, datetime

import pytest

from open_deep_research.memory.in_memory_repository import InMemoryMemoryRepository
from open_deep_research.memory.models import (
    EpisodicMemory,
    GateOutcome,
    MemoryStatus,
    MemoryType,
    MemoryWriteProposal,
    ProceduralMemory,
    SemanticMemory,
    UserPreferenceMemory,
    WorkingMemory,
)
from open_deep_research.memory.recall import MemoryRecall
from open_deep_research.memory.write_gate import MemoryWriteGate
from open_deep_research.runtime.identity import RuntimeIdentity


def identity(user="alice", thread="t1"):
    return RuntimeIdentity(tenant_id="tenant", user_id=user, project_id="project", thread_id=thread, auth_source="test")


def proposal(kind, **updates):
    who = updates.pop("identity", identity())
    values = dict(namespace=who.namespace(kind.value), memory_type=kind, content=f"Useful {kind.value} memory", importance=.9, confidence=.9, origin_run_ids=("run-1",))
    values.update(updates)
    return MemoryWriteProposal(**values)


@pytest.mark.asyncio
async def test_all_long_term_writes_have_seven_gate_checks_and_audit():
    repo = InMemoryMemoryRepository()
    gate = MemoryWriteGate(repo, evidence_validator=lambda ids: _truth(True))
    item = proposal(MemoryType.SEMANTIC, evidence_ids=("ev-1",), source_ids=("src-1",))
    decision, record = await gate.propose_evaluate_apply(item, identity(), actor="test")
    assert decision.outcome is GateOutcome.PROMOTE
    assert [check.name for check in decision.checks] == ["importance", "source", "dedupe", "freshness", "sensitivity", "quality", "policy"]
    assert record.status is MemoryStatus.ACTIVE
    assert {event.action for event in await repo.list_audit(item.namespace, item.proposal_id)} == {"proposal_created", "gate_decision"}


async def _truth(value):
    return value


@pytest.mark.asyncio
async def test_type_specific_hard_rules():
    repo = InMemoryMemoryRepository()
    gate = MemoryWriteGate(repo, evidence_validator=lambda ids: _truth(True))
    semantic = await gate.evaluate(proposal(MemoryType.SEMANTIC), identity())
    episodic = await gate.evaluate(proposal(MemoryType.EPISODIC, outcome_score=.2), identity())
    procedural = await gate.evaluate(proposal(MemoryType.PROCEDURAL, success_count=2, regression_passed=True, approved=True), identity())
    preference = await gate.evaluate(proposal(MemoryType.PREFERENCE), identity())
    working = await gate.evaluate(proposal(MemoryType.WORKING), identity())
    assert all(item.outcome is GateOutcome.REJECT for item in (semantic, episodic, procedural, preference, working))
    accepted = await gate.evaluate(proposal(MemoryType.PROCEDURAL, success_count=3, regression_passed=True, approved=True), identity())
    explicit = await gate.evaluate(proposal(MemoryType.PREFERENCE, explicit_statement_ref="message-7"), identity())
    assert accepted.outcome is explicit.outcome is GateOutcome.PROMOTE


@pytest.mark.asyncio
async def test_namespace_dedupe_stale_soft_delete_and_token_budget():
    repo = InMemoryMemoryRepository()
    gate = MemoryWriteGate(repo)
    item = proposal(MemoryType.PREFERENCE, explicit_statement_ref="m1", origin_run_ids=("r1",))
    decision, first = await gate.propose_evaluate_apply(item, identity(), actor="test")
    item2 = item.model_copy(update={"origin_run_ids": ("r1", "r2")})
    _, second = await gate.propose_evaluate_apply(item2, identity(), actor="test")
    assert first.memory_id == second.memory_id and second.origin_run_ids == ("r1", "r2")
    audit_count = len(await repo.list_audit(item.namespace, item.proposal_id))
    await gate.propose_evaluate_apply(item2, identity(), actor="test")
    assert len(await repo.list_audit(item.namespace, item.proposal_id)) == audit_count
    assert await repo.search(identity("bob").namespace("preference"), query="Useful") == []
    recall = MemoryRecall(repo)
    result = await recall.search("Useful", identity(), memory_types=(MemoryType.PREFERENCE,), token_budget=4)
    assert result.records == () and result.truncated
    await repo.mark_status(item.namespace, first.memory_id, MemoryStatus.STALE, actor="test", reason="expired")
    assert (await recall.search("Useful", identity(), memory_types=(MemoryType.PREFERENCE,))).records == ()
    deleted = await repo.soft_delete(item.namespace, first.memory_id, actor="test", reason="user request")
    assert deleted.soft_deleted_at is not None
    assert await repo.list_audit(item.namespace, first.memory_id)


@pytest.mark.asyncio
async def test_semantic_recall_revalidates_evidence():
    active = True
    async def validator(ids): return active
    repo = InMemoryMemoryRepository()
    gate = MemoryWriteGate(repo, evidence_validator=validator)
    item = proposal(MemoryType.SEMANTIC, evidence_ids=("ev",))
    _, record = await gate.propose_evaluate_apply(item, identity(), actor="test")
    recall = MemoryRecall(repo, evidence_validator=validator)
    assert (await recall.search("Useful", identity(), memory_types=(MemoryType.SEMANTIC,))).records
    active = False
    assert not (await recall.search("Useful", identity(), memory_types=(MemoryType.SEMANTIC,))).records


def test_identity_rejects_model_shaped_namespace_input():
    with pytest.raises(ValueError):
        RuntimeIdentity(tenant_id="tenant", user_id="../bob", project_id="project", thread_id="t", auth_source="test")


def test_five_memory_payload_schemas_enforce_hard_provenance():
    now = datetime.now(UTC)
    assert WorkingMemory(thread_id="thread").thread_id == "thread"
    assert EpisodicMemory(task_type="technical", brief_fingerprint="hash", plan_summary="plan", outcome_score=.9).outcome_score == .9
    assert SemanticMemory(fact="fact", evidence_ids=("ev",), source_ids=("src",), observed_at=now, valid_at=now, confidence=.9).evidence_ids
    assert ProceduralMemory(strategy="strategy", applicable_when="condition", supporting_run_ids=("r1", "r2", "r3"), success_count=3, regression_passed=True, approved=True).success_count == 3
    assert UserPreferenceMemory(preference="concise", scope="project", explicit_statement_ref="message", confirmed_at=now).explicit_statement_ref == "message"
