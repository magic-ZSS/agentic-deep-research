"""Budget-enforcing LangChain callback for paid research model calls."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetFailClosed,
    LiveTokenReservationLedger,
    TokenUsageCategory,
)
from open_deep_research.evaluation.telemetry import _extract_token_usage

_PROVIDER_OUTPUT_TOKEN_MARGIN = 256


def conservative_input_upper_bound(value: Any) -> int:
    """Bound token count by UTF-8 bytes plus explicit message overhead."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    return len(serialized.encode("utf-8")) + 1_024


def _max_output_tokens(kwargs: dict[str, Any], fallback: int) -> int:
    invocation = kwargs.get("invocation_params")
    candidates = invocation if isinstance(invocation, dict) else {}
    for name in ("max_tokens", "max_completion_tokens"):
        value = candidates.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value + _PROVIDER_OUTPUT_TOKEN_MARGIN
    return fallback + _PROVIDER_OUTPUT_TOKEN_MARGIN


class LiveModelBudgetCallback(BaseCallbackHandler):
    """Reserve before each model call and settle only provider-reported usage."""

    raise_error = True

    def __init__(
        self,
        *,
        ledger: LiveTokenReservationLedger,
        evaluation_run_id: str,
        default_output_upper_bound: int,
        persist_snapshot: Callable[[dict[str, Any]], None],
    ) -> None:
        """Bind one evaluation run to a durable reservation ledger."""
        self._ledger = ledger
        self._evaluation_run_id = evaluation_run_id
        self._default_output_upper_bound = default_output_upper_bound
        self._persist_snapshot = persist_snapshot
        self._lock = threading.RLock()
        self._active: dict[str, str] = {}

    def _start(self, payload: Any, run_id: UUID, kwargs: dict[str, Any]) -> None:
        key = str(run_id)
        with self._lock:
            if key in self._active:
                return
            input_bound = conservative_input_upper_bound(
                {"payload": payload, "invocation": kwargs.get("invocation_params")}
            )
            reservation = self._ledger.reserve_before_call(
                run_id=self._evaluation_run_id,
                category=TokenUsageCategory.RESEARCH,
                input_upper_bound=input_bound,
                output_upper_bound=_max_output_tokens(
                    kwargs, self._default_output_upper_bound
                ),
                reservation_id=f"research:{self._evaluation_run_id}:{key}",
            )
            self._active[key] = reservation.reservation_id
            try:
                self._persist_snapshot(self._ledger.snapshot())
            except BaseException:
                self._active.pop(key, None)
                self._ledger.settle_success(
                    reservation.reservation_id,
                    actual_input_tokens=0,
                    actual_output_tokens=0,
                )
                raise

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Reserve budget before a non-chat language-model dispatch."""
        del serialized
        self._start(prompts, run_id, kwargs)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Reserve budget before a chat-model dispatch."""
        del serialized
        self._start(messages, run_id, kwargs)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Settle a completed call using only provider-reported token usage."""
        del kwargs
        key = str(run_id)
        with self._lock:
            reservation_id = self._active.pop(key, None)
            if reservation_id is None:
                return
            usage = _extract_token_usage(response)
            if usage is None:
                self._ledger.settle_error(
                    reservation_id, error_signature="MissingTokenUsage"
                )
                self._persist_snapshot(self._ledger.snapshot())
                raise LiveTokenBudgetFailClosed(
                    "research model response omitted token usage"
                )
            try:
                self._ledger.settle_success(
                    reservation_id,
                    actual_input_tokens=usage[0],
                    actual_output_tokens=usage[1],
                )
            except BaseException as settlement_error:
                try:
                    self._persist_snapshot(self._ledger.snapshot())
                except BaseException as persistence_error:
                    settlement_error.add_note(
                        "token ledger persistence also failed: "
                        f"{type(persistence_error).__name__}"
                    )
                raise
            self._persist_snapshot(self._ledger.snapshot())

    def on_llm_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        """Charge the full reservation and fail closed after a model error."""
        del kwargs
        key = str(run_id)
        with self._lock:
            reservation_id = self._active.pop(key, None)
            if reservation_id is None:
                return
            self._ledger.settle_error(
                reservation_id, error_signature=type(error).__name__
            )
            self._persist_snapshot(self._ledger.snapshot())
