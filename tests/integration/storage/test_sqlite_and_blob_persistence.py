import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from open_deep_research.knowledge.models import (
    ChunkInput,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
)
from open_deep_research.knowledge.repositories import (
    CorruptSchemaError,
    RepositoryNotFoundError,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.blob_repository import LocalBlobRepository
from open_deep_research.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase


def _identity():
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id="project",
        auth_source="integration-test",
        request_id="request",
    )
    return scope, access


async def _seed(repo, blobs, scope, access, source_path):
    source = await repo.upsert_source(
        access,
        scope,
        kind=SourceKind.LOCAL_FILE,
        internal_storage_ref=str(source_path),
        display_name="Local snapshot",
        correlation_id="seed",
    )
    document = await repo.upsert_document(
        access,
        scope,
        source_id=source.source_id,
        logical_key="main",
        title="Snapshot",
        media_type="text/plain",
        correlation_id="seed",
    )
    content = source_path.read_bytes()
    blob = await blobs.put(access, scope, content, "text/plain")
    version = await repo.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        correlation_id="seed",
    )
    chunk = (
        await repo.add_chunks(
            access,
            scope,
            version.version_id,
            [ChunkInput(ordinal=0, text="original snapshot")],
            correlation_id="seed",
        )
    )[0]
    requirement = await repo.add_requirement(
        access,
        scope,
        run_id="run",
        text="Preserve the original",
        correlation_id="seed",
    )
    evidence = await repo.add_evidence(
        access,
        scope,
        chunk_id=chunk.chunk_id,
        requirement_id=requirement.requirement_id,
        excerpt="original snapshot",
        confidence=1,
        retrieval_method="seed",
        correlation_id="seed",
    )
    return source, document, blob, version, chunk, requirement, evidence, content


def test_sqlite_reopen_and_original_blob_survive_source_overwrite(tmp_path):
    source_path = tmp_path / "source.txt"
    source_path.write_bytes(b"original snapshot")
    database_path = tmp_path / "knowledge.db"
    blob_root = tmp_path / "blobs"
    scope, access = _identity()

    repo = SQLiteRepository(str(database_path))
    blobs = LocalBlobRepository(blob_root)
    seeded = asyncio.run(_seed(repo, blobs, scope, access, source_path))
    source, document, blob, version, chunk, requirement, evidence, content = seeded
    asyncio.run(
        repo.soft_delete(
            access,
            scope,
            entity_type="evidence",
            entity_id=evidence.evidence_id,
            actor_type="test",
            reason="persistence check",
            correlation_id="persist-delete",
        )
    )
    source_path.write_bytes(b"overwritten after snapshot")

    reopened_repo = SQLiteRepository(str(database_path))
    reopened_blobs = LocalBlobRepository(blob_root)
    assert reopened_repo.schema_version == SCHEMA_VERSION == 1
    assert asyncio.run(reopened_repo.get_source(access, scope, source.source_id)) == source
    assert asyncio.run(
        reopened_repo.list_versions(access, scope, document.document_id)
    ) == [version]
    assert asyncio.run(reopened_repo.get_chunk(access, scope, chunk.chunk_id)) == chunk
    assert asyncio.run(
        reopened_repo.get_requirement(access, scope, requirement.requirement_id)
    ) == requirement
    with pytest.raises(RepositoryNotFoundError):
        asyncio.run(reopened_repo.get_evidence(access, scope, evidence.evidence_id))
    restored_evidence = asyncio.run(
        reopened_repo.get_evidence(
            access, scope, evidence.evidence_id, include_deleted=True
        )
    )
    assert restored_evidence.soft_deleted_at is not None
    assert asyncio.run(reopened_blobs.get(access, scope, blob.blob_id)) == content
    assert asyncio.run(
        reopened_blobs.verify(access, scope, blob.blob_id, blob.content_sha256)
    )
    audit = asyncio.run(
        reopened_repo.list_audit_for_correlation(
            access, scope, "persist-delete"
        )
    )
    assert [event.action for event in audit] == ["soft_deleted"]


def test_two_sqlite_writers_create_one_version_and_one_create_audit(tmp_path):
    async def scenario():
        database_path = tmp_path / "knowledge.db"
        scope, access = _identity()
        setup_repo = SQLiteRepository(str(database_path), busy_timeout_ms=10_000)
        blobs = LocalBlobRepository(tmp_path / "blobs")
        source = await setup_repo.upsert_source(
            access,
            scope,
            kind=SourceKind.WEB,
            canonical_uri="https://example.com/concurrent",
            display_name="Concurrent",
        )
        document = await setup_repo.upsert_document(
            access,
            scope,
            source_id=source.source_id,
            logical_key="main",
            title="Concurrent",
            media_type="text/plain",
        )
        blob = await blobs.put(access, scope, b"concurrent", "text/plain")
        repo_one = SQLiteRepository(str(database_path), busy_timeout_ms=10_000)
        repo_two = SQLiteRepository(str(database_path), busy_timeout_ms=10_000)

        async def add(repo):
            return await repo.add_version(
                access,
                scope,
                document_id=document.document_id,
                blob=blob,
                retrieved_at=datetime.now(UTC),
                correlation_id="concurrent",
            )

        first, second = await asyncio.gather(add(repo_one), add(repo_two))
        assert first.version_id == second.version_id
        assert len(
            await setup_repo.list_versions(access, scope, document.document_id)
        ) == 1
        audit = await setup_repo.list_audit_for_entity(
            access, scope, "document_version", first.version_id
        )
        assert [event.action for event in audit].count("created") == 1

    asyncio.run(scenario())


def test_missing_foreign_keys_fail_and_schema_version_is_rejected(tmp_path):
    async def missing_reference():
        repo = SQLiteRepository(str(tmp_path / "valid.db"))
        scope, access = _identity()
        with pytest.raises(RepositoryNotFoundError):
            await repo.add_evidence(
                access,
                scope,
                chunk_id="chk_" + "a" * 64,
                excerpt="not real",
                confidence=0.5,
                retrieval_method="test",
            )
        with pytest.raises(RepositoryNotFoundError):
            await repo.add_requirement(
                access,
                scope,
                text="child",
                parent_id="req_" + "b" * 64,
            )

    asyncio.run(missing_reference())

    corrupt_path = tmp_path / "future.db"
    connection = sqlite3.connect(corrupt_path)
    connection.execute(
        "CREATE TABLE schema_metadata "
        "(singleton INTEGER PRIMARY KEY, schema_version INTEGER, applied_at TEXT)"
    )
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1, 2, '2026-07-20T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    with pytest.raises(CorruptSchemaError):
        SQLiteDatabase(corrupt_path)

    incomplete_path = tmp_path / "incomplete.db"
    connection = sqlite3.connect(incomplete_path)
    connection.execute(
        "CREATE TABLE schema_metadata "
        "(singleton INTEGER PRIMARY KEY, schema_version INTEGER, applied_at TEXT)"
    )
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1, 1, '2026-07-20T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    with pytest.raises(CorruptSchemaError):
        SQLiteDatabase(incomplete_path)
