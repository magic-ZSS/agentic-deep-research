import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_deep_research.evidence.models import EvidenceValidationStatus
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import (
    ChunkInput,
    ChunkLocatorType,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    VersionLifecycleStatus,
    Visibility,
)
from open_deep_research.knowledge.repositories import (
    KnowledgeEvidenceRepository,
    RepositoryAccessError,
    RepositoryNotFoundError,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.blob_repository import (
    InMemoryBlobRepository,
    LocalBlobRepository,
)


def _scope(
    *,
    tenant: str = "tenant-a",
    project: str = "project-a",
    owner: str | None = None,
    visibility: Visibility = Visibility.PROJECT,
):
    scope = KnowledgeScope(
        tenant_id=tenant,
        project_id=project,
        owner_user_id=owner,
        visibility=visibility,
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id=tenant,
        trusted_project_id=project,
        trusted_user_id=owner,
        auth_source="integration-test",
        request_id=f"request-{tenant}-{project}-{owner}",
    )
    return scope, access


def _backend(tmp_path: Path, name: str):
    if name == "memory":
        return InMemoryRepository(), InMemoryBlobRepository()
    return (
        SQLiteRepository(str(tmp_path / "knowledge.db")),
        LocalBlobRepository(tmp_path / "blobs"),
    )


async def _create_source_document(repo, access, scope, suffix="one"):
    source = await repo.upsert_source(
        access,
        scope,
        kind=SourceKind.WEB,
        canonical_uri=f"https://example.com/{suffix}",
        display_name=f"Source {suffix}",
        correlation_id="contract",
    )
    document = await repo.upsert_document(
        access,
        scope,
        source_id=source.source_id,
        logical_key="main",
        title=f"Document {suffix}",
        media_type="text/markdown",
        correlation_id="contract",
    )
    return source, document


async def _exercise_shared_contract(repo, blob_repo, scope, access):
    source, document = await _create_source_document(repo, access, scope)
    with pytest.raises(RepositoryNotFoundError):
        await repo.add_evidence(
            access,
            scope,
            chunk_id="chk_" + "0" * 64,
            excerpt="missing chunk",
            confidence=0.1,
            retrieval_method="contract-test",
        )
    blob = await blob_repo.put(access, scope, b"version one\r\n", "text/markdown")
    version = await repo.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        lifecycle_status=VersionLifecycleStatus.ACTIVE,
        correlation_id="contract",
    )
    duplicate = await repo.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
        lifecycle_status=VersionLifecycleStatus.CANDIDATE,
        correlation_id="duplicate",
    )
    assert duplicate.version_id == version.version_id
    assert duplicate.version_number == 1
    assert len(await repo.list_versions(access, scope, document.document_id)) == 1

    chunks = await repo.add_chunks(
        access,
        scope,
        version.version_id,
        [
            ChunkInput(
                ordinal=0,
                text="Page evidence",
                locator_type=ChunkLocatorType.PAGE,
                page_start=4,
            ),
            ChunkInput(
                ordinal=1,
                text="Heading evidence",
                locator_type=ChunkLocatorType.HEADING,
                heading_path=("Design", "Storage"),
            ),
        ],
        correlation_id="contract",
    )
    requirement = await repo.add_requirement(
        access,
        scope,
        run_id="run-1",
        text="Prove storage behavior",
        correlation_id="contract",
    )
    evidence = await repo.add_evidence(
        access,
        scope,
        chunk_id=chunks[0].chunk_id,
        requirement_id=requirement.requirement_id,
        excerpt="Page evidence",
        confidence=0.95,
        retrieval_method="contract-test",
        validation_status=EvidenceValidationStatus.VALIDATED,
        correlation_id="contract",
    )

    traced_chunk = await repo.get_chunk(access, scope, evidence.chunk_id)
    traced_version = await repo.get_version(access, scope, traced_chunk.version_id)
    traced_document = await repo.get_document(
        access, scope, traced_version.document_id
    )
    traced_source = await repo.get_source(access, scope, traced_document.source_id)
    assert traced_source.source_id == source.source_id
    assert await repo.list_evidence_for_source(access, scope, source.source_id) == [
        evidence
    ]

    source_two, document_two = await _create_source_document(
        repo, access, scope, "two"
    )
    same_blob = await blob_repo.put(
        access, scope, b"version one\r\n", "text/markdown"
    )
    assert same_blob.blob_id == blob.blob_id
    second_chain = await repo.add_version(
        access,
        scope,
        document_id=document_two.document_id,
        blob=same_blob,
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        correlation_id="contract",
    )
    assert second_chain.document_id != version.document_id

    changed_blob = await blob_repo.put(
        access, scope, b"version two\n", "text/markdown"
    )
    changed_version = await repo.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=changed_blob,
        retrieved_at=datetime(2026, 7, 22, tzinfo=UTC),
        supersedes_version_id=version.version_id,
        correlation_id="contract",
    )
    assert changed_version.version_number == 2
    assert (await repo.get_chunk(access, scope, chunks[0].chunk_id)).version_id == (
        version.version_id
    )
    assert (await repo.get_evidence(access, scope, evidence.evidence_id)).chunk_id == (
        chunks[0].chunk_id
    )

    before_delete_counts = repo.entity_counts(access, scope)
    deleted = await repo.soft_delete(
        access,
        scope,
        entity_type="evidence",
        entity_id=evidence.evidence_id,
        actor_type="test",
        reason="contract soft delete",
        correlation_id="delete-contract",
    )
    assert deleted.action == "soft_deleted"
    with pytest.raises(RepositoryNotFoundError):
        await repo.get_evidence(access, scope, evidence.evidence_id)
    assert (
        await repo.get_evidence(
            access, scope, evidence.evidence_id, include_deleted=True
        )
    ).soft_deleted_at is not None
    assert repo.entity_counts(access, scope) == before_delete_counts
    audit = await repo.list_audit_for_entity(
        access, scope, "evidence", evidence.evidence_id
    )
    assert [event.action for event in audit].count("soft_deleted") == 1

    for entity_type, entity_id, getter in (
        ("chunk", chunks[0].chunk_id, repo.get_chunk),
        ("document_version", version.version_id, repo.get_version),
        ("source", source.source_id, repo.get_source),
    ):
        await repo.soft_delete(
            access,
            scope,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_type="test",
            reason="contract soft delete",
            correlation_id="delete-contract",
        )
        with pytest.raises(RepositoryNotFoundError):
            await getter(access, scope, entity_id)
        deleted_model = await getter(
            access, scope, entity_id, include_deleted=True
        )
        assert deleted_model.soft_deleted_at is not None
    assert repo.entity_counts(access, scope) == before_delete_counts

    return {
        "source_ids": sorted((source.source_id, source_two.source_id)),
        "version_ids": sorted(
            (version.version_id, second_chain.version_id, changed_version.version_id)
        ),
        "chunk_ids": [item.chunk_id for item in chunks],
        "evidence_id": evidence.evidence_id,
        "counts": repo.entity_counts(access, scope),
        "blob_count": blob_repo.count(access, scope),
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_same_repository_contract_suite_covers_both_backends(tmp_path, backend):
    scope, access = _scope()
    repo, blob_repo = _backend(tmp_path / backend, backend)
    assert isinstance(repo, KnowledgeEvidenceRepository)
    result = asyncio.run(_exercise_shared_contract(repo, blob_repo, scope, access))
    assert result["counts"] == {
        "sources": 2,
        "documents": 2,
        "blobs": 2,
        "versions": 3,
        "chunks": 2,
        "requirements": 1,
        "evidence": 1,
    }
    assert result["blob_count"] == 2


def test_backends_have_identical_stable_observable_contract(tmp_path):
    scope, access = _scope()
    memory_result = asyncio.run(
        _exercise_shared_contract(
            *_backend(tmp_path / "memory", "memory"), scope, access
        )
    )
    sqlite_result = asyncio.run(
        _exercise_shared_contract(
            *_backend(tmp_path / "sqlite", "sqlite"), scope, access
        )
    )
    assert memory_result == sqlite_result


def test_scope_isolation_applies_to_metadata_and_blob_dedupe(tmp_path):
    async def scenario():
        repo = SQLiteRepository(str(tmp_path / "knowledge.db"))
        blobs = LocalBlobRepository(tmp_path / "blobs")
        scope_a, access_a = _scope(project="project-a")
        scope_b, access_b = _scope(project="project-b")
        source_a, document_a = await _create_source_document(
            repo, access_a, scope_a, "shared"
        )
        source_b, document_b = await _create_source_document(
            repo, access_b, scope_b, "shared"
        )
        blob_a = await blobs.put(access_a, scope_a, b"same bytes", "text/plain")
        blob_b = await blobs.put(access_b, scope_b, b"same bytes", "text/plain")
        version_a = await repo.add_version(
            access_a,
            scope_a,
            document_id=document_a.document_id,
            blob=blob_a,
            retrieved_at=datetime.now(UTC),
        )
        version_b = await repo.add_version(
            access_b,
            scope_b,
            document_id=document_b.document_id,
            blob=blob_b,
            retrieved_at=datetime.now(UTC),
        )
        assert source_a.source_id != source_b.source_id
        assert blob_a.blob_id != blob_b.blob_id
        assert version_a.version_id != version_b.version_id
        assert blobs.count(access_a, scope_a) == blobs.count(access_b, scope_b) == 1
        with pytest.raises(RepositoryAccessError):
            blobs.count(access_a, scope_b)
        with pytest.raises(RepositoryAccessError):
            repo.entity_counts(access_a, scope_b)
        with pytest.raises(RepositoryAccessError):
            await repo.get_version(access_a, scope_a, version_b.version_id)
        with pytest.raises(RepositoryAccessError):
            await blobs.get(access_a, scope_a, blob_b.blob_id)

    asyncio.run(scenario())


def test_private_scope_requires_matching_trusted_user(tmp_path):
    async def scenario():
        repo = InMemoryRepository()
        scope, access = _scope(
            owner="alice", visibility=Visibility.PRIVATE
        )
        source, _ = await _create_source_document(repo, access, scope)
        denied = access.model_copy(update={"trusted_user_id": "bob"})
        with pytest.raises(RepositoryAccessError):
            await repo.get_source(denied, scope, source.source_id)

    asyncio.run(scenario())
