from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import wraps

import pytest
from pydantic import ValidationError

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceValidationStatus,
)
from open_deep_research.evidence.run_store import (
    InMemoryRunEvidenceStore,
    RunEvidenceBundle,
    RunEvidenceConflictError,
    RunEvidenceContext,
    RunEvidenceNotFoundError,
    RunEvidenceStore,
    RunEvidenceValidationStatus,
    SQLiteRunEvidenceStore,
)
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkLocatorType,
    Document,
    DocumentVersion,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)


NOW = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_bundle(
    context: RunEvidenceContext,
    *,
    suffix: str = "one",
    ttl: timedelta = timedelta(hours=1),
    now: datetime = NOW,
) -> RunEvidenceBundle:
    source = Source(
        scope_id=context.scope_id,
        kind=SourceKind.WEB,
        canonical_uri=f"https://example.test/{suffix}",
        public_display_uri=f"https://example.test/{suffix}",
        display_name=f"Web source {suffix}",
        created_at=now,
    )
    document = Document(
        scope_id=context.scope_id,
        source_id=source.source_id,
        logical_key=suffix,
        title=f"Document {suffix}",
        media_type="text/html",
        created_at=now,
    )
    digest = hashlib.sha256(f"content-{suffix}".encode()).hexdigest()
    version = DocumentVersion(
        scope_id=context.scope_id,
        document_id=document.document_id,
        blob_id=f"blob_{digest}",
        content_sha256=digest,
        version_number=1,
        retrieved_at=now,
        lifecycle_status=VersionLifecycleStatus.CANDIDATE,
        created_at=now,
    )
    chunk = Chunk(
        scope_id=context.scope_id,
        version_id=version.version_id,
        ordinal=0,
        text=f"direct evidence {suffix}",
        locator_type=ChunkLocatorType.ANCHOR,
        anchor=f"result-{suffix}",
        created_at=now,
    )
    evidence = Evidence(
        scope_id=context.scope_id,
        chunk_id=chunk.chunk_id,
        excerpt=chunk.text,
        confidence=0.8,
        directness=EvidenceDirectness.DIRECT,
        retrieval_method="fake-governed-web",
        validation_status=EvidenceValidationStatus.PENDING,
        created_at=now,
    )
    return RunEvidenceBundle.create(
        context=context,
        source=source,
        document=document,
        version=version,
        chunk=chunk,
        evidence=evidence,
        ttl=ttl,
        now=now,
    )


def context(scope: str = "scope-test", run: str = "run-one") -> RunEvidenceContext:
    return RunEvidenceContext(scope_id=scope, run_id=run)


def store_factories(tmp_path, clock):
    return (
        lambda: InMemoryRunEvidenceStore(clock=clock),
        lambda: SQLiteRunEvidenceStore(tmp_path / "run-evidence.db", clock=clock),
    )


@async_test
async def test_memory_and_sqlite_share_idempotent_resolver_contract(tmp_path) -> None:
    clock = Clock()
    for index, factory in enumerate(store_factories(tmp_path, clock)):
        store = factory()
        assert isinstance(store, RunEvidenceStore)
        ctx = context(run=f"run-{index}")
        bundle = make_bundle(ctx)

        assert await store.put(ctx, bundle) == bundle
        assert await store.put(ctx, bundle) == bundle
        assert await store.list(ctx) == [bundle]
        for stable_id in bundle.stable_ids():
            assert (await store.resolve(ctx, stable_id)).evidence_id == bundle.evidence_id

        updated = await store.compare_and_set_validation(
            ctx,
            bundle.evidence_id,
            expected=RunEvidenceValidationStatus.PENDING,
            status=RunEvidenceValidationStatus.VALIDATED_FOR_RUN,
            reason="direct and current",
            actor="candidate-policy-v1",
        )
        assert updated.validation_status is RunEvidenceValidationStatus.VALIDATED_FOR_RUN
        assert updated.version.lifecycle_status is VersionLifecycleStatus.CANDIDATE
        assert updated.evidence.validation_status is EvidenceValidationStatus.PENDING
        assert await store.list(
            ctx, validation_status=RunEvidenceValidationStatus.VALIDATED_FOR_RUN
        ) == [updated]
        with pytest.raises(RunEvidenceConflictError):
            await store.compare_and_set_validation(
                ctx,
                bundle.evidence_id,
                expected=RunEvidenceValidationStatus.PENDING,
                status=RunEvidenceValidationStatus.REJECTED,
                reason="late result",
                actor="candidate-policy-v1",
            )


@async_test
async def test_stores_reject_cross_run_and_cross_scope_access(tmp_path) -> None:
    for factory in store_factories(tmp_path, Clock()):
        store = factory()
        owner = context()
        bundle = make_bundle(owner)
        await store.put(owner, bundle)

        for outsider in (context(run="run-two"), context(scope="scope-other")):
            with pytest.raises(RunEvidenceNotFoundError):
                await store.resolve(outsider, bundle.evidence_id)
            assert await store.list(outsider) == []
            with pytest.raises(RunEvidenceNotFoundError):
                await store.put(outsider, bundle)


@async_test
async def test_ttl_cleanup_is_maintenance_driven_and_audited(tmp_path) -> None:
    for index, factory in enumerate(store_factories(tmp_path, Clock())):
        clock = Clock()
        store = (
            InMemoryRunEvidenceStore(clock=clock)
            if index == 0
            else SQLiteRunEvidenceStore(
                tmp_path / "ttl-run-evidence.db", clock=clock
            )
        )
        ctx = context(run=f"ttl-{index}")
        bundle = make_bundle(ctx, ttl=timedelta(minutes=5))
        await store.put(ctx, bundle)
        clock.now = NOW + timedelta(minutes=6)

        with pytest.raises(RunEvidenceNotFoundError):
            await store.resolve(ctx, bundle.evidence_id)
        assert await store.list_cleanup_audit(ctx) == []
        audits = await store.cleanup_expired()
        assert len(audits) == 1
        assert audits[0].evidence_id == bundle.evidence_id
        assert audits[0].reason == "ttl_expired"
        assert await store.cleanup_expired() == []
        assert await store.list_cleanup_audit(ctx) == audits

        if isinstance(store, SQLiteRunEvidenceStore):
            reopened = SQLiteRunEvidenceStore(store.database_path, clock=clock)
            assert await reopened.list_cleanup_audit(ctx) == audits


@async_test
async def test_sqlite_reopens_and_concurrent_put_deduplicates(tmp_path) -> None:
    database = tmp_path / "persistent-run-evidence.db"
    ctx = context()
    bundle = make_bundle(ctx)
    store = SQLiteRunEvidenceStore(database, clock=Clock())

    results = await asyncio.gather(*(store.put(ctx, bundle) for _ in range(12)))
    assert results == [bundle] * 12
    reopened = SQLiteRunEvidenceStore(database, clock=Clock())
    assert await reopened.list(ctx) == [bundle]

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"run_evidence_bundles", "run_evidence_cleanup_audit"}


def test_bundle_rejects_active_or_canonical_validated_snapshots() -> None:
    ctx = context()
    bundle = make_bundle(ctx)
    with pytest.raises(ValidationError, match="must remain candidate"):
        RunEvidenceBundle(
            **bundle.model_dump(
                exclude={"version", "validation_status", "validation_reason", "validation_actor"}
            ),
            version=bundle.version.model_copy(
                update={"lifecycle_status": VersionLifecycleStatus.ACTIVE}
            ),
        )
    with pytest.raises(ValidationError, match="must remain pending"):
        RunEvidenceBundle(
            **bundle.model_dump(
                exclude={"evidence", "validation_status", "validation_reason", "validation_actor"}
            ),
            evidence=bundle.evidence.model_copy(
                update={"validation_status": EvidenceValidationStatus.VALIDATED}
            ),
        )


def test_bundle_rejects_non_positive_ttl_and_naive_time() -> None:
    ctx = context()
    bundle = make_bundle(ctx)
    with pytest.raises(ValueError, match="ttl must be positive"):
        RunEvidenceBundle.create(
            context=ctx,
            source=bundle.source,
            document=bundle.document,
            version=bundle.version,
            chunk=bundle.chunk,
            evidence=bundle.evidence,
            ttl=timedelta(0),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        RunEvidenceBundle.create(
            context=ctx,
            source=bundle.source,
            document=bundle.document,
            version=bundle.version,
            chunk=bundle.chunk,
            evidence=bundle.evidence,
            ttl=timedelta(minutes=1),
            now=datetime(2026, 1, 1),
        )
