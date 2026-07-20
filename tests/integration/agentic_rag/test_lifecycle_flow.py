import asyncio
import functools
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalAction,
    LifecycleProposalStatus,
    LifecycleTargetType,
)
from open_deep_research.knowledge.models import (
    ChunkInput,
    ContentBlob,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.repositories import (
    InvalidTransitionError,
    RepositoryAccessError,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.migrations import MIGRATION_V1, MIGRATION_V2
from open_deep_research.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _identity(project: str = "project"):
    scope = KnowledgeScope(tenant_id="tenant", project_id=project)
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id=project,
        auth_source="test",
        request_id=f"request-{project}",
    )
    return scope, access


def _repository(kind: str, tmp_path: Path):
    if kind == "memory":
        return InMemoryRepository()
    return SQLiteRepository(str(tmp_path / "lifecycle.sqlite"))


async def _version(repo, access, scope, suffix: str, status, *, document=None, supersedes=None):
    if document is None:
        source = await repo.upsert_source(
            access,
            scope,
            kind=SourceKind.WEB,
            display_name=suffix,
            canonical_uri=f"https://example.test/{suffix}",
        )
        document = await repo.upsert_document(
            access,
            scope,
            source_id=source.source_id,
            logical_key="main",
            title=suffix,
            media_type="text/plain",
        )
    content = suffix.encode()
    blob = ContentBlob.from_bytes(
        scope_id=scope.scope_id,
        content=content,
        media_type="text/plain",
        storage_ref=f"{suffix}.txt",
    )
    version = await repo.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
        supersedes_version_id=supersedes,
        lifecycle_status=status,
    )
    return document, version


def _transition_kwargs(before, after, correlation="transition"):
    return dict(
        expected_status=before,
        status=after,
        actor_type="policy",
        reason="rules passed",
        policy_version="phase3-v1",
        rule_results=("authority:pass", "freshness:pass"),
        run_id="run-1",
        proposal_id=None,
        correlation_id=correlation,
    )


def test_v2_database_migrates_forward_without_rewriting_old_migrations(tmp_path):
    path = tmp_path / "v2.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("BEGIN IMMEDIATE")
    for migration in (MIGRATION_V1, MIGRATION_V2):
        for statement in migration.split(";"):
            if statement.strip():
                connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_metadata(singleton, schema_version, applied_at) "
        "VALUES (1, 2, ?)",
        (datetime(2026, 7, 21, tzinfo=UTC).isoformat(),),
    )
    connection.commit()
    connection.close()

    database = SQLiteDatabase(path)
    assert database.schema_version() == SCHEMA_VERSION == 3
    connection = database.connect()
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'lifecycle_proposals'"
        ).fetchone()
    finally:
        connection.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@async_test
async def test_repository_transition_contract_and_same_state_idempotence(tmp_path, backend):
    repo = _repository(backend, tmp_path)
    scope, access = _identity()
    _, version = await _version(repo, access, scope, "candidate", VersionLifecycleStatus.CANDIDATE)
    updated = await repo.transition_version_lifecycle(
        access,
        scope,
        version.version_id,
        **_transition_kwargs(VersionLifecycleStatus.CANDIDATE, VersionLifecycleStatus.ACTIVE),
    )
    assert updated.lifecycle_status is VersionLifecycleStatus.ACTIVE
    before = await repo.list_audit_for_entity(access, scope, "document_version", version.version_id)
    retried = await repo.transition_version_lifecycle(
        access,
        scope,
        version.version_id,
        **_transition_kwargs(VersionLifecycleStatus.CANDIDATE, VersionLifecycleStatus.ACTIVE),
    )
    after = await repo.list_audit_for_entity(access, scope, "document_version", version.version_id)
    assert retried == updated
    assert after == before
    event = [item for item in after if item.action == "lifecycle_transition"][0]
    assert event.metadata == {
        "policy_version": "phase3-v1",
        "proposal_id": None,
        "rule_results": ["authority:pass", "freshness:pass"],
        "run_id": "run-1",
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@async_test
async def test_illegal_transition_is_rejected_without_audit(tmp_path, backend):
    repo = _repository(backend, tmp_path)
    scope, access = _identity()
    _, version = await _version(repo, access, scope, "archived", VersionLifecycleStatus.ARCHIVED)
    before = await repo.list_audit_for_entity(access, scope, "document_version", version.version_id)
    with pytest.raises(InvalidTransitionError):
        await repo.transition_version_lifecycle(
            access,
            scope,
            version.version_id,
            **_transition_kwargs(VersionLifecycleStatus.ARCHIVED, VersionLifecycleStatus.ACTIVE),
        )
    assert await repo.list_audit_for_entity(access, scope, "document_version", version.version_id) == before


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@async_test
async def test_replacement_activation_supersedes_old_active_atomically(tmp_path, backend):
    repo = _repository(backend, tmp_path)
    scope, access = _identity()
    document, old = await _version(repo, access, scope, "old", VersionLifecycleStatus.ACTIVE)
    _, new = await _version(
        repo,
        access,
        scope,
        "new",
        VersionLifecycleStatus.CANDIDATE,
        document=document,
        supersedes=old.version_id,
    )
    await repo.transition_version_lifecycle(
        access,
        scope,
        new.version_id,
        **_transition_kwargs(VersionLifecycleStatus.CANDIDATE, VersionLifecycleStatus.ACTIVE),
    )
    assert (await repo.get_version(access, scope, new.version_id)).lifecycle_status is VersionLifecycleStatus.ACTIVE
    assert (await repo.get_version(access, scope, old.version_id)).lifecycle_status is VersionLifecycleStatus.SUPERSEDED
    old_events = await repo.list_audit_for_entity(access, scope, "document_version", old.version_id)
    assert [item.action for item in old_events].count("lifecycle_transition") == 1


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@async_test
async def test_evidence_validation_and_relation_replacement_are_atomic(tmp_path, backend):
    repo = _repository(backend, tmp_path)
    scope, access = _identity()
    _, version = await _version(repo, access, scope, "evidence", VersionLifecycleStatus.CANDIDATE)
    chunk = (await repo.add_chunks(access, scope, version.version_id, [ChunkInput(ordinal=0, text="fact")]))[0]
    evidence = await repo.add_evidence(
        access,
        scope,
        chunk_id=chunk.chunk_id,
        excerpt="fact",
        confidence=0.2,
        retrieval_method="fake",
    )
    replacement = await repo.transition_evidence_validation(
        access,
        scope,
        evidence.evidence_id,
        expected_status=EvidenceValidationStatus.PENDING,
        status=EvidenceValidationStatus.VALIDATED,
        relation=EvidenceRelation.CONTRADICTS,
        directness=EvidenceDirectness.DIRECT,
        confidence=0.9,
        valid_at=datetime(2026, 7, 20, tzinfo=UTC),
        actor_type="policy",
        reason="validated",
        policy_version="phase3-v1",
        rule_results=("direct:pass",),
        run_id="run-1",
        proposal_id=None,
        correlation_id="validate",
    )
    assert replacement.evidence_id != evidence.evidence_id
    assert replacement.validation_status is EvidenceValidationStatus.VALIDATED
    assert replacement.directness is EvidenceDirectness.DIRECT
    old = await repo.get_evidence(access, scope, evidence.evidence_id, include_deleted=True)
    assert old.soft_deleted_at is not None
    retried = await repo.transition_evidence_validation(
        access,
        scope,
        evidence.evidence_id,
        expected_status=EvidenceValidationStatus.PENDING,
        status=EvidenceValidationStatus.VALIDATED,
        relation=EvidenceRelation.CONTRADICTS,
        directness=EvidenceDirectness.DIRECT,
        confidence=0.9,
        valid_at=datetime(2026, 7, 20, tzinfo=UTC),
        actor_type="policy",
        reason="validated",
        policy_version="phase3-v1",
        rule_results=("direct:pass",),
        run_id="run-1",
        proposal_id=None,
        correlation_id="validate",
    )
    assert retried == replacement
    old_events = await repo.list_audit_for_entity(access, scope, "evidence", evidence.evidence_id)
    assert [item.action for item in old_events].count("evidence_replaced") == 1


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@async_test
async def test_agent_proposal_is_scoped_and_never_mutates_or_deletes_target(tmp_path, backend):
    repo = _repository(backend, tmp_path)
    scope, access = _identity()
    _, version = await _version(repo, access, scope, "proposal", VersionLifecycleStatus.ACTIVE)
    proposal = LifecycleProposal(
        scope_id=scope.scope_id,
        target_entity_type=LifecycleTargetType.DOCUMENT_VERSION,
        target_id=version.version_id,
        action=LifecycleProposalAction.PROPOSE_SOFT_DELETE,
        reason="possibly obsolete",
        proposed_by="agent",
        run_id="run-1",
        correlation_id="proposal",
    )
    assert await repo.create_lifecycle_proposal(access, scope, proposal) == proposal
    assert await repo.create_lifecycle_proposal(access, scope, proposal) == proposal
    stored = await repo.get_version(access, scope, version.version_id, include_deleted=True)
    assert stored.soft_deleted_at is None
    decided = await repo.transition_lifecycle_proposal(
        access,
        scope,
        proposal.proposal_id,
        expected_status=LifecycleProposalStatus.PENDING,
        status=LifecycleProposalStatus.REJECTED,
        actor_type="policy",
        reason="insufficient proof",
        policy_version="phase3-v1",
        rule_results=("proof:fail",),
        correlation_id="proposal-review",
    )
    assert decided.status is LifecycleProposalStatus.REJECTED
    assert await repo.list_lifecycle_proposals(access, scope, status=LifecycleProposalStatus.REJECTED) == [decided]

    other_scope, other_access = _identity("other")
    with pytest.raises(RepositoryAccessError):
        await repo.get_lifecycle_proposal(other_access, other_scope, proposal.proposal_id)


@async_test
async def test_sqlite_concurrent_transition_has_one_audit_event(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "concurrent.sqlite"))
    scope, access = _identity()
    _, version = await _version(repo, access, scope, "race", VersionLifecycleStatus.CANDIDATE)

    async def promote():
        return await repo.transition_version_lifecycle(
            access,
            scope,
            version.version_id,
            **_transition_kwargs(VersionLifecycleStatus.CANDIDATE, VersionLifecycleStatus.ACTIVE, "race"),
        )

    first, second = await asyncio.gather(promote(), promote())
    assert first.lifecycle_status is second.lifecycle_status is VersionLifecycleStatus.ACTIVE
    events = await repo.list_audit_for_entity(access, scope, "document_version", version.version_id)
    assert [item.action for item in events].count("lifecycle_transition") == 1
