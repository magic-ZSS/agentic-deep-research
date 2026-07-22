"""Deterministic Phase 6 evidence fixtures."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.evidence.run_store import (
    InMemoryRunEvidenceStore,
    RunEvidenceBundle,
    RunEvidenceContext,
    RunEvidenceValidationStatus,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import (
    AuthorityClass,
    Chunk,
    ChunkInput,
    ChunkLocatorType,
    ContentBlob,
    Document,
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
    Visibility,
)


NOW = datetime(2026, 7, 21, tzinfo=UTC)


def identity(user: str = "alice"):
    scope = KnowledgeScope(
        tenant_id="tenant",
        project_id="project",
        owner_user_id=user,
        visibility=Visibility.PRIVATE,
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id="project",
        trusted_user_id=user,
        auth_source="test",
        request_id="phase6-test",
    )
    return scope, access


async def seed_canonical(
    repository: InMemoryRepository,
    scope: KnowledgeScope,
    access: KnowledgeAccessContext,
    *,
    suffix: str,
    text: str,
    authority: AuthorityClass = AuthorityClass.OFFICIAL,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    directness: EvidenceDirectness = EvidenceDirectness.DIRECT,
    status: VersionLifecycleStatus = VersionLifecycleStatus.ACTIVE,
    valid_from: datetime | None = NOW - timedelta(days=10),
    valid_to: datetime | None = NOW + timedelta(days=10),
    source_kind: SourceKind = SourceKind.WEB,
    internal_ref: str | None = None,
    public_uri: str | None = None,
    document_key: str | None = None,
):
    source = await repository.upsert_source(
        access,
        scope,
        kind=source_kind,
        display_name=(
            rf"C:\private\docs\{suffix}.md"
            if source_kind is SourceKind.LOCAL_FILE
            else f"Source {suffix}"
        ),
        canonical_uri=(
            f"https://example.test/{suffix}"
            if source_kind is SourceKind.WEB
            else None
        ),
        internal_storage_ref=internal_ref,
        public_display_uri=public_uri,
        publisher="Fixture Publisher",
        authority_class=authority,
    )
    document = await repository.upsert_document(
        access,
        scope,
        source_id=source.source_id,
        logical_key=document_key or suffix,
        title=(
            rf"C:\private\docs\{suffix}.md"
            if source_kind is SourceKind.LOCAL_FILE
            else f"Document {suffix}"
        ),
        media_type="text/plain",
    )
    content = text.encode()
    blob = ContentBlob.from_bytes(
        scope_id=scope.scope_id,
        content=content,
        media_type="text/plain",
        storage_ref=f"private/{suffix}.blob",
    )
    version = await repository.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=NOW,
        valid_from=valid_from,
        valid_to=valid_to,
        lifecycle_status=status,
    )
    chunk = (
        await repository.add_chunks(
            access,
            scope,
            version.version_id,
            [
                ChunkInput(
                    ordinal=0,
                    text=text,
                    locator_type=ChunkLocatorType.PAGE,
                    page_start=2,
                )
            ],
        )
    )[0]
    evidence = await repository.add_evidence(
        access,
        scope,
        chunk_id=chunk.chunk_id,
        excerpt=text,
        confidence=0.95,
        retrieval_method="phase6-fixture",
        relation=relation,
        directness=directness,
        validation_status=EvidenceValidationStatus.VALIDATED,
    )
    return source, document, version, chunk, evidence


async def seed_transient(
    store: InMemoryRunEvidenceStore,
    scope: KnowledgeScope,
    *,
    run_id: str,
    text: str,
):
    context = RunEvidenceContext(scope_id=scope.scope_id, run_id=run_id)
    source = Source(
        scope_id=scope.scope_id,
        kind=SourceKind.WEB,
        canonical_uri="https://example.test/transient",
        public_display_uri="https://example.test/transient",
        display_name="Transient source",
        publisher="Transient Publisher",
        authority_class=AuthorityClass.OFFICIAL,
    )
    document = Document(
        scope_id=scope.scope_id,
        source_id=source.source_id,
        logical_key="transient",
        title="Transient document",
        media_type="text/html",
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    version = DocumentVersion(
        scope_id=scope.scope_id,
        document_id=document.document_id,
        blob_id=f"blob_{digest}",
        content_sha256=digest,
        version_number=1,
        retrieved_at=NOW,
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=1),
        lifecycle_status=VersionLifecycleStatus.CANDIDATE,
    )
    chunk = Chunk(
        scope_id=scope.scope_id,
        version_id=version.version_id,
        ordinal=0,
        text=text,
        locator_type=ChunkLocatorType.ANCHOR,
        anchor="transient-result",
    )
    from open_deep_research.evidence.models import Evidence

    evidence = Evidence(
        scope_id=scope.scope_id,
        chunk_id=chunk.chunk_id,
        excerpt=text,
        confidence=0.95,
        retrieval_method="phase6-transient-fixture",
        directness=EvidenceDirectness.DIRECT,
        validation_status=EvidenceValidationStatus.PENDING,
    )
    bundle = RunEvidenceBundle.create(
        context=context,
        source=source,
        document=document,
        version=version,
        chunk=chunk,
        evidence=evidence,
        # The evidence validity interval is evaluated against the fixed NOW
        # fixture.  Keep the run-store TTL far enough in the future that the
        # deterministic fixture does not expire according to wall-clock time.
        ttl=timedelta(days=36_500),
        now=NOW,
    )
    await store.put(context, bundle)
    return await store.compare_and_set_validation(
        context,
        bundle.evidence_id,
        expected=RunEvidenceValidationStatus.PENDING,
        status=RunEvidenceValidationStatus.VALIDATED_FOR_RUN,
        reason="phase6 fixture",
        actor="phase6-policy",
    )
