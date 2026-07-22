from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetFailClosed,
    LiveTokenReservationLedger,
)
from open_deep_research.evaluation.live_callbacks import (
    LiveModelBudgetCallback,
    conservative_input_upper_bound,
)


def callback(ledger, snapshots):
    return LiveModelBudgetCallback(
        ledger=ledger,
        evaluation_run_id="run-1",
        default_output_upper_bound=100,
        persist_snapshot=snapshots.append,
    )


def response(input_tokens=20, output_tokens=10):
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    )
    generation = SimpleNamespace(message=message)
    return SimpleNamespace(generations=[[generation]], llm_output={})


def test_research_callback_reserves_before_and_settles_known_usage():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=5_000, per_run_token_limit=5_000
    )
    snapshots = []
    handler = callback(ledger, snapshots)
    run_id = uuid4()
    handler.on_chat_model_start(
        {}, [[{"role": "user", "content": "short"}]], run_id=run_id
    )
    assert snapshots[-1]["active_reserved_tokens"] > 0
    handler.on_llm_end(response(), run_id=run_id)
    assert snapshots[-1]["committed_tokens"] == 30
    assert snapshots[-1]["active_reserved_tokens"] == 0
    assert snapshots[0]["active_reservations"][0]["output_upper_bound"] == 356


def test_usage_over_reservation_is_persisted_before_fail_closed_propagates():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=10_000, per_run_token_limit=10_000
    )
    snapshots = []
    handler = callback(ledger, snapshots)
    run_id = uuid4()
    handler.on_llm_start({}, ["prompt"], run_id=run_id)
    with pytest.raises(LiveTokenBudgetFailClosed):
        handler.on_llm_end(response(input_tokens=20, output_tokens=357), run_id=run_id)
    assert snapshots[-1]["active_calls"] == 0
    assert snapshots[-1]["fail_closed"] is True
    assert snapshots[-1]["fail_closed_reason"] == (
        "actual_usage_exceeded_reservation"
    )


def test_missing_usage_and_model_error_charge_reservation_and_fail_closed():
    for missing_usage in (True, False):
        ledger = LiveTokenReservationLedger(
            hard_token_limit=10_000, per_run_token_limit=10_000
        )
        snapshots = []
        handler = callback(ledger, snapshots)
        run_id = uuid4()
        handler.on_llm_start({}, ["prompt"], run_id=run_id)
        if missing_usage:
            with pytest.raises(LiveTokenBudgetFailClosed):
                handler.on_llm_end(
                    SimpleNamespace(generations=[], llm_output={}), run_id=run_id
                )
        else:
            handler.on_llm_error(TimeoutError(), run_id=run_id)
        assert snapshots[-1]["fail_closed"] is True
        assert snapshots[-1]["unknown_usage"] is True


def test_input_bound_is_conservative_for_ascii_and_chinese():
    assert conservative_input_upper_bound("abcd") >= 4
    assert conservative_input_upper_bound("研究") >= len("研究".encode("utf-8"))
