"""Local-first evidence-governed retrieval orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.evidence.run_store import (
    RunEvidenceBundle,
    RunEvidenceContext,
    RunEvidenceStore,
    RunEvidenceValidationStatus,
)
from open_deep_research.knowledge.ids import stable_id
from open_deep_research.knowledge.lifecycle.service import KnowledgeLifecycleService
from open_deep_research.knowledge.models import (
    AuthorityClass,
    Chunk,
    ChunkInput,
    ContentBlob,
    Document,
    DocumentVersion,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    VersionLifecycleStatus,
)
from open_deep_research.knowledge.repositories import (
    BlobRepository,
    KnowledgeEvidenceRepository,
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from open_deep_research.knowledge.retrieval.budget import (
    DuplicateRunQueryError,
    RunBudgetExceededError,
    RunBudgetLimits,
    RunBudgetRegistry,
)
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeSearchRequest,
    RetrievalFilters,
    RetrievalRecord,
)
from open_deep_research.knowledge.retrieval.protocols import KnowledgeRetriever
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.retrieval.web import (
    StructuredWebResult,
    WebSearchProvider,
    WebSearchRuntime,
    require_web_provider,
)
from open_deep_research.knowledge.validation import CandidateValidationPolicy
from open_deep_research.research import (
    CoveragePolicy,
    CoverageReport,
    GovernedEvidenceRef,
    RequirementSet,
    ResearchBudgetSnapshot,
    ResearchCompletionDecision,
    ResearchCompletionGate,
)


class GovernedRetrievalRequest(BaseModel):
    """Trusted request assembled by the governed tool façade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    researcher_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=4000)
    requirement_ids: tuple[str, ...] = ()
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    local_limit: int = Field(default=8, ge=1, le=50)

    @model_validator(mode="after")
    def normalize(self) -> "GovernedRetrievalRequest":
        query = self.query.strip()
        if not query:
            raise ValueError("query cannot be blank")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))
        object.__setattr__(self, "requirement_ids", tuple(sorted(set(self.requirement_ids))))
        return self


class GovernedEvidenceSummary(BaseModel):
    """Safe, compact structured evidence handed to a Researcher."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    chunk_id: str
    version_id: str
    source_id: str
    requirement_id: str
    title: str
    source_uri: str | None
    excerpt: str
    locator: dict[str, Any]
    run_scoped: bool = False


class GovernedRetrievalResult(BaseModel):
    """Observable result of one local-first retrieval decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = ""
    run_id: str
    researcher_id: str
    query: str
    evidence: tuple[GovernedEvidenceSummary, ...]
    coverage: CoverageReport
    web_call_count: int = Field(ge=0)
    local_candidate_count: int = Field(ge=0)
    promoted_version_ids: tuple[str, ...] = ()
    quarantined_version_ids: tuple[str, ...] = ()
    run_evidence_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def model_post_init(self, _context: object) -> None:
        expected = stable_id(
            "retrieval_decision",
            self.run_id,
            self.researcher_id,
            self.query,
            tuple(item.evidence_id for item in self.evidence),
            self.coverage.plan_id,
            tuple(
                (item.requirement_id, item.status.value, item.evidence_ids)
                for item in self.coverage.assessments
            ),
            self.web_call_count,
            self.local_candidate_count,
            self.promoted_version_ids,
            self.quarantined_version_ids,
            self.run_evidence_ids,
            self.warnings,
        )
        if self.decision_id and self.decision_id != expected:
            raise ValueError("decision_id does not match governed retrieval result")
        object.__setattr__(self, "decision_id", expected)

    @property
    def coverage_assessment_ids(self) -> tuple[str, ...]:
        return tuple(
            stable_id(
                "coverage",
                self.coverage.plan_id,
                item.requirement_id,
                item.status.value,
                item.evidence_ids,
                item.missing_aspects,
                item.policy_version,
            )
            for item in self.coverage.assessments
        )

    def tool_content(self) -> str:
        """Return JSON so graph recovery can reconstruct IDs without free text parsing."""
        return self.model_dump_json()


class GovernedRetrievalOrchestrator:
    """Enforce local active -> local candidate gate -> budgeted Web ordering."""

    def __init__(
        self,
        *,
        repository: KnowledgeEvidenceRepository,
        blob_repository: BlobRepository,
        retriever: KnowledgeRetriever,
        run_store: RunEvidenceStore,
        budget_registry: RunBudgetRegistry,
        budget_limits: RunBudgetLimits,
        validation_policy: CandidateValidationPolicy,
        coverage_policy: CoveragePolicy,
        completion_gate: ResearchCompletionGate,
        lifecycle_service: KnowledgeLifecycleService | None = None,
        web_provider: WebSearchProvider | None = None,
        writeback: bool = False,
        run_evidence_ttl: timedelta = timedelta(days=1),
        max_web_results_per_query: int = 3,
    ) -> None:
        self.repository = repository
        self.blob_repository = blob_repository
        self.retriever = retriever
        self.catalog = RepositoryRetrievalCatalog(repository)
        self.run_store = run_store
        self.budget_registry = budget_registry
        self.budget_limits = budget_limits
        self.validation_policy = validation_policy
        self.coverage_policy = coverage_policy
        self.completion_gate = completion_gate
        self.lifecycle = lifecycle_service or KnowledgeLifecycleService(repository)
        self.web_provider = (
            require_web_provider(web_provider) if web_provider is not None else None
        )
        self.writeback = writeback
        self.run_evidence_ttl = run_evidence_ttl
        self.max_web_results_per_query = max_web_results_per_query
        self._refs: dict[str, dict[tuple[str, str], GovernedEvidenceRef]] = {}
        self._lock = asyncio.Lock()

    async def retrieve(
        self,
        request: GovernedRetrievalRequest,
        *,
        requirement_set: RequirementSet,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        config: RunnableConfig | None = None,
    ) -> GovernedRetrievalResult:
        self._validate_context(request, requirement_set, scope)
        selected = tuple(
            item
            for item in requirement_set.requirements
            if not request.requirement_ids or item.requirement_id in request.requirement_ids
        )
        if not selected:
            selected = requirement_set.requirements
        await self._ensure_budget(request.run_id)
        refs = await self._load_active_refs(
            selected, request=request, access=access, scope=scope
        )
        await self._remember_refs(request.run_id, refs)
        coverage = await self.coverage(requirement_set, as_of=request.as_of)
        promoted: set[str] = set()
        quarantined: set[str] = set()
        local_candidate_count = 0
        warnings: list[str] = []

        missing_ids = set(coverage.required_gap_ids)
        if missing_ids:
            candidate_refs, promoted_ids, quarantined_ids, inspected = (
                await self._inspect_local_candidates(
                    tuple(item for item in selected if item.requirement_id in missing_ids),
                    request=request,
                    access=access,
                    scope=scope,
                )
            )
            local_candidate_count += inspected
            promoted.update(promoted_ids)
            quarantined.update(quarantined_ids)
            await self._remember_refs(request.run_id, candidate_refs)
            coverage = await self.coverage(requirement_set, as_of=request.as_of)

        web_call_count = 0
        run_evidence_ids: set[str] = set()
        missing_ids = set(coverage.required_gap_ids)
        if missing_ids and self.web_provider is not None:
            for requirement in selected:
                if requirement.requirement_id not in missing_ids:
                    continue
                try:
                    (
                        web_refs,
                        transient_ids,
                        promoted_ids,
                        quarantined_ids,
                        web_warnings,
                        call_count,
                    ) = (
                        await self._retrieve_web_for_requirement(
                            requirement=requirement,
                            request=request,
                            access=access,
                            scope=scope,
                            config=config,
                        )
                    )
                except (RunBudgetExceededError, DuplicateRunQueryError) as exc:
                    warnings.append(f"web_budget:{type(exc).__name__}")
                    continue
                except Exception as exc:
                    warnings.append(f"web_provider:{type(exc).__name__}")
                    continue
                run_evidence_ids.update(transient_ids)
                promoted.update(promoted_ids)
                quarantined.update(quarantined_ids)
                warnings.extend(web_warnings)
                web_call_count += call_count
                await self._remember_refs(request.run_id, web_refs)
            coverage = await self.coverage(requirement_set, as_of=request.as_of)
        elif missing_ids:
            warnings.append("web_provider_unavailable")

        remembered = await self._refs_for_run(request.run_id)
        summaries = self._summaries(remembered, selected)
        return GovernedRetrievalResult(
            run_id=request.run_id,
            researcher_id=request.researcher_id,
            query=request.query,
            evidence=summaries,
            coverage=coverage,
            web_call_count=web_call_count,
            local_candidate_count=local_candidate_count,
            promoted_version_ids=tuple(sorted(promoted)),
            quarantined_version_ids=tuple(sorted(quarantined)),
            run_evidence_ids=tuple(sorted(run_evidence_ids)),
            warnings=tuple(sorted(set(warnings))),
        )

    async def coverage(
        self, requirement_set: RequirementSet, *, as_of: datetime | None = None
    ) -> CoverageReport:
        refs = await self._refs_for_run(requirement_set.run_id)
        return self.coverage_policy.assess(
            requirement_set, tuple(refs), as_of=as_of
        )

    async def completion_decision(
        self,
        requirement_set: RequirementSet,
        *,
        as_of: datetime | None = None,
        blocked: bool = False,
        blocked_reasons: tuple[str, ...] = (),
    ) -> ResearchCompletionDecision:
        coverage = await self.coverage(requirement_set, as_of=as_of)
        budget = await self._ensure_budget(requirement_set.run_id)
        snapshot = await budget.snapshot()
        remaining = max(
            0,
            min(
                snapshot.remaining_queries,
                snapshot.remaining_tool_calls,
                snapshot.remaining_results,
            ),
        )
        provider_blocked = self.web_provider is None and bool(
            coverage.required_gap_ids
        )
        effective_blocked = blocked or provider_blocked
        effective_reasons = blocked_reasons
        if provider_blocked and not effective_reasons:
            effective_reasons = ("web_provider_unavailable",)
        return self.completion_gate.evaluate(
            requirement_set=requirement_set,
            coverage=coverage,
            budget=ResearchBudgetSnapshot(
                remaining_units=remaining,
                consumed_units=snapshot.queries_used,
            ),
            blocked=effective_blocked,
            blocked_reasons=effective_reasons,
        )

    async def _load_active_refs(self, requirements, *, request, access, scope):
        refs: list[GovernedEvidenceRef] = []
        for requirement in requirements:
            result = await self.retriever.search(
                KnowledgeSearchRequest(
                    query=requirement.text,
                    limit=request.local_limit,
                    as_of=request.as_of,
                    filters=RetrievalFilters(
                        lifecycle_statuses=(VersionLifecycleStatus.ACTIVE,),
                        validation_statuses=(EvidenceValidationStatus.VALIDATED,),
                    ),
                ),
                access=access,
                scope=scope,
            )
            for hit in result.hits:
                if not hit.evidence_id:
                    continue
                try:
                    record = await self.catalog.get_record(
                        access, scope, hit.evidence_id
                    )
                except (RepositoryNotFoundError, LookupError):
                    continue
                if record.evidence is None:
                    continue
                # A previously validated excerpt is not automatically direct for
                # a different run Requirement. Re-apply the same deterministic
                # relevance/authority/temporal gate without mutating canonical
                # lifecycle state before binding it to this requirement.
                decision = self.validation_policy.evaluate(
                    record,
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.text,
                    as_of=request.as_of,
                )
                if not decision.accepted:
                    continue
                refs.append(
                    GovernedEvidenceRef(
                        evidence=record.evidence,
                        chunk=record.chunk,
                        version=record.version,
                        document=record.document,
                        source=record.source,
                        bound_requirement_id=requirement.requirement_id,
                    )
                )
        return refs

    async def _inspect_local_candidates(self, requirements, *, request, access, scope):
        refs: list[GovernedEvidenceRef] = []
        promoted: set[str] = set()
        quarantined: set[str] = set()
        inspected = 0
        for requirement in requirements:
            result = await self.retriever.search(
                KnowledgeSearchRequest(
                    query=requirement.text,
                    limit=request.local_limit,
                    as_of=request.as_of,
                    include_candidate=True,
                    filters=RetrievalFilters(
                        lifecycle_statuses=(VersionLifecycleStatus.CANDIDATE,),
                    ),
                ),
                access=access,
                scope=scope,
            )
            for hit in result.hits:
                try:
                    record = await self.catalog.get_record(
                        access, scope, hit.evidence_id or hit.chunk_id
                    )
                except (RepositoryNotFoundError, LookupError):
                    continue
                if record.version.lifecycle_status is not VersionLifecycleStatus.CANDIDATE:
                    continue
                if record.evidence is None:
                    continue
                inspected += 1
                decision = self.validation_policy.evaluate(
                    record,
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.text,
                    as_of=request.as_of,
                )
                rule_results = tuple(
                    f"{rule.rule}:{'pass' if rule.passed else 'fail'}:{rule.detail}"
                    for rule in decision.rule_results
                )
                correlation = stable_id(
                    "candidate_gate",
                    request.run_id,
                    requirement.requirement_id,
                    record.version.version_id,
                )
                try:
                    updated_evidence = await self.lifecycle.validate_evidence(
                        access,
                        scope,
                        record.evidence.evidence_id,
                        expected_status=record.evidence.validation_status,
                        status=(
                            EvidenceValidationStatus.VALIDATED
                            if decision.accepted
                            else EvidenceValidationStatus.REJECTED
                        ),
                        relation=decision.proposed_relation,
                        directness=decision.proposed_directness,
                        confidence=decision.proposed_confidence,
                        valid_at=request.as_of,
                        actor_type="candidate_validation_policy",
                        reason=("candidate accepted" if decision.accepted else "candidate rejected"),
                        policy_version=decision.policy_version,
                        rule_results=rule_results,
                        run_id=request.run_id,
                        proposal_id=None,
                        correlation_id=correlation,
                    )
                    target = (
                        VersionLifecycleStatus.ACTIVE
                        if decision.accepted
                        else VersionLifecycleStatus.QUARANTINED
                    )
                    updated_version = await self.lifecycle.transition_version(
                        access,
                        scope,
                        record.version.version_id,
                        expected_status=VersionLifecycleStatus.CANDIDATE,
                        status=target,
                        actor_type="candidate_validation_policy",
                        reason=("validated evidence" if decision.accepted else "failed candidate validation"),
                        policy_version=decision.policy_version,
                        rule_results=rule_results,
                        run_id=request.run_id,
                        proposal_id=None,
                        correlation_id=correlation,
                    )
                except RepositoryConflictError:
                    updated_version = await self.repository.get_version(
                        access, scope, record.version.version_id
                    )
                    evidence_items = await self.repository.list_evidence_for_chunk(
                        access, scope, record.chunk.chunk_id
                    )
                    updated_evidence = next(
                        (
                            item
                            for item in evidence_items
                            if item.validation_status
                            is EvidenceValidationStatus.VALIDATED
                        ),
                        record.evidence,
                    )
                if updated_version.lifecycle_status is VersionLifecycleStatus.ACTIVE:
                    promoted.add(updated_version.version_id)
                    refs.append(
                        GovernedEvidenceRef(
                            evidence=updated_evidence,
                            chunk=record.chunk,
                            version=updated_version,
                            document=record.document,
                            source=record.source,
                            bound_requirement_id=requirement.requirement_id,
                        )
                    )
                elif updated_version.lifecycle_status is VersionLifecycleStatus.QUARANTINED:
                    quarantined.add(updated_version.version_id)
        return refs, promoted, quarantined, inspected

    async def _retrieve_web_for_requirement(
        self, *, requirement, request, access, scope, config
    ):
        assert self.web_provider is not None
        budget = await self._ensure_budget(request.run_id)
        query = self._gap_query(requirement, request.query)
        reservation = await budget.reserve(
            query, result_limit=self.max_web_results_per_query
        )
        failed = True
        provider_error: Exception | None = None
        results: tuple[StructuredWebResult, ...] = ()
        try:
            results = await self.web_provider.search(
                (query,),
                runtime=WebSearchRuntime(
                    scope_id=scope.scope_id,
                    run_id=request.run_id,
                    researcher_id=request.researcher_id,
                    max_results_per_query=self.max_web_results_per_query,
                ),
                config=config,
            )
            failed = False
        except Exception as exc:
            provider_error = exc
        finally:
            await budget.release(reservation, failed=failed)

        if provider_error is not None:
            return (
                [],
                set(),
                set(),
                set(),
                (f"web_provider:{type(provider_error).__name__}",),
                1,
            )

        refs: list[GovernedEvidenceRef] = []
        transient_ids: set[str] = set()
        promoted: set[str] = set()
        quarantined: set[str] = set()
        warnings: list[str] = []
        context = RunEvidenceContext(scope_id=scope.scope_id, run_id=request.run_id)
        for result in results[: self.max_web_results_per_query]:
            try:
                bundle = self._web_bundle(
                    result,
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.text,
                    context=context,
                )
                stored = await self.run_store.put(context, bundle)
                transient_ids.add(stored.evidence_id)
                record = RetrievalRecord(
                    source=stored.source,
                    document=stored.document,
                    version=stored.version,
                    chunk=stored.chunk,
                    evidence=stored.evidence,
                )
                decision = self.validation_policy.evaluate(
                    record,
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.text,
                    as_of=request.as_of,
                )
                terminal = (
                    RunEvidenceValidationStatus.VALIDATED_FOR_RUN
                    if decision.accepted
                    else RunEvidenceValidationStatus.REJECTED
                )
                if stored.validation_status is RunEvidenceValidationStatus.PENDING:
                    stored = await self.run_store.compare_and_set_validation(
                        context,
                        stored.evidence_id,
                        expected=RunEvidenceValidationStatus.PENDING,
                        status=terminal,
                        reason="; ".join(
                            f"{item.rule}={'pass' if item.passed else 'fail'}"
                            for item in decision.rule_results
                        ),
                        actor="candidate_validation_policy",
                    )
                elif stored.validation_status is not terminal:
                    warnings.append("candidate:RunEvidenceValidationConflict")
                    continue

                # A validated transient chain remains usable for this run even
                # if optional canonical writeback subsequently fails.
                if decision.accepted:
                    refs.append(
                        GovernedEvidenceRef(
                            evidence=stored.evidence,
                            chunk=stored.chunk,
                            version=stored.version,
                            document=stored.document,
                            source=stored.source,
                            bound_requirement_id=requirement.requirement_id,
                            run_id=request.run_id,
                            validated_for_run=True,
                        )
                    )
                if self.writeback:
                    try:
                        canonical, did_promote = await self._writeback_bundle(
                            stored,
                            decision=decision,
                            requirement=requirement,
                            access=access,
                            scope=scope,
                            run_id=request.run_id,
                        )
                    except Exception as exc:
                        warnings.append(f"writeback:{type(exc).__name__}")
                    else:
                        if did_promote:
                            promoted.add(canonical.version.version_id)
                            refs.append(
                                GovernedEvidenceRef(
                                    evidence=canonical.evidence,
                                    chunk=canonical.chunk,
                                    version=canonical.version,
                                    document=canonical.document,
                                    source=canonical.source,
                                    bound_requirement_id=requirement.requirement_id,
                                )
                            )
                        else:
                            quarantined.add(canonical.version.version_id)
            except Exception as exc:
                warnings.append(f"candidate:{type(exc).__name__}")
                continue
        return refs, transient_ids, promoted, quarantined, tuple(warnings), 1

    async def _writeback_bundle(
        self, bundle, *, decision, requirement, access, scope, run_id
    ):
        source = await self.repository.upsert_source(
            access,
            scope,
            kind=SourceKind.WEB,
            canonical_uri=bundle.source.canonical_uri,
            public_display_uri=bundle.source.public_display_uri,
            display_name=bundle.source.display_name,
            publisher=bundle.source.publisher,
            authority_class=bundle.source.authority_class,
            correlation_id=run_id,
        )
        document = await self.repository.upsert_document(
            access,
            scope,
            source_id=source.source_id,
            logical_key=bundle.document.logical_key,
            title=bundle.document.title,
            media_type=bundle.document.media_type,
            correlation_id=run_id,
        )
        raw = bundle.chunk.text.encode("utf-8")
        blob = await self.blob_repository.put(
            access, scope, raw, bundle.document.media_type
        )
        version = await self.repository.add_version(
            access,
            scope,
            document_id=document.document_id,
            blob=blob,
            retrieved_at=bundle.version.retrieved_at,
            published_at=bundle.version.published_at,
            valid_from=bundle.version.valid_from,
            valid_to=bundle.version.valid_to,
            metadata=bundle.version.metadata,
            lifecycle_status=VersionLifecycleStatus.CANDIDATE,
            correlation_id=run_id,
        )
        chunks = await self.repository.add_chunks(
            access,
            scope,
            version.version_id,
            [
                ChunkInput(
                    ordinal=bundle.chunk.ordinal,
                    text=bundle.chunk.text,
                    locator_type=bundle.chunk.locator_type,
                    page_start=bundle.chunk.page_start,
                    page_end=bundle.chunk.page_end,
                    heading_path=bundle.chunk.heading_path,
                    anchor=bundle.chunk.anchor,
                    metadata=bundle.chunk.metadata,
                )
            ],
            correlation_id=run_id,
        )
        await self.repository.add_requirement(
            access,
            scope,
            run_id=run_id,
            text=requirement.text,
            acceptance_hint=requirement.acceptance_hint,
            priority=requirement.priority,
            correlation_id=run_id,
        )
        evidence = await self.repository.add_evidence(
            access,
            scope,
            chunk_id=chunks[0].chunk_id,
            requirement_id=requirement.requirement_id,
            excerpt=bundle.evidence.excerpt,
            relation=bundle.evidence.relation,
            directness=bundle.evidence.directness,
            confidence=bundle.evidence.confidence,
            valid_at=bundle.evidence.valid_at,
            retrieval_method="governed-web",
            validation_status=EvidenceValidationStatus.PENDING,
            correlation_id=run_id,
        )
        record = RetrievalRecord(
            source=source,
            document=document,
            version=version,
            chunk=chunks[0],
            evidence=evidence,
        )
        target_evidence_status = (
            EvidenceValidationStatus.VALIDATED
            if decision.accepted
            else EvidenceValidationStatus.REJECTED
        )
        rules = tuple(
            f"{item.rule}:{'pass' if item.passed else 'fail'}:{item.detail}"
            for item in decision.rule_results
        )
        try:
            evidence = await self.lifecycle.validate_evidence(
                access,
                scope,
                evidence.evidence_id,
                expected_status=EvidenceValidationStatus.PENDING,
                status=target_evidence_status,
                relation=decision.proposed_relation,
                directness=decision.proposed_directness,
                confidence=decision.proposed_confidence,
                valid_at=decision.evaluated_at,
                actor_type="candidate_validation_policy",
                reason="Web candidate validation",
                policy_version=decision.policy_version,
                rule_results=rules,
                run_id=run_id,
                proposal_id=None,
                correlation_id=decision.decision_id,
            )
            version = await self.lifecycle.transition_version(
                access,
                scope,
                version.version_id,
                expected_status=VersionLifecycleStatus.CANDIDATE,
                status=(
                    VersionLifecycleStatus.ACTIVE
                    if decision.accepted
                    else VersionLifecycleStatus.QUARANTINED
                ),
                actor_type="candidate_validation_policy",
                reason="Web candidate validation",
                policy_version=decision.policy_version,
                rule_results=rules,
                run_id=run_id,
                proposal_id=None,
                correlation_id=decision.decision_id,
            )
        except RepositoryConflictError:
            version = await self.repository.get_version(
                access, scope, version.version_id
            )
            evidence_items = await self.repository.list_evidence_for_chunk(
                access, scope, chunks[0].chunk_id
            )
            evidence = next(
                (
                    item
                    for item in evidence_items
                    if item.requirement_id == requirement.requirement_id
                    and item.validation_status
                    in {
                        EvidenceValidationStatus.VALIDATED,
                        EvidenceValidationStatus.REJECTED,
                    }
                ),
                evidence,
            )
        return (
            RetrievalRecord(
                source=source,
                document=document,
                version=version,
                chunk=chunks[0],
                evidence=evidence,
            ),
            version.lifecycle_status is VersionLifecycleStatus.ACTIVE,
        )

    def _web_bundle(self, result, *, requirement_id, requirement_text, context):
        source = Source(
            scope_id=context.scope_id,
            kind=SourceKind.WEB,
            canonical_uri=result.url,
            public_display_uri=result.url,
            display_name=result.title,
            authority_class=result.authority_class,
        )
        document = Document(
            scope_id=context.scope_id,
            source_id=source.source_id,
            logical_key=result.url,
            title=result.title,
            media_type="text/html",
        )
        raw = result.content.encode("utf-8")
        blob = ContentBlob.from_bytes(
            scope_id=context.scope_id,
            content=raw,
            media_type="text/html",
            storage_ref=f"run/{context.run_id}/{result.result_id}.blob",
        )
        version = DocumentVersion(
            scope_id=context.scope_id,
            document_id=document.document_id,
            blob_id=blob.blob_id,
            content_sha256=blob.content_sha256,
            version_number=1,
            retrieved_at=result.retrieved_at,
            published_at=result.published_at,
            metadata={**result.metadata, "query": result.query},
            lifecycle_status=VersionLifecycleStatus.CANDIDATE,
        )
        chunk = Chunk(
            scope_id=context.scope_id,
            version_id=version.version_id,
            ordinal=0,
            text=result.content,
            metadata={"result_id": result.result_id},
        )
        overlap = self._term_overlap(requirement_text, result.content)
        evidence = Evidence(
            scope_id=context.scope_id,
            chunk_id=chunk.chunk_id,
            requirement_id=requirement_id,
            excerpt=result.content[:4000],
            relation=EvidenceRelation.SUPPORTS,
            directness=(
                EvidenceDirectness.DIRECT
                if overlap >= self.validation_policy.min_requirement_overlap
                else EvidenceDirectness.UNKNOWN
            ),
            confidence=max(result.score, overlap),
            valid_at=result.retrieved_at,
            retrieval_method="governed-web",
            validation_status=EvidenceValidationStatus.PENDING,
        )
        return RunEvidenceBundle.create(
            context=context,
            source=source,
            document=document,
            version=version,
            chunk=chunk,
            evidence=evidence,
            ttl=self.run_evidence_ttl,
            now=result.retrieved_at,
        )

    async def _ensure_budget(self, run_id):
        return await self.budget_registry.get_or_create(run_id, self.budget_limits)

    async def _remember_refs(self, run_id, refs):
        async with self._lock:
            bucket = self._refs.setdefault(run_id, {})
            for ref in refs:
                key = (
                    ref.bound_requirement_id or ref.evidence.requirement_id or "",
                    ref.evidence.evidence_id,
                )
                bucket[key] = ref

    async def _refs_for_run(self, run_id):
        async with self._lock:
            return tuple(
                self._refs.get(run_id, {})[key]
                for key in sorted(self._refs.get(run_id, {}))
            )

    @staticmethod
    def _validate_context(request, requirement_set, scope):
        if request.run_id != requirement_set.run_id:
            raise ValueError("retrieval request run does not match RequirementSet")
        if requirement_set.scope_id != scope.scope_id:
            raise ValueError("RequirementSet does not belong to trusted scope")
        if request.requirement_ids and not set(request.requirement_ids).issubset(
            requirement_set.requirement_ids
        ):
            raise ValueError("retrieval request contains unknown requirement IDs")

    @staticmethod
    def _gap_query(requirement, user_query):
        return f"{requirement.text}\nEvidence objective: {user_query}".strip()

    @staticmethod
    def _term_overlap(left, right):
        left_tokens = {item.casefold() for item in left.split() if item.strip()}
        right_tokens = {item.casefold() for item in right.split() if item.strip()}
        return len(left_tokens & right_tokens) / max(1, len(left_tokens))

    @staticmethod
    def _summaries(refs, requirements):
        selected_ids = {item.requirement_id for item in requirements}
        summaries: dict[tuple[str, str], GovernedEvidenceSummary] = {}
        for ref in refs:
            requirement_id = ref.bound_requirement_id or ref.evidence.requirement_id
            if not requirement_id or requirement_id not in selected_ids:
                continue
            key = (requirement_id, ref.evidence.evidence_id)
            summaries[key] = GovernedEvidenceSummary(
                evidence_id=ref.evidence.evidence_id,
                chunk_id=ref.chunk.chunk_id,
                version_id=ref.version.version_id,
                source_id=ref.source.source_id,
                requirement_id=requirement_id,
                title=ref.document.title,
                source_uri=ref.source.public_display_uri or ref.source.canonical_uri,
                excerpt=ref.evidence.excerpt,
                locator={
                    "type": ref.chunk.locator_type.value,
                    "page_start": ref.chunk.page_start,
                    "page_end": ref.chunk.page_end,
                    "heading_path": list(ref.chunk.heading_path),
                    "anchor": ref.chunk.anchor,
                },
                run_scoped=ref.validated_for_run,
            )
        return tuple(summaries[key] for key in sorted(summaries))
