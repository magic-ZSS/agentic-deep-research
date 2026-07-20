"""Trusted runtime assembly for the opt-in governed retrieval façade."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig

from open_deep_research.configuration import Configuration, SearchAPI
from open_deep_research.evidence.run_store import (
    InMemoryRunEvidenceStore,
    SQLiteRunEvidenceStore,
)
from open_deep_research.knowledge.in_memory_repository import InMemoryRepository
from open_deep_research.knowledge.lifecycle.service import KnowledgeLifecycleService
from open_deep_research.knowledge.models import (
    AuthorityClass,
    KnowledgeAccessContext,
    KnowledgeScope,
)
from open_deep_research.knowledge.paperqa_adapter import (
    DeterministicHashEmbedding,
    NativePaperQABackend,
    PaperQAKnowledgeRetriever,
    create_offline_paperqa_settings,
)
from open_deep_research.knowledge.retrieval.budget import (
    RunBudgetLimits,
    RunBudgetRegistry,
)
from open_deep_research.knowledge.retrieval.orchestrator import (
    GovernedRetrievalOrchestrator,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryKnowledgeRetriever,
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.retrieval.web import (
    TavilyStructuredWebSearchProvider,
    WebSearchProvider,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository
from open_deep_research.knowledge.validation import CandidateValidationPolicy
from open_deep_research.research import CoveragePolicy, ResearchCompletionGate
from open_deep_research.storage.blob_repository import (
    InMemoryBlobRepository,
    LocalBlobRepository,
)


class GovernedRuntimeConfigurationError(RuntimeError):
    """Trusted scope/run services cannot be assembled safely."""


@dataclass(frozen=True, slots=True)
class GovernedRuntime:
    """Run-scoped services shared by Supervisor and all Researchers."""

    run_id: str
    scope: KnowledgeScope
    access: KnowledgeAccessContext
    repository: Any
    blob_repository: Any
    retriever: Any
    orchestrator: GovernedRetrievalOrchestrator


@dataclass(frozen=True, slots=True)
class _CanonicalServices:
    """Scope-level canonical services reused by independent research runs."""

    repository: Any
    blob_repository: Any
    retriever: Any


_RUNTIME_CACHE: dict[tuple[Any, ...], GovernedRuntime] = {}
_CANONICAL_CACHE: dict[tuple[Any, ...], _CanonicalServices] = {}
_RUNTIME_LOCK = threading.RLock()


def process_context(config: RunnableConfig | None) -> dict[str, Any]:
    configurable = (config or {}).get("configurable", {})
    context = configurable.get("_process_context", {})
    return context if isinstance(context, dict) else {}


def injected_runtime(config: RunnableConfig | None) -> GovernedRuntime | None:
    """Return a test/deployment supplied trusted runtime, never a model argument."""
    configurable = (config or {}).get("configurable", {})
    candidate = configurable.get("_governed_runtime")
    if candidate is None:
        candidate = process_context(config).get("governed_runtime")
    if candidate is None:
        return None
    if not isinstance(candidate, GovernedRuntime):
        raise GovernedRuntimeConfigurationError(
            "injected governed runtime has an invalid type"
        )
    return candidate


def get_governed_runtime(
    config: RunnableConfig | None,
    *,
    run_id: str | None = None,
    web_provider: WebSearchProvider | None = None,
) -> GovernedRuntime:
    """Resolve or lazily construct one runtime for a trusted scope/run."""
    supplied = injected_runtime(config)
    if supplied is not None:
        if run_id is not None and supplied.run_id != run_id:
            raise GovernedRuntimeConfigurationError(
                "injected governed runtime belongs to another run"
            )
        return supplied
    configuration = Configuration.from_runnable_config(config)
    context = process_context(config)
    resolved_run_id = (
        run_id
        or context.get("run_id")
        or (config or {}).get("configurable", {}).get("research_run_id")
        or (config or {}).get("configurable", {}).get("thread_id")
    )
    if not isinstance(resolved_run_id, str) or not resolved_run_id.strip():
        raise GovernedRuntimeConfigurationError(
            "governed retrieval requires a trusted run_id/thread_id"
        )
    if not configuration.knowledge_tenant_id or not configuration.knowledge_project_id:
        raise GovernedRuntimeConfigurationError(
            "governed retrieval requires knowledge_tenant_id and knowledge_project_id"
        )
    key = (
        resolved_run_id.strip(),
        configuration.knowledge_tenant_id,
        configuration.knowledge_project_id,
        configuration.knowledge_repository_backend,
        configuration.knowledge_db_path,
        configuration.knowledge_blob_dir,
        configuration.run_evidence_store_backend,
        configuration.run_evidence_db_path,
        configuration.enable_paperqa_retrieval,
        configuration.enable_knowledge_writeback,
        configuration.search_api.value,
        configuration.knowledge_lifecycle_policy_version,
        configuration.requirement_completion_policy_version,
        configuration.candidate_min_content_chars,
        configuration.candidate_min_confidence,
        configuration.min_source_authority,
        configuration.max_evidence_age_days,
        configuration.min_direct_evidence,
        configuration.max_web_queries_per_run,
        configuration.max_web_results_per_query,
        configuration.max_web_results_per_run,
        configuration.max_concurrent_web_requests,
        configuration.run_evidence_ttl_seconds,
        id(web_provider) if web_provider is not None else None,
    )
    with _RUNTIME_LOCK:
        existing = _RUNTIME_CACHE.get(key)
        if existing is not None:
            return existing
        runtime = _build_runtime(
            configuration,
            resolved_run_id.strip(),
            web_provider=web_provider,
        )
        _RUNTIME_CACHE[key] = runtime
        return runtime


def _build_runtime(
    configuration: Configuration,
    run_id: str,
    *,
    web_provider: WebSearchProvider | None,
) -> GovernedRuntime:
    scope = KnowledgeScope(
        tenant_id=configuration.knowledge_tenant_id,
        project_id=configuration.knowledge_project_id,
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id=scope.tenant_id,
        trusted_project_id=scope.project_id,
        auth_source="phase3-governed-runtime",
        request_id=run_id,
    )
    canonical = _canonical_services(configuration, scope)
    repository = canonical.repository
    blob_repository = canonical.blob_repository
    retriever = canonical.retriever
    run_store = (
        InMemoryRunEvidenceStore()
        if configuration.run_evidence_store_backend == "memory"
        else SQLiteRunEvidenceStore(configuration.run_evidence_db_path)
    )
    accepted_authorities = _authorities_at_least(
        AuthorityClass(configuration.min_source_authority)
    )
    coverage_policy = CoveragePolicy(
        policy_version=configuration.requirement_completion_policy_version,
        min_confidence=configuration.candidate_min_confidence,
        min_direct_evidence=configuration.min_direct_evidence,
        accepted_authorities=accepted_authorities,
        max_evidence_age_days=configuration.max_evidence_age_days,
    )
    validation_policy = CandidateValidationPolicy(
        policy_version=configuration.knowledge_lifecycle_policy_version,
        min_content_chars=configuration.candidate_min_content_chars,
        min_confidence=configuration.candidate_min_confidence,
        min_source_authority=AuthorityClass(configuration.min_source_authority),
        max_evidence_age_days=configuration.max_evidence_age_days,
    )
    selected_provider = web_provider
    if selected_provider is None and configuration.search_api is SearchAPI.TAVILY:
        selected_provider = TavilyStructuredWebSearchProvider()
    orchestrator = GovernedRetrievalOrchestrator(
        repository=repository,
        blob_repository=blob_repository,
        retriever=retriever,
        run_store=run_store,
        budget_registry=RunBudgetRegistry(),
        budget_limits=RunBudgetLimits(
            max_queries=configuration.max_web_queries_per_run,
            max_tool_calls=configuration.max_web_queries_per_run,
            max_results=configuration.max_web_results_per_run,
            max_concurrency=configuration.max_concurrent_web_requests,
        ),
        validation_policy=validation_policy,
        coverage_policy=coverage_policy,
        completion_gate=ResearchCompletionGate(
            policy_version=configuration.requirement_completion_policy_version
        ),
        lifecycle_service=KnowledgeLifecycleService(repository),
        web_provider=selected_provider,
        writeback=configuration.enable_knowledge_writeback,
        run_evidence_ttl=timedelta(
            seconds=configuration.run_evidence_ttl_seconds
        ),
        max_web_results_per_query=configuration.max_web_results_per_query,
    )
    return GovernedRuntime(
        run_id=run_id,
        scope=scope,
        access=access,
        repository=repository,
        blob_repository=blob_repository,
        retriever=retriever,
        orchestrator=orchestrator,
    )


def _canonical_services(
    configuration: Configuration, scope: KnowledgeScope
) -> _CanonicalServices:
    """Build one process-local canonical boundary per configured scope/store."""
    key = (
        scope.scope_id,
        configuration.knowledge_repository_backend,
        configuration.knowledge_db_path,
        configuration.knowledge_blob_dir,
        configuration.sqlite_busy_timeout_ms,
        configuration.enable_paperqa_retrieval,
        configuration.paperqa_index_dir,
    )
    existing = _CANONICAL_CACHE.get(key)
    if existing is not None:
        return existing
    if configuration.knowledge_repository_backend == "memory":
        repository = InMemoryRepository()
        blob_repository = InMemoryBlobRepository()
    else:
        repository = SQLiteRepository(
            configuration.knowledge_db_path,
            busy_timeout_ms=configuration.sqlite_busy_timeout_ms,
        )
        blob_repository = LocalBlobRepository(configuration.knowledge_blob_dir)
    catalog = RepositoryRetrievalCatalog(repository)
    fallback = RepositoryKnowledgeRetriever(catalog)
    if configuration.enable_paperqa_retrieval:
        settings = create_offline_paperqa_settings(configuration.paperqa_index_dir)
        retriever = PaperQAKnowledgeRetriever(
            catalog,
            backend=NativePaperQABackend(
                settings=settings,
                embedding_model=DeterministicHashEmbedding(dimensions=1024),
            ),
            enabled=True,
            fallback_on_error=True,
        )
    else:
        retriever = fallback
    services = _CanonicalServices(
        repository=repository,
        blob_repository=blob_repository,
        retriever=retriever,
    )
    _CANONICAL_CACHE[key] = services
    return services


def _authorities_at_least(minimum: AuthorityClass) -> tuple[AuthorityClass, ...]:
    order = (
        AuthorityClass.UNKNOWN,
        AuthorityClass.SELF_REPORTED,
        AuthorityClass.SECONDARY,
        AuthorityClass.PRIMARY,
        AuthorityClass.OFFICIAL,
    )
    return order[order.index(minimum) :]


def clear_governed_runtime_cache() -> None:
    """Test-only process cache reset; it does not delete persisted data."""
    with _RUNTIME_LOCK:
        _RUNTIME_CACHE.clear()
        _CANONICAL_CACHE.clear()
