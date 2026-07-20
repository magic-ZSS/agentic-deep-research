from __future__ import annotations

import asyncio
from functools import wraps

import pytest

from open_deep_research.knowledge.retrieval.budget import (
    DuplicateRunQueryError,
    RunBudgetConflictError,
    RunBudgetExceededError,
    RunBudgetLimits,
    RunBudgetRegistry,
    RunScopedBudget,
    normalize_run_query,
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


LIMITS = RunBudgetLimits(
    max_queries=3,
    max_tool_calls=4,
    max_results=6,
    max_concurrency=1,
)


@async_test
async def test_registry_shares_one_budget_across_researchers() -> None:
    registry = RunBudgetRegistry()
    first, second = await asyncio.gather(
        registry.get_or_create("run-one", LIMITS),
        registry.get_or_create("run-one", LIMITS),
    )
    assert first is second

    reservation = await first.reserve("Requirement A", result_limit=2)
    snapshot = await second.snapshot()
    assert snapshot.queries_used == 1
    assert snapshot.tool_calls_used == 1
    assert snapshot.results_reserved == 2
    assert snapshot.in_flight == 1
    await second.release(reservation)
    final = await registry.release_run("run-one")
    assert final.in_flight == 0

    with pytest.raises(RunBudgetConflictError, match="different limits"):
        budget = await registry.get_or_create("run-conflict", LIMITS)
        assert budget
        await registry.get_or_create(
            "run-conflict",
            RunBudgetLimits(1, 1, 1, 1),
        )


@async_test
async def test_concurrency_slot_is_shared_and_released() -> None:
    budget = RunScopedBudget("run-concurrency", LIMITS)
    first = await budget.reserve("first", result_limit=1)
    waiting = asyncio.create_task(budget.reserve("second", result_limit=1))
    await asyncio.sleep(0)
    assert not waiting.done()

    await budget.release(first)
    second = await waiting
    assert (await budget.snapshot()).in_flight == 1
    await budget.release(second)
    assert (await budget.snapshot()).in_flight == 0


@async_test
async def test_query_dedupe_is_atomic_and_normalized_across_researchers() -> None:
    limits = RunBudgetLimits(5, 5, 5, 2)
    budget = RunScopedBudget("run-dedupe", limits)
    first = await budget.reserve("ＡLPHA   Query")
    await budget.release(first)

    with pytest.raises(DuplicateRunQueryError):
        await budget.reserve("alpha\nquery")
    assert normalize_run_query("  Alpha\tQuery ") == "alpha query"
    snapshot = await budget.snapshot()
    assert snapshot.queries_used == 1
    assert snapshot.normalized_queries == ("alpha query",)


@async_test
async def test_failed_calls_consume_query_tool_and_result_capacity() -> None:
    budget = RunScopedBudget("run-failure", LIMITS)
    reservation = await budget.reserve(
        "will fail", tool_calls=2, result_limit=3
    )
    after_failure = await budget.release(reservation, failed=True)
    assert after_failure.queries_used == 1
    assert after_failure.tool_calls_used == 2
    assert after_failure.results_reserved == 3
    assert after_failure.in_flight == 0

    # Release is idempotent and must not over-release the bounded semaphore.
    assert await budget.release(reservation, failed=True) == after_failure
    next_reservation = await budget.reserve("next", result_limit=1)
    await budget.release(next_reservation)


@async_test
async def test_every_global_limit_is_checked_before_reservation() -> None:
    result_budget = RunScopedBudget(
        "run-result-cap", RunBudgetLimits(3, 3, 3, 2)
    )
    first = await result_budget.reserve("first", result_limit=2)
    with pytest.raises(RunBudgetExceededError, match="results"):
        await result_budget.reserve("second", result_limit=2)
    second = await result_budget.reserve("second", result_limit=1)
    await result_budget.release(first)
    await result_budget.release(second)

    tool_budget = RunScopedBudget(
        "run-tool-cap", RunBudgetLimits(3, 2, 5, 2)
    )
    tool = await tool_budget.reserve("tool-heavy", tool_calls=2)
    with pytest.raises(RunBudgetExceededError, match="tool_calls"):
        await tool_budget.reserve("another")
    await tool_budget.release(tool)

    query_budget = RunScopedBudget(
        "run-query-cap", RunBudgetLimits(1, 3, 3, 2)
    )
    only = await query_budget.reserve("only")
    with pytest.raises(RunBudgetExceededError, match="queries"):
        await query_budget.reserve("extra")
    await query_budget.release(only)


@async_test
async def test_parallel_reservations_never_exceed_hard_limits() -> None:
    budget = RunScopedBudget(
        "run-atomic", RunBudgetLimits(3, 3, 3, 8)
    )

    async def attempt(index: int) -> str:
        try:
            reservation = await budget.reserve(f"query {index}")
        except RunBudgetExceededError:
            return "rejected"
        await asyncio.sleep(0)
        await budget.release(reservation)
        return "accepted"

    outcomes = await asyncio.gather(*(attempt(index) for index in range(12)))
    assert outcomes.count("accepted") == 3
    assert outcomes.count("rejected") == 9
    snapshot = await budget.snapshot()
    assert snapshot.queries_used == 3
    assert snapshot.tool_calls_used == 3
    assert snapshot.results_reserved == 3
    assert snapshot.in_flight == 0
    assert snapshot.exhausted


@async_test
async def test_release_rejects_other_run_and_live_run_registry_cleanup() -> None:
    registry = RunBudgetRegistry()
    budget = await registry.get_or_create("run-owner", LIMITS)
    other = RunScopedBudget("run-other", LIMITS)
    reservation = await budget.reserve("held")

    with pytest.raises(RunBudgetConflictError, match="in-flight"):
        await registry.release_run("run-owner")
    with pytest.raises(RunBudgetConflictError, match="another run"):
        await other.release(reservation)
    await budget.release(reservation)
    await registry.release_run("run-owner")
    with pytest.raises(RunBudgetConflictError, match="unknown"):
        await registry.release_run("run-owner")


def test_limits_and_inputs_fail_closed() -> None:
    with pytest.raises(ValueError):
        RunBudgetLimits(-1, 1, 1, 1)
    with pytest.raises(ValueError):
        RunBudgetLimits(1, 1, 1, 0)
    with pytest.raises(ValueError, match="blank"):
        RunScopedBudget(" ", LIMITS)

    async def scenario() -> None:
        budget = RunScopedBudget("run-input", LIMITS)
        with pytest.raises(ValueError, match="blank"):
            await budget.reserve(" ")
        with pytest.raises(ValueError, match="positive"):
            await budget.reserve("query", result_limit=0)

    asyncio.run(scenario())
