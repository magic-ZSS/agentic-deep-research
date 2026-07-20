from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from open_deep_research.evidence.models import EvidenceDirectness, EvidenceValidationStatus
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import (
    ChunkInput, ChunkLocatorType, ContentBlob, KnowledgeAccessContext,
    KnowledgeScope, SourceKind, VersionLifecycleStatus,
)
from open_deep_research.knowledge.repositories import RepositoryAccessError
from open_deep_research.knowledge.retrieval.models import KnowledgeSearchRequest
from open_deep_research.knowledge.retrieval.repository_retriever import RepositoryKnowledgeRetriever, RepositoryRetrievalCatalog
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.mcp.config import AllowedRoot, RootMode
from open_deep_research.mcp.filesystem_policy import AllowedRootsPolicy
from open_deep_research.mcp.staging import ExclusiveCreateStaging
from open_deep_research.mcp_servers.schemas import KnowledgeMCPContext
from open_deep_research.mcp_servers.services import KnowledgeMCPService
from open_deep_research.mcp_servers.knowledge_server import create_knowledge_server


async def _fixture(tmp_path):
    repo = InMemoryRepository()
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    access = KnowledgeAccessContext(trusted_tenant_id="tenant", trusted_project_id="project", auth_source="test", request_id="request")
    source = await repo.upsert_source(access, scope, kind=SourceKind.LOCAL_FILE, display_name="Public doc", internal_storage_ref="private/internal/doc.md", public_display_uri="https://example.test/doc")
    document = await repo.upsert_document(access, scope, source_id=source.source_id, logical_key="doc", title="Public doc", media_type="text/markdown")
    content = b"governed MCP evidence"
    blob = ContentBlob.from_bytes(scope_id=scope.scope_id, content=content, media_type="text/markdown", storage_ref="private/blob")
    version = await repo.add_version(access, scope, document_id=document.document_id, blob=blob, retrieved_at=datetime.now(UTC), lifecycle_status=VersionLifecycleStatus.ACTIVE)
    chunk = (await repo.add_chunks(access, scope, version.version_id, [ChunkInput(ordinal=0, text=content.decode(), locator_type=ChunkLocatorType.HEADING, heading_path=("MCP",))]))[0]
    evidence = await repo.add_evidence(access, scope, chunk_id=chunk.chunk_id, excerpt=chunk.text, confidence=.95, retrieval_method="fixture", directness=EvidenceDirectness.DIRECT, validation_status=EvidenceValidationStatus.VALIDATED)
    retriever = RepositoryKnowledgeRetriever(RepositoryRetrievalCatalog(repo))
    staging_root = tmp_path / "staging"; staging_root.mkdir()
    staging = ExclusiveCreateStaging(AllowedRootsPolicy((AllowedRoot(root_id="staging", path=str(staging_root), mode=RootMode.IMPORT_STAGING, public_alias="imports"),)))
    artifact = staging.exclusive_create(root_id="staging", relative_locator="new.md", content=b"candidate", media_type="text/markdown", run_id="run", request_id="request", actor="test")
    service = KnowledgeMCPService(retriever=retriever, repository=repo, context=KnowledgeMCPContext(access=access, scope=scope, actor="mcp", run_id="run"), staging=staging)
    return repo, scope, access, retriever, service, source, version, evidence, artifact


def test_kb_search_matches_internal_retriever_and_public_reads(tmp_path) -> None:
    async def run():
        repo, scope, access, retriever, service, source, version, evidence, artifact = await _fixture(tmp_path)
        internal = await retriever.search(KnowledgeSearchRequest(query="governed MCP"), access=access, scope=scope)
        external = await service.kb_search("governed MCP")
        assert [hit.evidence_id for hit in internal.hits] == [hit["evidence_id"] for hit in external["hits"]]
        assert (await service.kb_read(evidence.evidence_id))["hit"]["source"]["source_id"] == source.source_id
        public = await service.kb_get_source(source.source_id)
        assert "internal_storage_ref" not in public
        assert "private/internal" not in str(public)
    asyncio.run(run())


def test_all_knowledge_writes_only_create_pending_proposals(tmp_path) -> None:
    async def run():
        repo, scope, access, retriever, service, source, version, evidence, artifact = await _fixture(tmp_path)
        before = (await repo.get_version(access, scope, version.version_id)).lifecycle_status
        values = [
            await service.kb_propose_ingest(artifact.artifact_id, reason="review import"),
            await service.kb_propose_stale(version.version_id, reason="possibly old"),
            await service.kb_propose_quarantine(version.version_id, reason="quality review"),
        ]
        assert {item["status"] for item in values} == {"pending"}
        assert (await repo.get_version(access, scope, version.version_id)).lifecycle_status is before
        assert len(await repo.list_lifecycle_proposals(access, scope)) == 3
        assert len(await repo.list_audit_for_correlation(access, scope, access.request_id)) == 3
    asyncio.run(run())


def test_scope_is_trusted_and_cross_project_probe_is_indistinguishable(tmp_path) -> None:
    async def run():
        repo, scope, access, retriever, service, source, version, evidence, artifact = await _fixture(tmp_path)
        other_scope = KnowledgeScope(tenant_id="tenant", project_id="other")
        bad_service = KnowledgeMCPService(retriever=retriever, repository=repo, context=KnowledgeMCPContext(access=access, scope=other_scope, actor="mcp"))
        with pytest.raises(RepositoryAccessError, match="not authorized"):
            await bad_service.kb_get_source(source.source_id)
        with pytest.raises(RepositoryAccessError, match="not authorized"):
            await bad_service.kb_get_source("src_" + "0" * 64)
    asyncio.run(run())


def test_tool_annotations_match_read_and_proposal_semantics(tmp_path) -> None:
    async def run():
        *_, service, source, version, evidence, artifact = await _fixture(tmp_path)
        tools = {item.name: item for item in await create_knowledge_server(service).list_tools()}
        assert set(tools) == {"kb_search", "kb_read", "kb_get_source", "kb_search_past_queries", "kb_propose_ingest", "kb_propose_stale", "kb_propose_quarantine"}
        assert all(tools[name].annotations.readOnlyHint for name in ("kb_search", "kb_read", "kb_get_source", "kb_search_past_queries"))
        assert all(not tools[name].annotations.readOnlyHint and not tools[name].annotations.destructiveHint for name in ("kb_propose_ingest", "kb_propose_stale", "kb_propose_quarantine"))
    asyncio.run(run())


def test_sqlite_persists_scope_bound_ingest_proposal(tmp_path) -> None:
    async def run():
        repository = SQLiteRepository(str(tmp_path / "knowledge.sqlite"))
        scope = KnowledgeScope(tenant_id="tenant", project_id="project")
        access = KnowledgeAccessContext(trusted_tenant_id="tenant", trusted_project_id="project", auth_source="test", request_id="request")
        staging_root = tmp_path / "staging"; staging_root.mkdir()
        staging = ExclusiveCreateStaging(AllowedRootsPolicy((AllowedRoot(root_id="staging", path=str(staging_root), mode=RootMode.IMPORT_STAGING, public_alias="imports"),)))
        artifact = staging.exclusive_create(root_id="staging", relative_locator="candidate.md", content=b"candidate", media_type="text/markdown", run_id="run", request_id="request", actor="test")
        service = KnowledgeMCPService(retriever=RepositoryKnowledgeRetriever(RepositoryRetrievalCatalog(repository)), repository=repository, context=KnowledgeMCPContext(access=access, scope=scope, actor="mcp", run_id="run"), staging=staging)
        result = await service.kb_propose_ingest(artifact.artifact_id, reason="review")
        reopened = SQLiteRepository(str(tmp_path / "knowledge.sqlite"))
        stored = await reopened.get_lifecycle_proposal(access, scope, result["proposal_id"])
        assert stored.status.value == "pending"
        assert stored.target_id == artifact.artifact_id
    asyncio.run(run())
