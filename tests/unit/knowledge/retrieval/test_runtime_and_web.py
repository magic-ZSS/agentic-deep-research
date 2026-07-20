from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import wraps

import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.retrieval.runtime import (
    GovernedRuntimeConfigurationError,
    clear_governed_runtime_cache,
    get_governed_runtime,
)
from open_deep_research.knowledge.retrieval.web import (
    StructuredWebResult,
    TavilyStructuredWebSearchProvider,
    WebSearchRuntime,
    require_web_provider,
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class FakeProvider:
    name = "fake"

    async def search(self, queries, *, runtime, config=None):
        del queries, runtime, config
        return ()


def runtime_config(run_id: str, **overrides):
    values = {
        "enable_agentic_rag": True,
        "knowledge_repository_backend": "memory",
        "knowledge_tenant_id": "tenant",
        "knowledge_project_id": "project",
        "search_api": "none",
        "_process_context": {"run_id": run_id},
    }
    values.update(overrides)
    return {"configurable": values}


def test_runtime_rejects_untrusted_injected_object() -> None:
    with pytest.raises(
        GovernedRuntimeConfigurationError, match="invalid type"
    ):
        get_governed_runtime(
            {"configurable": {"_governed_runtime": object()}},
        )


def test_memory_canonical_services_are_shared_across_independent_runs() -> None:
    clear_governed_runtime_cache()
    provider = FakeProvider()
    first = get_governed_runtime(
        runtime_config("run-one"), run_id="run-one", web_provider=provider
    )
    second = get_governed_runtime(
        runtime_config("run-two"), run_id="run-two", web_provider=provider
    )
    assert first is not second
    assert first.repository is second.repository
    assert first.blob_repository is second.blob_repository
    assert first.retriever is second.retriever
    assert first.orchestrator is not second.orchestrator


def test_policy_or_provider_change_does_not_reuse_stale_runtime() -> None:
    clear_governed_runtime_cache()
    first_provider = FakeProvider()
    second_provider = FakeProvider()
    first = get_governed_runtime(
        runtime_config("run-policy", candidate_min_confidence=0.6),
        run_id="run-policy",
        web_provider=first_provider,
    )
    changed_policy = get_governed_runtime(
        runtime_config("run-policy", candidate_min_confidence=0.9),
        run_id="run-policy",
        web_provider=first_provider,
    )
    changed_provider = get_governed_runtime(
        runtime_config("run-policy", candidate_min_confidence=0.9),
        run_id="run-policy",
        web_provider=second_provider,
    )
    assert first is not changed_policy
    assert changed_policy is not changed_provider
    assert first.repository is changed_policy.repository is changed_provider.repository


@async_test
async def test_tavily_adapter_is_structured_bounded_and_deterministic(
    monkeypatch,
) -> None:
    async def fake_search(*args, **kwargs):
        assert args == (["alpha"],)
        assert kwargs["max_results"] == 2
        return [
            {
                "query": "alpha",
                "results": [
                    {
                        "url": "https://example.test/same",
                        "title": "Lower",
                        "raw_content": "lower result content",
                        "score": 0.2,
                        "nested": object(),
                    },
                    {
                        "url": "https://example.test/same",
                        "title": "Higher",
                        "raw_content": "higher result content",
                        "score": 0.9,
                        "source_type": "primary",
                    },
                    {"url": "https://invalid.test", "title": "missing"},
                ],
            }
        ]

    monkeypatch.setattr(
        "open_deep_research.utils.tavily_search_async", fake_search
    )
    provider = TavilyStructuredWebSearchProvider()
    results = await provider.search(
        ("alpha",),
        runtime=WebSearchRuntime(
            scope_id="scope",
            run_id="run",
            researcher_id="researcher",
            max_results_per_query=2,
        ),
    )
    assert len(results) == 1
    assert results[0].title == "Higher"
    assert results[0].score == 0.9
    assert results[0].metadata == {
        "provider": "tavily-structured",
        "source_type": "primary",
    }
    assert "higher result content" not in results[0].metadata.values()


def test_web_contract_rejects_naive_timestamps_and_wrong_provider() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        StructuredWebResult(
            query="query",
            url="https://example.test",
            title="title",
            content="content",
            retrieved_at=datetime(2026, 7, 21),
        )
    with pytest.raises(TypeError, match="WebSearchProvider"):
        require_web_provider(object())
    with pytest.raises(ValidationError, match="identity cannot be blank"):
        WebSearchRuntime(
            scope_id=" ", run_id="run", researcher_id="researcher"
        )

    aware = StructuredWebResult(
        query="query",
        url="https://EXAMPLE.test:443/path#fragment",
        title="title",
        content="content",
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert aware.url == "https://example.test/path"
