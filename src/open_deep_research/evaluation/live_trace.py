"""Local callback trace used by paid evaluation without platform upload."""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from open_deep_research.evaluation.artifact_safety import sanitize_evaluation_value
from open_deep_research.evaluation.trace_adapter import TraceEvent

_RETRIEVAL_TOOLS = {
    "tavily_search",
    "web_search",
    "governed_retrieval",
    "knowledge_search",
    "knowledge_read",
}


def _bounded_text(value: Any, limit: int) -> str:
    """Serialize callback values without retaining unbounded provider payloads."""
    sanitized = sanitize_evaluation_value(value)
    if isinstance(sanitized, str):
        text = sanitized
    else:
        try:
            text = json.dumps(sanitized, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(sanitized)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[truncated:{len(text) - limit}]"


class LiveTraceCollector(BaseCallbackHandler):
    """Capture actual tool activity and an explicit graph-produced plan."""

    def __init__(self, *, max_input_chars: int = 8_000, max_output_chars: int = 30_000):
        """Configure bounded local trace retention."""
        self._lock = threading.RLock()
        self._max_input_chars = max_input_chars
        self._max_output_chars = max_output_chars
        self._sequence = 0
        self._events: dict[str, TraceEvent] = {}

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    @property
    def events(self) -> list[TraceEvent]:
        """Return a deterministic immutable snapshot."""
        with self._lock:
            return sorted(self._events.values(), key=lambda item: (item.sequence, item.event_id))

    def add_plan(self, value: Any) -> None:
        """Record only a plan emitted by the graph state."""
        if value is None:
            return
        if isinstance(value, str):
            steps = [line.strip() for line in value.splitlines() if line.strip()]
        elif isinstance(value, list | tuple):
            steps = [_bounded_text(item, self._max_input_chars) for item in value]
        else:
            steps = [_bounded_text(value, self._max_input_chars)]
        if not steps:
            return
        with self._lock:
            self._events["graph-plan"] = TraceEvent(
                event_id="graph-plan",
                kind="plan",
                name="research_plan",
                sequence=self._next_sequence(),
                output=steps,
            )

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
        """Record a bounded tool request without uploading it."""
        del kwargs
        tool_name = name or str(serialized.get("name") or "unknown_tool")
        key = str(run_id)
        kind = "retriever" if tool_name.lower() in _RETRIEVAL_TOOLS else "tool"
        with self._lock:
            self._events[key] = TraceEvent(
                event_id=key,
                parent_id=str(parent_run_id) if parent_run_id else None,
                kind=kind,
                name=tool_name,
                sequence=self._next_sequence(),
                input={"raw": _bounded_text(input_str, self._max_input_chars)},
                status="started",
            )

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Complete a tool event with bounded output."""
        del kwargs
        key = str(run_id)
        with self._lock:
            current = self._events.get(key)
            if current is None:
                return
            self._events[key] = current.model_copy(
                update={
                    "output": _bounded_text(output, self._max_output_chars),
                    "status": "completed",
                }
            )

    def on_tool_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        """Record only the error type for a failed tool call."""
        del kwargs
        key = str(run_id)
        with self._lock:
            current = self._events.get(key)
            if current is None:
                return
            self._events[key] = current.model_copy(
                update={
                    "output": {"error_type": type(error).__name__},
                    "status": "failed",
                }
            )
