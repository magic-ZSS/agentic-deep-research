import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.ingestion.models import (
    ImportIndexStatus,
    ImportInputKind,
    ImportJob,
    ImportJobError,
    ImportJobStatus,
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
    RepositoryAccessError,
    RepositoryConflictError,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.storage.migrations import MIGRATION_V1
from open_deep_research.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase


def _identity(project="project"):
    scope = KnowledgeScope(tenant_id="tenant", project_id=project)
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id=project,
        auth_source="phase2-contract",
        request_id=f"request-{project}",
    )
    return scope, access


def _repository(tmp_path, backend):
    if backend == "memory":
        return InMemoryRepository()
    return SQLiteRepository(str(tmp_path / "knowledge.db"))


async def _seed_chain(repository, scope, access):
    content = b"phase two snapshot"
    source = await repository.upsert_source(
        access,
        scope,
        kind=SourceKind.LOCAL_FILE,
        internal_storage_ref=r"C:\fixtures\phase-two.md",
        display_name="Phase two",
    )
    document = await repository.upsert_document(
        access,
        scope,
        source_id=source.source_id,
        logical_key="main",
        title="Phase two",
        media_type="text/markdown",
    )
    blob = ContentBlob.from_bytes(
        scope_id=scope.scope_id,
        content=content,
        media_type="text/markdown",
        storage_ref=f"{scope.scope_id}/fixture.blob",
    )
    version = await repository.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    chunk = (
        await repository.add_chunks(
            access,
            scope,
            version.version_id,
            [ChunkInput(ordinal=0, text="phase two snapshot")],
        )
    )[0]
    return source, document, blob, version, chunk


async def _exercise_import_job_contract(repository, scope, access):
    source, document, blob, version, chunk = await _seed_chain(
        repository, scope, access
    )
    assert version.lifecycle_status is VersionLifecycleStatus.CANDIDATE
    job = ImportJob(
        scope_id=scope.scope_id,
        input_kind=ImportInputKind.MARKDOWN,
        input_ref=r"C:\fixtures\phase-two.md",
        content_sha256=blob.content_sha256,
        parser_name="markdown-parser",
        parser_version="1",
        chunk_config={"size": 500},
    )
    stored = await repository.create_import_job(access, scope, job)
    duplicate = await repository.create_import_job(access, scope, job)
    assert duplicate.job_id == stored.job_id

    running = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.PENDING,
        status=ImportJobStatus.RUNNING,
        actor_type="test",
        reason="claim",
        correlation_id="phase2-job",
    )
    failed = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.RUNNING,
        status=ImportJobStatus.FAILED,
        error=ImportJobError(
            code="parse_failed",
            stage="parse",
            message="deterministic failure",
            retryable=True,
        ),
        actor_type="test",
        reason="fixture failure",
        correlation_id="phase2-job",
    )
    assert failed.attempt_count == running.attempt_count == 1
    retried = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.FAILED,
        status=ImportJobStatus.RUNNING,
        actor_type="test",
        reason="retry",
        correlation_id="phase2-job",
    )
    assert retried.attempt_count == 2
    assert retried.error is None
    succeeded = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.RUNNING,
        status=ImportJobStatus.SUCCEEDED,
        blob_id=blob.blob_id,
        source_id=source.source_id,
        document_id=document.document_id,
        version_id=version.version_id,
        actor_type="test",
        reason="persisted",
        correlation_id="phase2-job",
    )
    pending_index = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.SUCCEEDED,
        status=ImportJobStatus.SUCCEEDED,
        expected_index_status=ImportIndexStatus.NOT_REQUESTED,
        index_status=ImportIndexStatus.PENDING,
        actor_type="test",
        reason="index start",
        correlation_id="phase2-job",
    )
    ready = await repository.transition_import_job(
        access,
        scope,
        job.job_id,
        expected_status=ImportJobStatus.SUCCEEDED,
        status=ImportJobStatus.SUCCEEDED,
        expected_index_status=ImportIndexStatus.PENDING,
        index_status=ImportIndexStatus.READY,
        actor_type="test",
        reason="index ready",
        correlation_id="phase2-job",
    )
    assert succeeded.status is ImportJobStatus.SUCCEEDED
    assert pending_index.index_status is ImportIndexStatus.PENDING
    assert ready.index_status is ImportIndexStatus.READY
    with pytest.raises(RepositoryConflictError):
        await repository.transition_import_job(
            access,
            scope,
            job.job_id,
            expected_status=ImportJobStatus.FAILED,
            status=ImportJobStatus.RUNNING,
            actor_type="test",
            reason="stale writer",
            correlation_id="phase2-job",
        )

    assert await repository.list_sources(access, scope) == [source]
    assert await repository.list_documents(access, scope) == [document]
    assert await repository.get_content_blob_metadata(
        access, scope, blob.blob_id
    ) == blob
    assert await repository.list_content_blob_metadata(access, scope) == [blob]
    assert await repository.list_versions_for_scope(access, scope) == [version]
    assert await repository.list_chunks_for_version(
        access, scope, version.version_id
    ) == [chunk]
    assert await repository.list_import_jobs(
        access, scope, index_status=ImportIndexStatus.READY
    ) == [ready]
    audit = await repository.list_audit_for_entity(
        access, scope, "import_job", job.job_id
    )
    actions = [event.action for event in audit]
    assert actions.count("created") == 1
    assert actions.count("state_changed") == 6
    return job.job_id, ready


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_phase2_repository_contract_matches_both_backends(tmp_path, backend):
    scope, access = _identity()
    repository = _repository(tmp_path / backend, backend)
    asyncio.run(_exercise_import_job_contract(repository, scope, access))


def test_import_job_and_enumeration_are_scope_isolated(tmp_path):
    async def scenario():
        repository = SQLiteRepository(str(tmp_path / "knowledge.db"))
        scope_a, access_a = _identity("project-a")
        scope_b, access_b = _identity("project-b")
        job_id, _ = await _exercise_import_job_contract(
            repository, scope_a, access_a
        )
        with pytest.raises(RepositoryAccessError):
            await repository.get_import_job(access_b, scope_b, job_id)
        assert await repository.list_import_jobs(access_b, scope_b) == []
        assert await repository.list_sources(access_b, scope_b) == []

    asyncio.run(scenario())


def test_existing_v1_database_migrates_to_v2_without_changing_v1(tmp_path):
    database_path = tmp_path / "v1.db"
    connection = sqlite3.connect(database_path)
    connection.execute("BEGIN IMMEDIATE")
    for statement in MIGRATION_V1.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_metadata(singleton, schema_version, applied_at) "
        "VALUES (1, 1, ?)",
        (datetime(2026, 7, 21, tzinfo=UTC).isoformat(),),
    )
    connection.commit()
    connection.close()

    database = SQLiteDatabase(database_path)
    assert database.schema_version() == SCHEMA_VERSION == 2
    reopened = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in reopened.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        reopened.close()
    assert "import_jobs" in tables


def test_two_sqlite_workers_claim_import_job_with_cas(tmp_path):
    async def scenario():
        database_path = tmp_path / "knowledge.db"
        setup = SQLiteRepository(str(database_path), busy_timeout_ms=10_000)
        scope, access = _identity()
        job = ImportJob(
            scope_id=scope.scope_id,
            input_kind=ImportInputKind.HTML_SNAPSHOT,
            input_ref=r"C:\fixtures\snapshot.html",
            content_sha256="a" * 64,
            parser_name="html-parser",
            parser_version="1",
        )
        await setup.create_import_job(access, scope, job)
        workers = (
            SQLiteRepository(str(database_path), busy_timeout_ms=10_000),
            SQLiteRepository(str(database_path), busy_timeout_ms=10_000),
        )

        async def claim(repository):
            return await repository.transition_import_job(
                access,
                scope,
                job.job_id,
                expected_status=ImportJobStatus.PENDING,
                status=ImportJobStatus.RUNNING,
                actor_type="test-worker",
                reason="claim",
                correlation_id="concurrent-claim",
            )

        results = await asyncio.gather(
            *(claim(repository) for repository in workers),
            return_exceptions=True,
        )
        assert sum(isinstance(item, ImportJob) for item in results) == 1
        assert sum(isinstance(item, RepositoryConflictError) for item in results) == 1
        audit = await setup.list_audit_for_entity(
            access, scope, "import_job", job.job_id
        )
        assert [event.action for event in audit].count("state_changed") == 1

    asyncio.run(scenario())
