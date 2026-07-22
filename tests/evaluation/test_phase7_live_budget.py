import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from open_deep_research.evaluation.live_budget import (
    LiveTokenBudgetError,
    LiveTokenBudgetExceeded,
    LiveTokenBudgetFailClosed,
    LiveTokenReservationLedger,
    TokenUsageCategory,
)


def test_success_charges_actual_and_releases_unused_reservation():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=600,
    )
    empty = ledger.snapshot()
    assert empty["revision"] == 0
    assert empty["dispatched_calls"] == 0
    assert empty["settled_calls"] == 0
    assert empty["error_calls"] == 0
    reservation = ledger.reserve_before_call(
        run_id="run-1",
        category="research",
        input_upper_bound=200,
        output_upper_bound=100,
        reservation_id="call-1",
    )
    reserved = ledger.snapshot()
    assert reserved["accounted_tokens"] == 300
    assert reserved["revision"] == 1
    assert reserved["dispatched_calls"] == 1
    assert reserved["settled_calls"] == 0
    assert reserved["error_calls"] == 0
    assert reserved["active_calls"] == 1
    assert reserved["categories"]["research"]["dispatched_calls"] == 1
    assert reserved["runs"]["run-1"]["dispatched_calls"] == 1

    settlement = ledger.settle_success(
        reservation.reservation_id,
        actual_input_tokens=120,
        actual_output_tokens=30,
    )
    assert settlement.charged_tokens == 150
    assert settlement.released_tokens == 150
    snapshot = ledger.snapshot()
    assert snapshot["revision"] == 2
    assert snapshot["committed_tokens"] == 150
    assert snapshot["active_reserved_tokens"] == 0
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["settled_calls"] == 1
    assert snapshot["error_calls"] == 0
    assert snapshot["active_calls"] == 0
    assert snapshot["categories"]["research"]["committed_tokens"] == 150
    assert snapshot["categories"]["research"]["settled_calls"] == 1
    assert snapshot["runs"]["run-1"]["settled_calls"] == 1
    assert snapshot["runs"]["run-1"]["remaining_tokens"] == 450


def test_experiment_and_run_overflow_are_rejected_before_mutation():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=500,
        per_run_token_limit=300,
    )
    ledger.reserve_before_call(
        run_id="run-a",
        category="research",
        input_upper_bound=100,
        output_upper_bound=100,
        reservation_id="a",
    )
    before = ledger.snapshot()
    with pytest.raises(LiveTokenBudgetExceeded, match="per-run"):
        ledger.reserve_before_call(
            run_id="run-a",
            category="judge",
            input_upper_bound=101,
            output_upper_bound=0,
            reservation_id="run-overflow",
        )
    assert ledger.snapshot() == before

    ledger.reserve_before_call(
        run_id="run-b",
        category="judge",
        input_upper_bound=100,
        output_upper_bound=100,
        reservation_id="b",
    )
    before = ledger.snapshot()
    with pytest.raises(LiveTokenBudgetExceeded, match="experiment"):
        ledger.reserve_before_call(
            run_id="run-c",
            category="retry",
            input_upper_bound=101,
            output_upper_bound=0,
            reservation_id="total-overflow",
        )
    assert ledger.snapshot() == before


def test_concurrent_reservations_are_atomic_and_include_in_flight_holds():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=1_000,
    )

    def attempt(index: int) -> bool:
        try:
            ledger.reserve_before_call(
                run_id="shared-run",
                category=TokenUsageCategory.JUDGE,
                input_upper_bound=100,
                output_upper_bound=100,
                reservation_id=f"call-{index}",
            )
        except LiveTokenBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    assert sum(results) == 5
    snapshot = ledger.snapshot()
    assert snapshot["revision"] == 5
    assert snapshot["dispatched_calls"] == 5
    assert snapshot["settled_calls"] == 0
    assert snapshot["active_calls"] == 5
    assert snapshot["active_reserved_tokens"] == 1_000
    assert len(snapshot["active_reservations"]) == 5


def test_error_charges_full_reservation_and_permanently_fails_closed():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=800,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="retry",
        input_upper_bound=250,
        output_upper_bound=150,
        reservation_id="retry-1",
    )
    settlement = ledger.settle_error("retry-1", error_signature="TimeoutError")
    assert settlement.charged_tokens == 400
    assert not settlement.usage_known
    snapshot = ledger.snapshot()
    assert snapshot["revision"] == 2
    assert snapshot["unknown_charged_tokens"] == 400
    assert snapshot["unknown_usage"] is True
    assert snapshot["fail_closed"] is True
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["settled_calls"] == 1
    assert snapshot["error_calls"] == 1
    assert snapshot["categories"]["retry"]["committed_tokens"] == 400
    assert snapshot["categories"]["retry"]["error_calls"] == 1
    assert snapshot["runs"]["run-1"]["error_calls"] == 1
    with pytest.raises(LiveTokenBudgetFailClosed, match="TimeoutError"):
        ledger.reserve_before_call(
            run_id="run-2",
            category="research",
            input_upper_bound=1,
            output_upper_bound=0,
        )


def test_snapshot_is_json_serializable_and_clean_restore_can_continue():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="research",
        input_upper_bound=100_000,
        output_upper_bound=50_000,
        reservation_id="research-1",
    )
    ledger.settle_success(
        "research-1", actual_input_tokens=70_000, actual_output_tokens=20_000
    )
    serialized = json.loads(json.dumps(ledger.snapshot()))
    restored = LiveTokenReservationLedger.from_snapshot(serialized)
    assert restored.snapshot() == ledger.snapshot()
    restored.reserve_before_call(
        run_id="run-1",
        category="judge",
        input_upper_bound=10_000,
        output_upper_bound=5_000,
        reservation_id="judge-1",
    )
    assert restored.snapshot()["accounted_tokens"] == 105_000


def test_restore_with_in_flight_call_conservatively_charges_and_fails_closed():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=800,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="judge",
        input_upper_bound=200,
        output_upper_bound=100,
        reservation_id="interrupted",
    )
    persisted = ledger.snapshot()
    assert persisted["revision"] == 1
    restored = LiveTokenReservationLedger.from_snapshot(persisted)
    snapshot = restored.snapshot()
    assert snapshot["revision"] == 2
    assert snapshot["active_reserved_tokens"] == 0
    assert snapshot["active_calls"] == 0
    assert snapshot["committed_tokens"] == 300
    assert snapshot["unknown_charged_tokens"] == 300
    assert snapshot["fail_closed_reason"] == "restored_with_unsettled_reservations"
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["settled_calls"] == 1
    assert snapshot["error_calls"] == 1
    assert snapshot["categories"]["judge"]["settled_calls"] == 1
    assert snapshot["categories"]["judge"]["error_calls"] == 1
    assert snapshot["runs"]["run-1"]["settled_calls"] == 1
    assert snapshot["runs"]["run-1"]["error_calls"] == 1
    assert LiveTokenReservationLedger.from_snapshot(snapshot).snapshot() == snapshot
    with pytest.raises(LiveTokenBudgetFailClosed):
        restored.reserve_before_call(
            run_id="run-2",
            category="research",
            input_upper_bound=1,
            output_upper_bound=0,
        )


def test_actual_usage_above_reservation_is_recorded_then_fails_closed():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=800,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="research",
        input_upper_bound=100,
        output_upper_bound=50,
        reservation_id="underestimated",
    )
    with pytest.raises(LiveTokenBudgetFailClosed, match="exceeded"):
        ledger.settle_success(
            "underestimated", actual_input_tokens=110, actual_output_tokens=20
        )
    snapshot = ledger.snapshot()
    assert snapshot["revision"] == 2
    assert snapshot["committed_tokens"] == 130
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["settled_calls"] == 1
    assert snapshot["error_calls"] == 1
    assert snapshot["fail_closed_reason"] == "actual_usage_exceeded_reservation"


def test_known_usage_error_counts_error_without_marking_usage_unknown():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=800,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="judge",
        input_upper_bound=200,
        output_upper_bound=100,
        reservation_id="judge-parse",
    )

    settlement = ledger.settle_known_error(
        "judge-parse",
        actual_input_tokens=120,
        actual_output_tokens=20,
        error_signature="schema_parse_failed",
    )

    assert settlement.usage_known is True
    assert settlement.charged_tokens == 140
    assert settlement.released_tokens == 160
    snapshot = ledger.snapshot()
    assert snapshot["revision"] == 2
    assert snapshot["unknown_usage"] is False
    assert snapshot["unknown_charged_tokens"] == 0
    assert snapshot["dispatched_calls"] == 1
    assert snapshot["settled_calls"] == 1
    assert snapshot["error_calls"] == 1
    assert snapshot["categories"]["judge"]["error_calls"] == 1
    assert snapshot["runs"]["run-1"]["error_calls"] == 1
    assert snapshot["fail_closed_reason"] == "model_call_error:schema_parse_failed"


def test_duplicate_or_already_settled_reservation_cannot_be_reused():
    ledger = LiveTokenReservationLedger(
        hard_token_limit=100,
        per_run_token_limit=100,
    )
    ledger.reserve_before_call(
        run_id="run",
        category="judge",
        input_upper_bound=10,
        output_upper_bound=10,
        reservation_id="same",
    )
    with pytest.raises(LiveTokenBudgetError, match="duplicate"):
        ledger.reserve_before_call(
            run_id="run",
            category="judge",
            input_upper_bound=1,
            output_upper_bound=1,
            reservation_id="same",
        )
    ledger.settle_success("same", actual_input_tokens=5, actual_output_tokens=5)
    with pytest.raises(LiveTokenBudgetError, match="already-settled"):
        ledger.settle_success("same", actual_input_tokens=5, actual_output_tokens=5)


def test_invalid_limits_and_zero_reservation_are_rejected_locally():
    with pytest.raises(ValueError, match="cannot exceed"):
        LiveTokenReservationLedger(
            hard_token_limit=100,
            per_run_token_limit=101,
        )
    ledger = LiveTokenReservationLedger(
        hard_token_limit=100,
        per_run_token_limit=100,
    )
    with pytest.raises(ValueError, match="at least one"):
        ledger.reserve_before_call(
            run_id="run",
            category="research",
            input_upper_bound=0,
            output_upper_bound=0,
        )
    assert ledger.snapshot()["revision"] == 0


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.pop("revision"), "invalid keys"),
        (lambda value: value.update(revision=True), "revision"),
        (lambda value: value.update(revision=99), "revision"),
        (
            lambda value: value["categories"]["research"].update(dispatched_calls=2),
            "category dispatched",
        ),
        (
            lambda value: value["runs"]["run-1"].update(settled_calls=0),
            "run settled",
        ),
        (lambda value: value.update(active_calls=True), "active_calls"),
        (lambda value: value.update(unknown_usage=True), "unknown_usage"),
        (lambda value: value.update(unexpected=True), "invalid keys"),
    ],
)
def test_restore_strictly_rejects_malformed_or_inconsistent_snapshots(
    mutation,
    match: str,
):
    ledger = LiveTokenReservationLedger(
        hard_token_limit=1_000,
        per_run_token_limit=800,
    )
    ledger.reserve_before_call(
        run_id="run-1",
        category="research",
        input_upper_bound=100,
        output_upper_bound=50,
        reservation_id="research-1",
    )
    ledger.settle_success(
        "research-1",
        actual_input_tokens=50,
        actual_output_tokens=25,
    )
    malformed = deepcopy(ledger.snapshot())
    mutation(malformed)

    with pytest.raises(ValueError, match=match):
        LiveTokenReservationLedger.from_snapshot(malformed)
