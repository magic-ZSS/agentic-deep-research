"""Atomic run-scoped budgets shared by concurrent researchers."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass

from open_deep_research.knowledge.ids import stable_id


class RunBudgetError(RuntimeError):
    """Base class for run budget failures."""


class RunBudgetExceededError(RunBudgetError):
    """A reservation would exceed a global run limit."""


class DuplicateRunQueryError(RunBudgetError):
    """The normalized Web query has already been reserved in this run."""


class RunBudgetConflictError(RunBudgetError):
    """A run or reservation was reused with conflicting data."""


@dataclass(frozen=True, slots=True)
class RunBudgetLimits:
    """Hard ceilings for one run; zero disables the corresponding resource."""

    max_queries: int
    max_tool_calls: int
    max_results: int
    max_concurrency: int

    def __post_init__(self) -> None:
        if min(self.max_queries, self.max_tool_calls, self.max_results) < 0:
            raise ValueError("run budget limits cannot be negative")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")


@dataclass(frozen=True, slots=True)
class RunBudgetReservation:
    """A capacity allocation consumed before an external tool is invoked."""

    reservation_id: str
    run_id: str
    query: str
    query_key: str
    tool_calls: int
    result_limit: int


@dataclass(frozen=True, slots=True)
class RunBudgetSnapshot:
    """Deterministic immutable view of a run budget."""

    run_id: str
    limits: RunBudgetLimits
    queries_used: int
    tool_calls_used: int
    results_reserved: int
    in_flight: int
    normalized_queries: tuple[str, ...]

    @property
    def remaining_queries(self) -> int:
        return self.limits.max_queries - self.queries_used

    @property
    def remaining_tool_calls(self) -> int:
        return self.limits.max_tool_calls - self.tool_calls_used

    @property
    def remaining_results(self) -> int:
        return self.limits.max_results - self.results_reserved

    @property
    def exhausted(self) -> bool:
        return (
            self.remaining_queries == 0
            or self.remaining_tool_calls == 0
            or self.remaining_results == 0
        )


class RunScopedBudget:
    """In-process atomic counter, dedupe registry, lock, and semaphore."""

    def __init__(self, run_id: str, limits: RunBudgetLimits) -> None:
        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id cannot be blank")
        self.run_id = normalized_run_id
        self.limits = limits
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.BoundedSemaphore(limits.max_concurrency)
        self._queries_used = 0
        self._tool_calls_used = 0
        self._results_reserved = 0
        self._normalized_queries: set[str] = set()
        self._in_flight: dict[str, RunBudgetReservation] = {}
        self._completed: set[str] = set()

    async def reserve(
        self,
        query: str,
        *,
        tool_calls: int = 1,
        result_limit: int = 1,
    ) -> RunBudgetReservation:
        """Atomically consume capacity before a tool call, then hold a slot."""
        query_key = normalize_run_query(query)
        if tool_calls < 1 or result_limit < 1:
            raise ValueError("tool_calls and result_limit must be positive")
        await self._semaphore.acquire()
        try:
            async with self._lock:
                if query_key in self._normalized_queries:
                    raise DuplicateRunQueryError(query_key)
                failures = []
                if self._queries_used + 1 > self.limits.max_queries:
                    failures.append("queries")
                if self._tool_calls_used + tool_calls > self.limits.max_tool_calls:
                    failures.append("tool_calls")
                if self._results_reserved + result_limit > self.limits.max_results:
                    failures.append("results")
                if failures:
                    raise RunBudgetExceededError(
                        "run budget exhausted: " + ", ".join(failures)
                    )
                reservation = RunBudgetReservation(
                    reservation_id=stable_id(
                        "budget_res", self.run_id, query_key
                    ),
                    run_id=self.run_id,
                    query=query.strip(),
                    query_key=query_key,
                    tool_calls=tool_calls,
                    result_limit=result_limit,
                )
                self._queries_used += 1
                self._tool_calls_used += tool_calls
                self._results_reserved += result_limit
                self._normalized_queries.add(query_key)
                self._in_flight[reservation.reservation_id] = reservation
                return reservation
        except BaseException:
            self._semaphore.release()
            raise

    async def release(
        self,
        reservation: RunBudgetReservation,
        *,
        failed: bool = False,
    ) -> RunBudgetSnapshot:
        """Release concurrency only; failed calls intentionally keep capacity spent."""
        del failed  # The flag is diagnostic for callers; consumption is identical.
        release_slot = False
        async with self._lock:
            if reservation.run_id != self.run_id:
                raise RunBudgetConflictError("reservation belongs to another run")
            current = self._in_flight.get(reservation.reservation_id)
            if current is not None and current != reservation:
                raise RunBudgetConflictError("reservation contents do not match")
            if current is not None:
                del self._in_flight[reservation.reservation_id]
                self._completed.add(reservation.reservation_id)
                release_slot = True
            elif reservation.reservation_id not in self._completed:
                raise RunBudgetConflictError("reservation is unknown")
            snapshot = self._snapshot_unlocked()
        if release_slot:
            self._semaphore.release()
        return snapshot

    async def snapshot(self) -> RunBudgetSnapshot:
        """Return an atomic deterministic budget snapshot."""
        async with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> RunBudgetSnapshot:
        return RunBudgetSnapshot(
            run_id=self.run_id,
            limits=self.limits,
            queries_used=self._queries_used,
            tool_calls_used=self._tool_calls_used,
            results_reserved=self._results_reserved,
            in_flight=len(self._in_flight),
            normalized_queries=tuple(sorted(self._normalized_queries)),
        )


class RunBudgetRegistry:
    """Return one shared budget object to every researcher in the same run."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._budgets: dict[str, RunScopedBudget] = {}

    async def get_or_create(
        self, run_id: str, limits: RunBudgetLimits
    ) -> RunScopedBudget:
        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id cannot be blank")
        async with self._lock:
            existing = self._budgets.get(normalized_run_id)
            if existing is not None:
                if existing.limits != limits:
                    raise RunBudgetConflictError(
                        "run budget already exists with different limits"
                    )
                return existing
            budget = RunScopedBudget(normalized_run_id, limits)
            self._budgets[normalized_run_id] = budget
            return budget

    async def release_run(self, run_id: str) -> RunBudgetSnapshot:
        """Remove a completed run only after every reservation is released."""
        normalized_run_id = run_id.strip()
        async with self._lock:
            budget = self._budgets.get(normalized_run_id)
            if budget is None:
                raise RunBudgetConflictError("run budget is unknown")
            snapshot = await budget.snapshot()
            if snapshot.in_flight:
                raise RunBudgetConflictError("run budget still has in-flight calls")
            del self._budgets[normalized_run_id]
            return snapshot


def normalize_run_query(query: str) -> str:
    """Normalize superficial spelling differences for deterministic dedupe."""
    normalized = unicodedata.normalize("NFKC", query).strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        raise ValueError("query cannot be blank")
    return normalized
