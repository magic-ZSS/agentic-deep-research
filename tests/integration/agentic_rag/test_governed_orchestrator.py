from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from functools import wraps

import pytest

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.evidence.run_store import (
    InMemoryRunEvidenceStore,
    RunEvidenceContext,
    RunEvidenceNotFoundError,
    RunEvidenceValidationStatus,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.models import (
    AuthorityClass,
    ChunkInput,
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.retrieval.budget import (
    RunBudgetLimits,
    RunBudgetRegistry,
)
from open_deep_research.knowledge.retrieval.orchestrator import (
    GovernedRetrievalOrchestrator,
    GovernedRetrievalRequest,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryKnowledgeRetriever,
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.retrieval.web import (
    StructuredWebResult,
    WebSearchRuntime,
)
from open_deep_research.knowledge.validation import CandidateValidationPolicy
from open_deep_research.research import (
    CompletionDecision,
    CoveragePolicy,
    RequirementDraft,
    RequirementMaterializer,
    ResearchCompletionGate,
)
from open_deep_research.storage.blob_repository import InMemoryBlobRepository


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)
BRIEF = "alpha policy evidence"
GOOD_CONTENT = (
    "Alpha policy evidence directly confirms the required alpha policy "
    "mechanism with an authoritative primary explanation."
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class FakeWebProvider:
    name = "fake-governed-web"

    def __init__(
        self,
        results=(),
        *,
        error: Exception | None = None,
        yield_once: bool = False,
    ) -> None:
        self.results = tuple(results)
        self.error = error
        self.yield_once = yield_once
        self.calls: list[tuple[tuple[str, ...], WebSearchRuntime]] = []

    async def search(self, queries, *, runtime, config=None):
        del config
        self.calls.append((queries, runtime))
        if self.yield_once:
            await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.results


class FailingBlobRepository(InMemoryBlobRepository):
    async def put(self, access, scope, content, media_type):
        del access, scope, content, media_type
        raise RuntimeError("fixture writeback failed")


def identity():
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    access = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id="project",
        auth_source="test",
        request_id="phase3",
    )
    return scope, access


async def requirement_set(scope, run_id: str):
    return await RequirementMaterializer().materialize(
        research_brief=BRIEF,
        scope_id=scope.scope_id,
        run_id=run_id,
        created_at=NOW,
    )


def web_result(
    *,
    suffix: str = "good",
    authority: AuthorityClass = AuthorityClass.SECONDARY,
    content: str = GOOD_CONTENT,
):
    return StructuredWebResult(
        query=BRIEF,
        url=f"https://example.test/{suffix}",
        title=f"Evidence {suffix}",
        content=content,
        score=0.9,
        retrieved_at=NOW + timedelta(seconds=1),
        authority_class=authority,
    )


def orchestrator(
    repository,
    blobs,
    provider,
    run_store,
    *,
    writeback: bool = False,
):
    return GovernedRetrievalOrchestrator(
        repository=repository,
        blob_repository=blobs,
        retriever=RepositoryKnowledgeRetriever(
            RepositoryRetrievalCatalog(repository)
        ),
        run_store=run_store,
        budget_registry=RunBudgetRegistry(),
        budget_limits=RunBudgetLimits(5, 5, 15, 2),
        validation_policy=CandidateValidationPolicy(
            policy_version="candidate-v1",
            min_content_chars=20,
            min_confidence=0.5,
            min_source_authority=AuthorityClass.SECONDARY,
        ),
        coverage_policy=CoveragePolicy(
            policy_version="coverage-v1",
            min_confidence=0.5,
            min_direct_evidence=1,
            accepted_authorities=(
                AuthorityClass.SECONDARY,
                AuthorityClass.PRIMARY,
                AuthorityClass.OFFICIAL,
            ),
        ),
        completion_gate=ResearchCompletionGate(policy_version="completion-v1"),
        web_provider=provider,
        writeback=writeback,
        run_evidence_ttl=timedelta(hours=1),
        max_web_results_per_query=3,
    )


async def seed_record(
    repository,
    blobs,
    access,
    scope,
    plan,
    *,
    status: VersionLifecycleStatus,
    validation_status: EvidenceValidationStatus,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    directness: EvidenceDirectness = EvidenceDirectness.DIRECT,
    confidence: float = 0.9,
    requirement_index: int = 0,
):
    source = await repository.upsert_source(
        access,
        scope,
        kind=SourceKind.WEB,
        canonical_uri="https://example.test/local",
        public_display_uri="https://example.test/local",
        display_name="Local evidence",
        authority_class=AuthorityClass.SECONDARY,
    )
    document = await repository.upsert_document(
        access,
        scope,
        source_id=source.source_id,
        logical_key="local",
        title="Local evidence",
        media_type="text/plain",
    )
    blob = await blobs.put(access, scope, GOOD_CONTENT.encode(), "text/plain")
    version = await repository.add_version(
        access,
        scope,
        document_id=document.document_id,
        blob=blob,
        retrieved_at=NOW,
        lifecycle_status=status,
    )
    chunk = (
        await repository.add_chunks(
            access,
            scope,
            version.version_id,
            [ChunkInput(ordinal=0, text=GOOD_CONTENT)],
        )
    )[0]
    requirement = plan.requirements[requirement_index]
    await repository.add_requirement(
        access,
        scope,
        run_id=plan.run_id,
        text=requirement.text,
        acceptance_hint=requirement.acceptance_hint,
        priority=requirement.priority,
    )
    evidence = await repository.add_evidence(
        access,
        scope,
        chunk_id=chunk.chunk_id,
        requirement_id=requirement.requirement_id,
        excerpt=GOOD_CONTENT,
        relation=relation,
        directness=directness,
        confidence=confidence,
        retrieval_method="fixture",
        validation_status=validation_status,
    )
    return source, document, version, chunk, evidence


async def retrieve(
    service,
    scope,
    access,
    plan,
    *,
    researcher="researcher",
    as_of: datetime = NOW,
):
    return await service.retrieve(
        GovernedRetrievalRequest(
            run_id=plan.run_id,
            researcher_id=researcher,
            query=BRIEF,
            requirement_ids=plan.requirement_ids,
            as_of=as_of,
        ),
        requirement_set=plan,
        access=access,
        scope=scope,
    )


@async_test
async def test_sufficient_active_local_evidence_strictly_skips_web() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-local")
    repository = InMemoryRepository()
    blobs = InMemoryBlobRepository()
    await seed_record(
        repository,
        blobs,
        access,
        scope,
        plan,
        status=VersionLifecycleStatus.ACTIVE,
        validation_status=EvidenceValidationStatus.VALIDATED,
    )
    provider = FakeWebProvider((web_result(),))
    result = await retrieve(
        orchestrator(
            repository,
            blobs,
            provider,
            InMemoryRunEvidenceStore(),
        ),
        scope,
        access,
        plan,
    )

    assert provider.calls == []
    assert result.web_call_count == 0
    assert result.coverage.required_complete
    assert len(result.evidence) == 1


@async_test
async def test_only_missing_requirement_drives_bounded_web_query() -> None:
    class TwoRequirementExtractor:
        def extract(self, **_kwargs):
            return (
                RequirementDraft(text="alpha policy evidence", required=True),
                RequirementDraft(text="beta deployment proof", required=True),
            )

    scope, access = identity()
    plan = await RequirementMaterializer(
        extractor=TwoRequirementExtractor(), extractor_version="fixture-v1"
    ).materialize(
        research_brief=BRIEF,
        scope_id=scope.scope_id,
        run_id="run-two-requirements",
        created_at=NOW,
    )
    repository = InMemoryRepository()
    blobs = InMemoryBlobRepository()
    await seed_record(
        repository,
        blobs,
        access,
        scope,
        plan,
        status=VersionLifecycleStatus.ACTIVE,
        validation_status=EvidenceValidationStatus.VALIDATED,
        requirement_index=0,
    )
    provider = FakeWebProvider(
        (
            web_result(
                suffix="beta",
                content=(
                    "Beta deployment proof directly confirms the required beta "
                    "deployment proof with authoritative implementation detail."
                ),
            ),
        )
    )
    result = await retrieve(
        orchestrator(
            repository,
            blobs,
            provider,
            InMemoryRunEvidenceStore(),
        ),
        scope,
        access,
        plan,
    )

    assert result.coverage.required_complete
    assert result.web_call_count == 1
    assert len(provider.calls) == 1
    assert len(provider.calls[0][0]) == 1
    query = provider.calls[0][0][0]
    assert "beta deployment proof" in query
    assert "alpha policy evidence\nEvidence objective" not in query


@async_test
async def test_writeback_off_keeps_validated_candidate_run_scoped_only() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-transient")
    repository = InMemoryRepository()
    run_store = InMemoryRunEvidenceStore()
    provider = FakeWebProvider((web_result(),))
    result = await retrieve(
        orchestrator(
            repository,
            InMemoryBlobRepository(),
            provider,
            run_store,
            writeback=False,
        ),
        scope,
        access,
        plan,
    )

    assert result.web_call_count == 1
    assert result.coverage.required_complete
    assert len(result.run_evidence_ids) == 1
    assert result.evidence[0].run_scoped
    assert await repository.list_sources(access, scope) == []
    context = RunEvidenceContext(scope_id=scope.scope_id, run_id=plan.run_id)
    bundle = await run_store.resolve(context, result.run_evidence_ids[0])
    assert bundle.validation_status is RunEvidenceValidationStatus.VALIDATED_FOR_RUN
    assert bundle.version.lifecycle_status is VersionLifecycleStatus.CANDIDATE
    with pytest.raises(RunEvidenceNotFoundError):
        await run_store.resolve(
            RunEvidenceContext(scope_id=scope.scope_id, run_id="another-run"),
            bundle.evidence_id,
        )


@async_test
async def test_low_authority_web_candidate_is_quarantined_not_returned() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-quarantine")
    repository = InMemoryRepository()
    run_store = InMemoryRunEvidenceStore()
    provider = FakeWebProvider(
        (web_result(authority=AuthorityClass.SELF_REPORTED),)
    )
    result = await retrieve(
        orchestrator(
            repository,
            InMemoryBlobRepository(),
            provider,
            run_store,
            writeback=True,
        ),
        scope,
        access,
        plan,
    )

    assert result.evidence == ()
    assert len(result.quarantined_version_ids) == 1
    version = await repository.get_version(
        access, scope, result.quarantined_version_ids[0]
    )
    assert version.lifecycle_status is VersionLifecycleStatus.QUARANTINED
    stored = await run_store.resolve(
        RunEvidenceContext(scope_id=scope.scope_id, run_id=plan.run_id),
        result.run_evidence_ids[0],
    )
    assert stored.validation_status is RunEvidenceValidationStatus.REJECTED


@async_test
async def test_local_candidate_uses_same_gate_then_avoids_web() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-local-candidate")
    repository = InMemoryRepository()
    blobs = InMemoryBlobRepository()
    _, _, version, _, _ = await seed_record(
        repository,
        blobs,
        access,
        scope,
        plan,
        status=VersionLifecycleStatus.CANDIDATE,
        validation_status=EvidenceValidationStatus.PENDING,
        relation=EvidenceRelation.CONTEXT,
        directness=EvidenceDirectness.UNKNOWN,
        confidence=0.4,
    )
    provider = FakeWebProvider((web_result(),))
    result = await retrieve(
        orchestrator(
            repository,
            blobs,
            provider,
            InMemoryRunEvidenceStore(),
        ),
        scope,
        access,
        plan,
    )

    assert result.local_candidate_count == 1
    assert result.web_call_count == 0
    assert provider.calls == []
    assert result.coverage.required_complete
    assert (
        await repository.get_version(access, scope, version.version_id)
    ).lifecycle_status is VersionLifecycleStatus.ACTIVE


@async_test
async def test_writeback_allows_next_independent_run_to_skip_web() -> None:
    scope, access = identity()
    repository = InMemoryRepository()
    blobs = InMemoryBlobRepository()
    first_plan = await requirement_set(scope, "run-first")
    first_provider = FakeWebProvider((web_result(),))
    first = await retrieve(
        orchestrator(
            repository,
            blobs,
            first_provider,
            InMemoryRunEvidenceStore(),
            writeback=True,
        ),
        scope,
        access,
        first_plan,
    )
    assert first.web_call_count == 1
    assert len(first.promoted_version_ids) == 1

    second_plan = await requirement_set(scope, "run-second")
    second_provider = FakeWebProvider((web_result(suffix="unused"),))
    second = await retrieve(
        orchestrator(
            repository,
            blobs,
            second_provider,
            InMemoryRunEvidenceStore(),
            writeback=True,
        ),
        scope,
        access,
        second_plan,
        as_of=NOW + timedelta(seconds=2),
    )
    assert second.coverage.required_complete
    assert second.web_call_count == 0
    assert second_provider.calls == []
    assert len(await repository.list_sources(access, scope)) == 1
    assert len(await repository.list_versions_for_scope(access, scope)) == 1


@async_test
async def test_provider_failure_is_counted_and_never_fabricates_evidence() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-provider-failure")
    repository = InMemoryRepository()
    provider = FakeWebProvider(error=RuntimeError("offline fixture"))
    result = await retrieve(
        orchestrator(
            repository,
            InMemoryBlobRepository(),
            provider,
            InMemoryRunEvidenceStore(),
        ),
        scope,
        access,
        plan,
    )

    assert result.web_call_count == 1
    assert result.evidence == ()
    assert result.run_evidence_ids == ()
    assert result.warnings == ("web_provider:RuntimeError",)
    assert await repository.list_versions_for_scope(access, scope) == []


@async_test
async def test_writeback_failure_retains_run_validated_evidence() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-writeback-failure")
    repository = InMemoryRepository()
    run_store = InMemoryRunEvidenceStore()
    result = await retrieve(
        orchestrator(
            repository,
            FailingBlobRepository(),
            FakeWebProvider((web_result(),)),
            run_store,
            writeback=True,
        ),
        scope,
        access,
        plan,
    )

    assert result.coverage.required_complete
    assert result.evidence[0].run_scoped
    assert "writeback:RuntimeError" in result.warnings
    assert result.promoted_version_ids == ()
    assert await repository.list_versions_for_scope(access, scope) == []
    assert (
        await run_store.resolve(
            RunEvidenceContext(scope_id=scope.scope_id, run_id=plan.run_id),
            result.run_evidence_ids[0],
        )
    ).validation_status is RunEvidenceValidationStatus.VALIDATED_FOR_RUN


@async_test
async def test_one_bad_candidate_does_not_discard_valid_provider_result() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-partial-result")
    repository = InMemoryRepository()
    provider = FakeWebProvider((object(), web_result()))
    result = await retrieve(
        orchestrator(
            repository,
            InMemoryBlobRepository(),
            provider,
            InMemoryRunEvidenceStore(),
        ),
        scope,
        access,
        plan,
    )

    assert result.web_call_count == 1
    assert result.coverage.required_complete
    assert len(result.evidence) == 1
    assert "candidate:AttributeError" in result.warnings


@async_test
async def test_parallel_same_candidate_has_stable_ids_and_one_canonical_version() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-parallel")
    repository = InMemoryRepository()
    blobs = InMemoryBlobRepository()
    run_store = InMemoryRunEvidenceStore()
    provider = FakeWebProvider((web_result(),), yield_once=True)
    service = orchestrator(
        repository,
        blobs,
        provider,
        run_store,
        writeback=True,
    )

    async def invoke(researcher: str, objective: str):
        return await service.retrieve(
            GovernedRetrievalRequest(
                run_id=plan.run_id,
                researcher_id=researcher,
                query=objective,
                requirement_ids=plan.requirement_ids,
                as_of=NOW,
            ),
            requirement_set=plan,
            access=access,
            scope=scope,
        )

    first, second = await asyncio.gather(
        invoke("researcher-a", "alpha mechanism"),
        invoke("researcher-b", "alpha implementation"),
    )
    assert len(provider.calls) == 2
    assert len(await repository.list_sources(access, scope)) == 1
    assert len(await repository.list_versions_for_scope(access, scope)) == 1
    bundles = await run_store.list(
        RunEvidenceContext(scope_id=scope.scope_id, run_id=plan.run_id)
    )
    assert len(bundles) == 1
    assert bundles[0].validation_status is RunEvidenceValidationStatus.VALIDATED_FOR_RUN
    assert first.evidence[0].evidence_id == second.evidence[0].evidence_id
    assert first.evidence[0].source_id == second.evidence[0].source_id


@async_test
async def test_missing_provider_blocks_completion_with_explicit_reason() -> None:
    scope, access = identity()
    plan = await requirement_set(scope, "run-no-provider")
    service = orchestrator(
        InMemoryRepository(),
        InMemoryBlobRepository(),
        None,
        InMemoryRunEvidenceStore(),
    )
    result = await retrieve(service, scope, access, plan)
    decision = await service.completion_decision(plan, as_of=NOW)

    assert result.warnings == ("web_provider_unavailable",)
    assert decision.decision is CompletionDecision.BLOCKED
    assert decision.reasons == ("web_provider_unavailable",)
    assert decision.explicit_gaps
