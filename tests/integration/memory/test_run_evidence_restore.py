from datetime import UTC, datetime

import pytest
from langgraph.graph import START, StateGraph
from langgraph.types import interrupt

from open_deep_research.evidence.run_store import (
    RunEvidenceContext,
    RunEvidenceNotFoundError,
    SQLiteRunEvidenceStore,
)
from open_deep_research.knowledge.ids import scope_id_for
from open_deep_research.runtime.context import RunEvidenceReference, reopen_run_evidence
from open_deep_research.runtime.identity import RuntimeIdentity
from open_deep_research.runtime.persistence import persistence_lifespan
from tests.unit.evidence.test_run_store import make_bundle


@pytest.mark.asyncio
async def test_checkpoint_reference_reopens_same_run_and_rejects_other_user(tmp_path):
    alice = RuntimeIdentity(tenant_id="tenant", user_id="alice", project_id="project", thread_id="thread", auth_source="test")
    scope = scope_id_for("tenant", "project", "alice", "private")
    context = RunEvidenceContext(scope_id=scope, run_id="run-1")
    path = tmp_path / "run-evidence.db"
    store = SQLiteRunEvidenceStore(path)
    bundle = make_bundle(context, now=datetime.now(UTC))
    await store.put(context, bundle)
    ref = RunEvidenceReference(run_id="run-1", evidence_ids=(bundle.evidence_id,))
    _, restored = await reopen_run_evidence(ref, identity=alice, database_path=str(path))
    assert restored[0].evidence_id == bundle.evidence_id
    bob = alice.model_copy(update={"user_id": "bob"})
    with pytest.raises(RunEvidenceNotFoundError):
        await reopen_run_evidence(ref, identity=bob, database_path=str(path))


@pytest.mark.asyncio
async def test_run_evidence_reference_survives_sqlite_checkpoint_reopen(tmp_path):
    identity = RuntimeIdentity(tenant_id="tenant", user_id="alice", project_id="project", thread_id="thread", auth_source="test")
    context = RunEvidenceContext(scope_id=scope_id_for("tenant", "project", "alice", "private"), run_id="run-resume")
    evidence_db = tmp_path / "run.db"
    store = SQLiteRunEvidenceStore(evidence_db)
    bundle = make_bundle(context, now=datetime.now(UTC))
    await store.put(context, bundle)

    def pause(state):
        interrupt("pause")
        return {}

    builder = StateGraph(dict).add_node("pause", pause).add_edge(START, "pause")
    config = identity.checkpoint_config()
    checkpoint_db, checkpoint_store = tmp_path / "checkpoint.db", tmp_path / "checkpoint-store.db"
    payload = RunEvidenceReference(run_id=context.run_id, evidence_ids=(bundle.evidence_id,)).model_dump(mode="json")
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(checkpoint_db), store_db_path=str(checkpoint_store)) as resources:
        graph = builder.compile(checkpointer=resources.checkpointer)
        assert "__interrupt__" in await graph.ainvoke({"run_evidence_reference": payload}, config)
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(checkpoint_db), store_db_path=str(checkpoint_store)) as resources:
        graph = builder.compile(checkpointer=resources.checkpointer)
        snapshot = await graph.aget_state(config)
        restored_ref = RunEvidenceReference.model_validate(snapshot.values["run_evidence_reference"])
        _, bundles = await reopen_run_evidence(restored_ref, identity=identity, database_path=str(evidence_db))
        assert bundles[0].evidence_id == bundle.evidence_id
