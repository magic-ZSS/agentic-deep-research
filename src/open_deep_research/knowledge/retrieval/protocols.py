"""Retrieval protocols isolated from PaperQA and production agent tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from open_deep_research.evidence.models import Evidence
from open_deep_research.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
)
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RetrievalRecord,
)


@runtime_checkable
class RetrievalRepositoryProjection(Protocol):
    """Minimum read projection expected from Phase-1 repositories."""

    async def list_sources(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        include_deleted: bool = False,
    ) -> list[Source]: ...

    async def list_documents(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        *,
        include_deleted: bool = False,
    ) -> list[Document]: ...

    async def list_versions(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[DocumentVersion]: ...

    async def list_chunks_for_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Chunk]: ...

    async def list_evidence_for_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Evidence]: ...

    async def get_source(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        source_id: str,
        *,
        include_deleted: bool = False,
    ) -> Source: ...

    async def get_document(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        document_id: str,
        *,
        include_deleted: bool = False,
    ) -> Document: ...

    async def get_version(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentVersion: ...

    async def get_chunk(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        chunk_id: str,
        *,
        include_deleted: bool = False,
    ) -> Chunk: ...

    async def get_evidence(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        evidence_id: str,
        *,
        include_deleted: bool = False,
    ) -> Evidence: ...


@runtime_checkable
class RetrievalCatalog(Protocol):
    """Authorized aggregate projection, independent of storage technology."""

    async def list_records(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> Sequence[RetrievalRecord]: ...

    async def get_record(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        stable_id: str,
    ) -> RetrievalRecord: ...


@runtime_checkable
class KnowledgeRetriever(Protocol):
    """Project-owned retrieval boundary used by inspection services."""

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeSearchResult: ...

    async def read(
        self,
        request: KnowledgeReadRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeReadResult: ...
