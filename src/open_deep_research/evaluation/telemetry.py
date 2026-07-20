"""Opt-in local telemetry for a runnable without changing graph state."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import merge_configs

from open_deep_research.evaluation.models import (
    RunStatus,
    RunTelemetry,
    TelemetrySpan,
)


_SEARCH_TOOL_NAMES = {
    "tavily_search",
    "web_search",
    "search",
    "search_query",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _name_from_serialized(serialized: dict[str, Any] | None, fallback: str) -> str:
    if not serialized:
        return fallback
    name = serialized.get("name")
    if isinstance(name, str) and name:
        return name
    identifiers = serialized.get("id")
    if isinstance(identifiers, list) and identifiers:
        return str(identifiers[-1])
    return fallback


@dataclass
class _OpenSpan:
    run_id: str
    parent_run_id: str | None
    kind: str
    name: str
    started_at: datetime
    started_ns: int


class EvaluationTelemetryCollector:
    """Thread-safe callback collector scoped to exactly one invocation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at: datetime | None = None
        self._started_ns: int | None = None
        self._finished = False
        self._telemetry: RunTelemetry | None = None
        self._open_spans: dict[str, _OpenSpan] = {}
        self._spans: list[TelemetrySpan] = []
        self._model_run_ids: set[str] = set()
        self._model_usage: dict[str, tuple[int, int]] = {}
        self._model_ended: set[str] = set()
        self._tool_run_ids: set[str] = set()
        self._tool_counts: Counter[str] = Counter()
        self._tool_request_keys: set[tuple[str, str]] = set()
        self._tool_requests: Counter[str] = Counter()

    @property
    def callback(self) -> BaseCallbackHandler:
        """Return the LangChain callback view for this collector."""
        return _EvaluationCallback(self)

    @property
    def telemetry(self) -> RunTelemetry | None:
        """Expose the final snapshot after ``finish`` was called."""
        with self._lock:
            return self._telemetry

    def start(self) -> None:
        """Start the outer wall clock once."""
        with self._lock:
            if self._started_at is not None:
                raise RuntimeError("telemetry collector is single-use")
            self._started_at = _utc_now()
            self._started_ns = time.perf_counter_ns()

    def _open(
        self,
        run_id: UUID | str,
        parent_run_id: UUID | str | None,
        kind: str,
        name: str,
    ) -> None:
        key = str(run_id)
        with self._lock:
            if key in self._open_spans or any(span.run_id == key for span in self._spans):
                return
            self._open_spans[key] = _OpenSpan(
                run_id=key,
                parent_run_id=str(parent_run_id) if parent_run_id is not None else None,
                kind=kind,
                name=name,
                started_at=_utc_now(),
                started_ns=time.perf_counter_ns(),
            )

    def _close(
        self,
        run_id: UUID | str,
        status: str,
        error_type: str | None = None,
    ) -> None:
        key = str(run_id)
        with self._lock:
            opened = self._open_spans.pop(key, None)
            if opened is None:
                return
            finished_at = _utc_now()
            duration_ms = max(
                0.0, (time.perf_counter_ns() - opened.started_ns) / 1_000_000
            )
            self._spans.append(
                TelemetrySpan(
                    run_id=opened.run_id,
                    parent_run_id=opened.parent_run_id,
                    kind=opened.kind,
                    name=opened.name,
                    started_at=opened.started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status=status,
                    error_type=error_type,
                )
            )

    def _record_model_start(
        self,
        serialized: dict[str, Any] | None,
        run_id: UUID | str,
        parent_run_id: UUID | str | None,
    ) -> None:
        key = str(run_id)
        with self._lock:
            self._model_run_ids.add(key)
        self._open(run_id, parent_run_id, "model", _name_from_serialized(serialized, "model"))

    def _record_model_end(self, response: Any, run_id: UUID | str) -> None:
        key = str(run_id)
        usage = _extract_token_usage(response)
        with self._lock:
            self._model_ended.add(key)
            if usage is not None:
                self._model_usage[key] = usage
            self._record_tool_requests(response, key)
        self._close(run_id, "completed")

    def _record_tool_requests(self, response: Any, model_run_id: str) -> None:
        generations = getattr(response, "generations", None) or []
        for generation_group in generations:
            group = generation_group if isinstance(generation_group, list) else [generation_group]
            for generation in group:
                message = getattr(generation, "message", None)
                tool_calls = getattr(message, "tool_calls", None) or []
                for index, tool_call in enumerate(tool_calls):
                    if isinstance(tool_call, dict):
                        name = str(tool_call.get("name") or "unknown_tool")
                        call_id = str(tool_call.get("id") or index)
                    else:
                        name = str(getattr(tool_call, "name", "unknown_tool"))
                        call_id = str(getattr(tool_call, "id", index))
                    request_key = (model_run_id, call_id)
                    if request_key not in self._tool_request_keys:
                        self._tool_request_keys.add(request_key)
                        self._tool_requests[name] += 1

    def _record_tool_start(
        self,
        serialized: dict[str, Any] | None,
        run_id: UUID | str,
        parent_run_id: UUID | str | None,
        name: str | None,
    ) -> None:
        key = str(run_id)
        tool_name = name or _name_from_serialized(serialized, "unknown_tool")
        with self._lock:
            if key not in self._tool_run_ids:
                self._tool_run_ids.add(key)
                self._tool_counts[tool_name] += 1
        self._open(run_id, parent_run_id, "tool", tool_name)

    def finish(
        self,
        status: RunStatus,
        error_type: str | None = None,
    ) -> RunTelemetry:
        """Close remaining spans and create an immutable validated snapshot."""
        with self._lock:
            if self._finished:
                assert self._telemetry is not None
                return self._telemetry
            if self._started_at is None or self._started_ns is None:
                raise RuntimeError("telemetry collector has not been started")

            span_status = {
                RunStatus.COMPLETED: "completed",
                RunStatus.FAILED: "failed",
                RunStatus.CANCELLED: "cancelled",
            }.get(status, "failed")
            for open_run_id in list(self._open_spans):
                self._close(open_run_id, span_status, error_type)

            finished_at = _utc_now()
            wall_time_ms = max(
                0.0, (time.perf_counter_ns() - self._started_ns) / 1_000_000
            )
            model_calls = len(self._model_run_ids)
            complete_usage = (
                model_calls == len(self._model_usage)
                and self._model_ended.issuperset(self._model_run_ids)
            )
            if complete_usage:
                input_tokens = sum(item[0] for item in self._model_usage.values())
                output_tokens = sum(item[1] for item in self._model_usage.values())
                total_tokens = input_tokens + output_tokens
            else:
                input_tokens = output_tokens = total_tokens = None

            search_calls = sum(
                count
                for name, count in self._tool_counts.items()
                if name.lower() in _SEARCH_TOOL_NAMES
            )
            self._telemetry = RunTelemetry(
                started_at=self._started_at,
                finished_at=finished_at,
                wall_time_ms=wall_time_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=None,
                model_calls=model_calls,
                model_calls_with_usage=len(self._model_usage),
                tool_requests_by_name=dict(sorted(self._tool_requests.items())),
                tool_calls_by_name=dict(sorted(self._tool_counts.items())),
                search_calls=search_calls,
                search_calls_complete=False,
                researcher_runs=None,
                status=status,
                error_type=error_type,
                spans=sorted(self._spans, key=lambda span: (span.started_at, span.run_id)),
            )
            self._finished = True
            return self._telemetry


class _EvaluationCallback(BaseCallbackHandler):
    """LangChain callback forwarding observations to a local collector."""

    def __init__(self, collector: EvaluationTelemetryCollector) -> None:
        self._collector = collector

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del prompts, kwargs
        self._collector._record_model_start(serialized, run_id, parent_run_id)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del messages, kwargs
        self._collector._record_model_start(serialized, run_id, parent_run_id)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        del kwargs
        self._collector._record_model_end(response, run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        del kwargs
        self._collector._close(run_id, "failed", type(error).__name__)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        del input_str, kwargs
        self._collector._record_tool_start(serialized, run_id, parent_run_id, name)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        del output, kwargs
        self._collector._close(run_id, "completed")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        del kwargs
        self._collector._close(run_id, "failed", type(error).__name__)

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        del serialized, inputs, run_id, parent_run_id, name, kwargs

    def on_chain_end(self, outputs: dict[str, Any], *, run_id: UUID, **kwargs: Any) -> None:
        del outputs, kwargs
        self._collector._close(run_id, "completed")

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        del kwargs
        self._collector._close(run_id, "failed", type(error).__name__)


def _extract_token_usage(response: Any) -> tuple[int, int] | None:
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return input_tokens, output_tokens

    generations = getattr(response, "generations", None) or []
    for generation_group in generations:
        group = generation_group if isinstance(generation_group, list) else [generation_group]
        for generation in group:
            message = getattr(generation, "message", None)
            message_usage = getattr(message, "usage_metadata", None)
            if isinstance(message_usage, dict):
                input_tokens = message_usage.get("input_tokens")
                output_tokens = message_usage.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    return input_tokens, output_tokens
    return None


async def ainvoke_with_evaluation_telemetry(
    runnable: Any,
    input_value: Any,
    config: dict[str, Any] | None = None,
    *,
    enabled: bool = False,
    collector: EvaluationTelemetryCollector | None = None,
) -> Any:
    """Invoke a runnable, returning its original result and exception types.

    When disabled, this is a direct call with the exact input/config objects and no
    callback. When enabled, callers can inspect ``collector.telemetry`` afterward.
    """
    if not enabled:
        return await runnable.ainvoke(input_value, config)

    active_collector = collector or EvaluationTelemetryCollector()
    active_collector.start()
    callback = active_collector.callback
    merged_config = merge_configs(config or {}, {"callbacks": [callback]})
    try:
        result = await runnable.ainvoke(input_value, merged_config)
    except asyncio.CancelledError as exc:
        active_collector.finish(RunStatus.CANCELLED, type(exc).__name__)
        raise
    except BaseException as exc:
        active_collector.finish(RunStatus.FAILED, type(exc).__name__)
        raise
    active_collector.finish(RunStatus.COMPLETED)
    return result
