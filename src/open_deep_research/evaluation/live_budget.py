"""Thread-safe, fail-closed token reservations for paid evaluation calls.

The ledger is deliberately independent from the Phase 7 runner.  A caller must
reserve a conservative input/output upper bound *before* dispatching every
model call, persist :meth:`LiveTokenReservationLedger.snapshot`, and then
settle the reservation.  A failed call is charged at its full reservation
because its provider-side usage is not trustworthy.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


class LiveTokenBudgetError(RuntimeError):
    """Base error for a rejected or unsafe paid-call reservation."""


class LiveTokenBudgetExceeded(LiveTokenBudgetError):
    """Raised before dispatch when a reservation would exceed a ceiling."""


class LiveTokenBudgetFailClosed(LiveTokenBudgetError):
    """Raised when prior usage uncertainty prohibits another paid call."""


class TokenUsageCategory(StrEnum):
    """Cost attribution used by Phase 7 calibration and full evaluation."""

    RESEARCH = "research"
    JUDGE = "judge"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class TokenReservation:
    """One conservative upper bound acquired before a model call."""

    reservation_id: str
    run_id: str
    category: TokenUsageCategory
    input_upper_bound: int
    output_upper_bound: int

    @property
    def reserved_tokens(self) -> int:
        """Return the total amount held against both ceilings."""
        return self.input_upper_bound + self.output_upper_bound

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "reservation_id": self.reservation_id,
            "run_id": self.run_id,
            "category": self.category.value,
            "input_upper_bound": self.input_upper_bound,
            "output_upper_bound": self.output_upper_bound,
            "reserved_tokens": self.reserved_tokens,
        }


@dataclass(frozen=True, slots=True)
class TokenSettlement:
    """Accounting result after a reservation reaches a terminal state."""

    reservation_id: str
    charged_tokens: int
    released_tokens: int
    usage_known: bool
    fail_closed: bool


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    parsed = _non_negative_int(value, field=field)
    if parsed == 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _strict_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    expected: set[str],
    field: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(str(item) for item in actual - expected)
        raise ValueError(
            f"{field} has invalid keys; missing={missing}, unexpected={unexpected}"
        )


class LiveTokenReservationLedger:
    """Reserve and settle paid-call tokens under experiment and run ceilings.

    All state transitions are protected by one re-entrant lock.  Reservations
    therefore include other concurrent in-flight calls when checking limits.
    A successful settlement charges measured usage and releases the unused
    upper bound.  An error charges the complete reservation, records unknown
    usage, and permanently fails the ledger closed.
    """

    SCHEMA_VERSION = "1.1"

    _SNAPSHOT_KEYS = {
        "schema_version",
        "revision",
        "hard_token_limit",
        "per_run_token_limit",
        "committed_tokens",
        "active_reserved_tokens",
        "accounted_tokens",
        "remaining_tokens",
        "actual_input_tokens",
        "actual_output_tokens",
        "unknown_charged_tokens",
        "unknown_usage",
        "fail_closed",
        "fail_closed_reason",
        "error_count",
        "dispatched_calls",
        "settled_calls",
        "error_calls",
        "active_calls",
        "categories",
        "runs",
        "active_reservations",
    }
    _CATEGORY_KEYS = {
        "committed_tokens",
        "actual_input_tokens",
        "actual_output_tokens",
        "active_reserved_tokens",
        "dispatched_calls",
        "settled_calls",
        "error_calls",
        "active_calls",
    }
    _RUN_KEYS = {
        "committed_tokens",
        "active_reserved_tokens",
        "accounted_tokens",
        "remaining_tokens",
        "dispatched_calls",
        "settled_calls",
        "error_calls",
        "active_calls",
    }
    _RESERVATION_KEYS = {
        "reservation_id",
        "run_id",
        "category",
        "input_upper_bound",
        "output_upper_bound",
        "reserved_tokens",
    }

    def __init__(self, *, hard_token_limit: int, per_run_token_limit: int) -> None:
        """Create an empty ledger with immutable experiment and run ceilings."""
        self.hard_token_limit = _positive_int(
            hard_token_limit, field="hard_token_limit"
        )
        self.per_run_token_limit = _positive_int(
            per_run_token_limit, field="per_run_token_limit"
        )
        if self.per_run_token_limit > self.hard_token_limit:
            raise ValueError("per_run_token_limit cannot exceed hard_token_limit")

        self._lock = threading.RLock()
        self._revision = 0
        self._active: dict[str, TokenReservation] = {}
        self._committed_tokens = 0
        self._actual_input_tokens = 0
        self._actual_output_tokens = 0
        self._unknown_charged_tokens = 0
        self._committed_by_category = {category: 0 for category in TokenUsageCategory}
        self._actual_input_by_category = {
            category: 0 for category in TokenUsageCategory
        }
        self._actual_output_by_category = {
            category: 0 for category in TokenUsageCategory
        }
        self._committed_by_run: dict[str, int] = {}
        self._dispatched_calls = 0
        self._settled_calls = 0
        self._error_count = 0
        self._dispatched_by_category = {category: 0 for category in TokenUsageCategory}
        self._settled_by_category = {category: 0 for category in TokenUsageCategory}
        self._errors_by_category = {category: 0 for category in TokenUsageCategory}
        self._dispatched_by_run: dict[str, int] = {}
        self._settled_by_run: dict[str, int] = {}
        self._errors_by_run: dict[str, int] = {}
        self._unknown_usage = False
        self._fail_closed = False
        self._fail_closed_reason: str | None = None

    def reserve_before_call(
        self,
        *,
        run_id: str,
        category: TokenUsageCategory | str,
        input_upper_bound: int,
        output_upper_bound: int,
        reservation_id: str | None = None,
    ) -> TokenReservation:
        """Atomically reserve an upper bound before any external dispatch."""
        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id must be non-empty")
        normalized_category = TokenUsageCategory(category)
        input_limit = _non_negative_int(input_upper_bound, field="input_upper_bound")
        output_limit = _non_negative_int(output_upper_bound, field="output_upper_bound")
        if input_limit + output_limit == 0:
            raise ValueError("a reservation must hold at least one token")
        identifier = (reservation_id or uuid4().hex).strip()
        if not identifier:
            raise ValueError("reservation_id must be non-empty")

        reservation = TokenReservation(
            reservation_id=identifier,
            run_id=normalized_run_id,
            category=normalized_category,
            input_upper_bound=input_limit,
            output_upper_bound=output_limit,
        )
        with self._lock:
            if self._fail_closed:
                raise LiveTokenBudgetFailClosed(
                    "token ledger is fail-closed: "
                    + (self._fail_closed_reason or "unknown usage")
                )
            if identifier in self._active:
                raise LiveTokenBudgetError(
                    f"duplicate active reservation_id: {identifier}"
                )

            active_total = sum(item.reserved_tokens for item in self._active.values())
            prospective_total = (
                self._committed_tokens + active_total + reservation.reserved_tokens
            )
            if prospective_total > self.hard_token_limit:
                raise LiveTokenBudgetExceeded(
                    "experiment hard token ceiling would be exceeded before call"
                )

            active_for_run = sum(
                item.reserved_tokens
                for item in self._active.values()
                if item.run_id == normalized_run_id
            )
            prospective_run = (
                self._committed_by_run.get(normalized_run_id, 0)
                + active_for_run
                + reservation.reserved_tokens
            )
            if prospective_run > self.per_run_token_limit:
                raise LiveTokenBudgetExceeded(
                    f"per-run token ceiling would be exceeded before call: {normalized_run_id}"
                )

            self._active[identifier] = reservation
            self._record_dispatch(reservation)
            self._advance_revision()
            return reservation

    def settle_success(
        self,
        reservation_id: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
    ) -> TokenSettlement:
        """Charge known actual usage and release the unused reservation."""
        actual_input = _non_negative_int(
            actual_input_tokens, field="actual_input_tokens"
        )
        actual_output = _non_negative_int(
            actual_output_tokens, field="actual_output_tokens"
        )
        actual_total = actual_input + actual_output
        with self._lock:
            reservation = self._require_active(reservation_id)
            if (
                actual_input > reservation.input_upper_bound
                or actual_output > reservation.output_upper_bound
            ):
                # The caller's upper bound was not conservative.  Record the
                # provider-reported actual usage, then prohibit further calls.
                self._active.pop(reservation_id)
                self._charge(
                    reservation,
                    total_tokens=actual_total,
                    actual_input_tokens=actual_input,
                    actual_output_tokens=actual_output,
                )
                self._record_settlement(reservation, error=True)
                self._fail_closed = True
                self._fail_closed_reason = "actual_usage_exceeded_reservation"
                self._advance_revision()
                raise LiveTokenBudgetFailClosed(
                    "actual usage exceeded its pre-call reservation"
                )

            self._active.pop(reservation_id)
            self._charge(
                reservation,
                total_tokens=actual_total,
                actual_input_tokens=actual_input,
                actual_output_tokens=actual_output,
            )
            self._record_settlement(reservation, error=False)
            self._advance_revision()
            return TokenSettlement(
                reservation_id=reservation_id,
                charged_tokens=actual_total,
                released_tokens=reservation.reserved_tokens - actual_total,
                usage_known=True,
                fail_closed=self._fail_closed,
            )

    def settle_error(
        self,
        reservation_id: str,
        *,
        error_signature: str,
    ) -> TokenSettlement:
        """Charge the full upper bound and fail closed after an errored call."""
        signature = error_signature.strip()
        if not signature:
            raise ValueError("error_signature must be non-empty")
        with self._lock:
            reservation = self._require_active(reservation_id)
            self._active.pop(reservation_id)
            self._charge(
                reservation,
                total_tokens=reservation.reserved_tokens,
                actual_input_tokens=0,
                actual_output_tokens=0,
            )
            self._unknown_charged_tokens += reservation.reserved_tokens
            self._record_settlement(reservation, error=True)
            self._unknown_usage = True
            self._fail_closed = True
            self._fail_closed_reason = f"model_call_error:{signature}"
            self._advance_revision()
            return TokenSettlement(
                reservation_id=reservation_id,
                charged_tokens=reservation.reserved_tokens,
                released_tokens=0,
                usage_known=False,
                fail_closed=True,
            )

    def settle_known_error(
        self,
        reservation_id: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
        error_signature: str,
    ) -> TokenSettlement:
        """Charge trustworthy usage for an errored call and fail closed."""
        signature = error_signature.strip()
        if not signature:
            raise ValueError("error_signature must be non-empty")
        actual_input = _non_negative_int(
            actual_input_tokens, field="actual_input_tokens"
        )
        actual_output = _non_negative_int(
            actual_output_tokens, field="actual_output_tokens"
        )
        actual_total = actual_input + actual_output
        with self._lock:
            reservation = self._require_active(reservation_id)
            if (
                actual_input > reservation.input_upper_bound
                or actual_output > reservation.output_upper_bound
            ):
                fail_closed_reason = "actual_usage_exceeded_reservation"
            else:
                fail_closed_reason = f"model_call_error:{signature}"
            self._active.pop(reservation_id)
            self._charge(
                reservation,
                total_tokens=actual_total,
                actual_input_tokens=actual_input,
                actual_output_tokens=actual_output,
            )
            self._record_settlement(reservation, error=True)
            self._fail_closed = True
            self._fail_closed_reason = fail_closed_reason
            self._advance_revision()
            return TokenSettlement(
                reservation_id=reservation_id,
                charged_tokens=actual_total,
                released_tokens=max(0, reservation.reserved_tokens - actual_total),
                usage_known=True,
                fail_closed=True,
            )

    def snapshot(self) -> dict[str, Any]:
        """Return a stable JSON-serializable snapshot for atomic persistence."""
        with self._lock:
            return self._snapshot_locked()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, Any],
    ) -> LiveTokenReservationLedger:
        """Restore a snapshot, conservatively charging interrupted calls.

        An active reservation persisted before a crash represents a call whose
        provider-side outcome cannot be proven.  Each such hold is charged in
        full and the restored ledger is failed closed.
        """
        if snapshot.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported live token budget snapshot version")
        _require_exact_keys(
            snapshot,
            expected=cls._SNAPSHOT_KEYS,
            field="snapshot",
        )
        ledger = cls(
            hard_token_limit=_positive_int(
                snapshot.get("hard_token_limit"), field="hard_token_limit"
            ),
            per_run_token_limit=_positive_int(
                snapshot.get("per_run_token_limit"), field="per_run_token_limit"
            ),
        )
        with ledger._lock:
            ledger._revision = _non_negative_int(
                snapshot.get("revision"), field="revision"
            )
            ledger._committed_tokens = _non_negative_int(
                snapshot.get("committed_tokens"), field="committed_tokens"
            )
            ledger._actual_input_tokens = _non_negative_int(
                snapshot.get("actual_input_tokens"), field="actual_input_tokens"
            )
            ledger._actual_output_tokens = _non_negative_int(
                snapshot.get("actual_output_tokens"), field="actual_output_tokens"
            )
            ledger._unknown_charged_tokens = _non_negative_int(
                snapshot.get("unknown_charged_tokens"),
                field="unknown_charged_tokens",
            )
            ledger._dispatched_calls = _non_negative_int(
                snapshot.get("dispatched_calls"), field="dispatched_calls"
            )
            ledger._settled_calls = _non_negative_int(
                snapshot.get("settled_calls"), field="settled_calls"
            )
            ledger._error_count = _non_negative_int(
                snapshot.get("error_calls"), field="error_calls"
            )
            if (
                _non_negative_int(snapshot.get("error_count"), field="error_count")
                != ledger._error_count
            ):
                raise ValueError("error_count and error_calls are inconsistent")
            ledger._unknown_usage = _strict_bool(
                snapshot.get("unknown_usage"), field="unknown_usage"
            )
            ledger._fail_closed = _strict_bool(
                snapshot.get("fail_closed"), field="fail_closed"
            )
            reason = snapshot.get("fail_closed_reason")
            if reason is not None and (
                not isinstance(reason, str) or not reason.strip()
            ):
                raise ValueError(
                    "fail_closed_reason must be a non-empty string or null"
                )
            if isinstance(reason, str) and reason != reason.strip():
                raise ValueError("fail_closed_reason must not contain outer whitespace")
            ledger._fail_closed_reason = reason

            categories = snapshot.get("categories")
            if not isinstance(categories, Mapping):
                raise ValueError("categories must be an object")
            if set(categories) != {category.value for category in TokenUsageCategory}:
                raise ValueError(
                    "categories must contain exactly the supported categories"
                )
            for category in TokenUsageCategory:
                payload = categories.get(category.value)
                if not isinstance(payload, Mapping):
                    raise ValueError(f"missing category snapshot: {category.value}")
                _require_exact_keys(
                    payload,
                    expected=cls._CATEGORY_KEYS,
                    field=f"categories.{category.value}",
                )
                ledger._committed_by_category[category] = _non_negative_int(
                    payload.get("committed_tokens"),
                    field=f"categories.{category.value}.committed_tokens",
                )
                ledger._actual_input_by_category[category] = _non_negative_int(
                    payload.get("actual_input_tokens"),
                    field=f"categories.{category.value}.actual_input_tokens",
                )
                ledger._actual_output_by_category[category] = _non_negative_int(
                    payload.get("actual_output_tokens"),
                    field=f"categories.{category.value}.actual_output_tokens",
                )
                ledger._dispatched_by_category[category] = _non_negative_int(
                    payload.get("dispatched_calls"),
                    field=f"categories.{category.value}.dispatched_calls",
                )
                ledger._settled_by_category[category] = _non_negative_int(
                    payload.get("settled_calls"),
                    field=f"categories.{category.value}.settled_calls",
                )
                ledger._errors_by_category[category] = _non_negative_int(
                    payload.get("error_calls"),
                    field=f"categories.{category.value}.error_calls",
                )

            runs = snapshot.get("runs")
            if not isinstance(runs, Mapping):
                raise ValueError("runs must be an object")
            for run_id, payload in runs.items():
                if not isinstance(run_id, str) or not run_id.strip():
                    raise ValueError("snapshot run IDs must be non-empty strings")
                if run_id != run_id.strip():
                    raise ValueError(
                        "snapshot run IDs must not contain outer whitespace"
                    )
                if not isinstance(payload, Mapping):
                    raise ValueError(f"invalid run snapshot: {run_id}")
                _require_exact_keys(
                    payload,
                    expected=cls._RUN_KEYS,
                    field=f"runs.{run_id}",
                )
                ledger._committed_by_run[run_id] = _non_negative_int(
                    payload.get("committed_tokens"),
                    field=f"runs.{run_id}.committed_tokens",
                )
                ledger._dispatched_by_run[run_id] = _non_negative_int(
                    payload.get("dispatched_calls"),
                    field=f"runs.{run_id}.dispatched_calls",
                )
                ledger._settled_by_run[run_id] = _non_negative_int(
                    payload.get("settled_calls"),
                    field=f"runs.{run_id}.settled_calls",
                )
                ledger._errors_by_run[run_id] = _non_negative_int(
                    payload.get("error_calls"),
                    field=f"runs.{run_id}.error_calls",
                )

            active = snapshot.get("active_reservations")
            if not isinstance(active, list):
                raise ValueError("active_reservations must be an array")
            for payload in active:
                if not isinstance(payload, Mapping):
                    raise ValueError("active reservation must be an object")
                _require_exact_keys(
                    payload,
                    expected=cls._RESERVATION_KEYS,
                    field="active reservation",
                )
                identifier = payload.get("reservation_id")
                run_id = payload.get("run_id")
                if not isinstance(identifier, str) or not identifier.strip():
                    raise ValueError("reservation_id must be non-empty")
                if identifier != identifier.strip():
                    raise ValueError("reservation_id must not contain outer whitespace")
                if not isinstance(run_id, str) or not run_id.strip():
                    raise ValueError("reservation run_id must be non-empty")
                if run_id != run_id.strip():
                    raise ValueError(
                        "reservation run_id must not contain outer whitespace"
                    )
                if identifier in ledger._active:
                    raise ValueError(f"duplicate reservation_id: {identifier}")
                raw_category = payload.get("category")
                if not isinstance(raw_category, str):
                    raise ValueError("reservation category must be a string")
                reservation = TokenReservation(
                    reservation_id=identifier,
                    run_id=run_id,
                    category=TokenUsageCategory(raw_category),
                    input_upper_bound=_non_negative_int(
                        payload.get("input_upper_bound"),
                        field="input_upper_bound",
                    ),
                    output_upper_bound=_non_negative_int(
                        payload.get("output_upper_bound"),
                        field="output_upper_bound",
                    ),
                )
                if reservation.reserved_tokens == 0:
                    raise ValueError("active reservation cannot be empty")
                if (
                    _non_negative_int(
                        payload.get("reserved_tokens"), field="reserved_tokens"
                    )
                    != reservation.reserved_tokens
                ):
                    raise ValueError("active reservation total is inconsistent")
                ledger._active[identifier] = reservation

            ledger._validate_restored_totals(snapshot)
            if ledger._active:
                interrupted = list(ledger._active.values())
                ledger._active.clear()
                for reservation in interrupted:
                    ledger._charge(
                        reservation,
                        total_tokens=reservation.reserved_tokens,
                        actual_input_tokens=0,
                        actual_output_tokens=0,
                    )
                    ledger._unknown_charged_tokens += reservation.reserved_tokens
                    ledger._record_settlement(reservation, error=True)
                ledger._unknown_usage = True
                ledger._fail_closed = True
                ledger._fail_closed_reason = "restored_with_unsettled_reservations"
                ledger._advance_revision()
            return ledger

    def _require_active(self, reservation_id: str) -> TokenReservation:
        try:
            return self._active[reservation_id]
        except KeyError as exc:
            raise LiveTokenBudgetError(
                f"unknown or already-settled reservation_id: {reservation_id}"
            ) from exc

    def _charge(
        self,
        reservation: TokenReservation,
        *,
        total_tokens: int,
        actual_input_tokens: int,
        actual_output_tokens: int,
    ) -> None:
        self._committed_tokens += total_tokens
        self._actual_input_tokens += actual_input_tokens
        self._actual_output_tokens += actual_output_tokens
        self._committed_by_category[reservation.category] += total_tokens
        self._actual_input_by_category[reservation.category] += actual_input_tokens
        self._actual_output_by_category[reservation.category] += actual_output_tokens
        self._committed_by_run[reservation.run_id] = (
            self._committed_by_run.get(reservation.run_id, 0) + total_tokens
        )

    def _record_dispatch(self, reservation: TokenReservation) -> None:
        self._dispatched_calls += 1
        self._dispatched_by_category[reservation.category] += 1
        self._dispatched_by_run[reservation.run_id] = (
            self._dispatched_by_run.get(reservation.run_id, 0) + 1
        )

    def _record_settlement(
        self,
        reservation: TokenReservation,
        *,
        error: bool,
    ) -> None:
        self._settled_calls += 1
        self._settled_by_category[reservation.category] += 1
        self._settled_by_run[reservation.run_id] = (
            self._settled_by_run.get(reservation.run_id, 0) + 1
        )
        if error:
            self._error_count += 1
            self._errors_by_category[reservation.category] += 1
            self._errors_by_run[reservation.run_id] = (
                self._errors_by_run.get(reservation.run_id, 0) + 1
            )

    def _advance_revision(self) -> None:
        self._revision += 1

    def _snapshot_locked(self) -> dict[str, Any]:
        active_total = sum(item.reserved_tokens for item in self._active.values())
        active_by_category = {
            category: sum(
                item.reserved_tokens
                for item in self._active.values()
                if item.category is category
            )
            for category in TokenUsageCategory
        }
        active_calls_by_category = {
            category: sum(
                1 for item in self._active.values() if item.category is category
            )
            for category in TokenUsageCategory
        }
        run_ids = (
            set(self._committed_by_run)
            | set(self._dispatched_by_run)
            | set(self._settled_by_run)
            | set(self._errors_by_run)
            | {item.run_id for item in self._active.values()}
        )
        runs = {}
        for run_id in sorted(run_ids):
            active_for_run = sum(
                item.reserved_tokens
                for item in self._active.values()
                if item.run_id == run_id
            )
            committed = self._committed_by_run.get(run_id, 0)
            runs[run_id] = {
                "committed_tokens": committed,
                "active_reserved_tokens": active_for_run,
                "accounted_tokens": committed + active_for_run,
                "remaining_tokens": max(
                    0, self.per_run_token_limit - committed - active_for_run
                ),
                "dispatched_calls": self._dispatched_by_run.get(run_id, 0),
                "settled_calls": self._settled_by_run.get(run_id, 0),
                "error_calls": self._errors_by_run.get(run_id, 0),
                "active_calls": sum(
                    1 for item in self._active.values() if item.run_id == run_id
                ),
            }
        return {
            "schema_version": self.SCHEMA_VERSION,
            "revision": self._revision,
            "hard_token_limit": self.hard_token_limit,
            "per_run_token_limit": self.per_run_token_limit,
            "committed_tokens": self._committed_tokens,
            "active_reserved_tokens": active_total,
            "accounted_tokens": self._committed_tokens + active_total,
            "remaining_tokens": max(
                0, self.hard_token_limit - self._committed_tokens - active_total
            ),
            "actual_input_tokens": self._actual_input_tokens,
            "actual_output_tokens": self._actual_output_tokens,
            "unknown_charged_tokens": self._unknown_charged_tokens,
            "unknown_usage": self._unknown_usage,
            "fail_closed": self._fail_closed,
            "fail_closed_reason": self._fail_closed_reason,
            "error_count": self._error_count,
            "dispatched_calls": self._dispatched_calls,
            "settled_calls": self._settled_calls,
            "error_calls": self._error_count,
            "active_calls": len(self._active),
            "categories": {
                category.value: {
                    "committed_tokens": self._committed_by_category[category],
                    "actual_input_tokens": self._actual_input_by_category[category],
                    "actual_output_tokens": self._actual_output_by_category[category],
                    "active_reserved_tokens": active_by_category[category],
                    "dispatched_calls": self._dispatched_by_category[category],
                    "settled_calls": self._settled_by_category[category],
                    "error_calls": self._errors_by_category[category],
                    "active_calls": active_calls_by_category[category],
                }
                for category in TokenUsageCategory
            },
            "runs": runs,
            "active_reservations": [
                item.to_dict()
                for item in sorted(
                    self._active.values(), key=lambda value: value.reservation_id
                )
            ],
        }

    def _validate_restored_totals(self, snapshot: Mapping[str, Any]) -> None:
        if sum(self._committed_by_category.values()) != self._committed_tokens:
            raise ValueError("category committed totals are inconsistent")
        if sum(self._committed_by_run.values()) != self._committed_tokens:
            raise ValueError("run committed totals are inconsistent")
        if self._actual_input_tokens != sum(self._actual_input_by_category.values()):
            raise ValueError("category input totals are inconsistent")
        if self._actual_output_tokens != sum(self._actual_output_by_category.values()):
            raise ValueError("category output totals are inconsistent")
        if sum(self._dispatched_by_category.values()) != self._dispatched_calls:
            raise ValueError("category dispatched call totals are inconsistent")
        if sum(self._settled_by_category.values()) != self._settled_calls:
            raise ValueError("category settled call totals are inconsistent")
        if sum(self._errors_by_category.values()) != self._error_count:
            raise ValueError("category error call totals are inconsistent")
        if sum(self._dispatched_by_run.values()) != self._dispatched_calls:
            raise ValueError("run dispatched call totals are inconsistent")
        if sum(self._settled_by_run.values()) != self._settled_calls:
            raise ValueError("run settled call totals are inconsistent")
        if sum(self._errors_by_run.values()) != self._error_count:
            raise ValueError("run error call totals are inconsistent")
        if self._settled_calls > self._dispatched_calls:
            raise ValueError("settled calls cannot exceed dispatched calls")
        if self._error_count > self._settled_calls:
            raise ValueError("error calls cannot exceed settled calls")
        if self._revision < self._dispatched_calls:
            raise ValueError("revision cannot precede dispatched calls")
        if self._revision > self._dispatched_calls + self._settled_calls:
            raise ValueError("revision exceeds possible ledger transitions")
        active_total = sum(item.reserved_tokens for item in self._active.values())
        active_calls = len(self._active)
        if (
            _non_negative_int(
                snapshot.get("active_reserved_tokens"),
                field="active_reserved_tokens",
            )
            != active_total
        ):
            raise ValueError("active reservation total is inconsistent")
        if (
            _non_negative_int(snapshot.get("active_calls"), field="active_calls")
            != active_calls
        ):
            raise ValueError("active call count is inconsistent")
        if self._dispatched_calls - self._settled_calls != active_calls:
            raise ValueError("dispatched, settled, and active calls are inconsistent")
        accounted = self._committed_tokens + active_total
        if (
            _non_negative_int(
                snapshot.get("accounted_tokens"), field="accounted_tokens"
            )
            != accounted
        ):
            raise ValueError("snapshot accounted total is inconsistent")
        if _non_negative_int(
            snapshot.get("remaining_tokens"), field="remaining_tokens"
        ) != max(0, self.hard_token_limit - accounted):
            raise ValueError("snapshot remaining total is inconsistent")
        if accounted > self.hard_token_limit:
            raise ValueError("snapshot exceeds experiment hard token ceiling")
        if self._unknown_charged_tokens > self._committed_tokens:
            raise ValueError("unknown charged tokens exceed committed tokens")
        if self._unknown_usage != (self._unknown_charged_tokens > 0):
            raise ValueError(
                "unknown_usage and unknown charged tokens are inconsistent"
            )
        if (
            self._actual_input_tokens + self._actual_output_tokens
            > self._committed_tokens
        ):
            raise ValueError("actual token totals exceed committed tokens")
        if self._fail_closed != (self._fail_closed_reason is not None):
            raise ValueError("fail_closed and fail_closed_reason are inconsistent")
        if self._fail_closed != (self._error_count > 0):
            raise ValueError("fail_closed and error calls are inconsistent")
        active_by_category = {
            category: [
                item for item in self._active.values() if item.category is category
            ]
            for category in TokenUsageCategory
        }
        categories = snapshot["categories"]
        for category in TokenUsageCategory:
            payload = categories[category.value]
            active_items = active_by_category[category]
            if _non_negative_int(
                payload.get("active_reserved_tokens"),
                field=f"categories.{category.value}.active_reserved_tokens",
            ) != sum(item.reserved_tokens for item in active_items):
                raise ValueError(
                    f"category active reservation total is inconsistent: {category.value}"
                )
            if _non_negative_int(
                payload.get("active_calls"),
                field=f"categories.{category.value}.active_calls",
            ) != len(active_items):
                raise ValueError(
                    f"category active call count is inconsistent: {category.value}"
                )
            if self._dispatched_by_category[category] - self._settled_by_category[
                category
            ] != len(active_items):
                raise ValueError(
                    f"category call totals are inconsistent: {category.value}"
                )
            if self._errors_by_category[category] > self._settled_by_category[category]:
                raise ValueError(
                    f"category error calls exceed settled calls: {category.value}"
                )
            if (
                self._actual_input_by_category[category]
                + self._actual_output_by_category[category]
                > self._committed_by_category[category]
            ):
                raise ValueError(
                    f"category actual tokens exceed committed tokens: {category.value}"
                )
        runs = snapshot["runs"]
        for run_id in (
            set(self._committed_by_run)
            | {item.run_id for item in self._active.values()}
            | set(self._dispatched_by_run)
            | set(self._settled_by_run)
            | set(self._errors_by_run)
        ):
            active_for_run = sum(
                item.reserved_tokens
                for item in self._active.values()
                if item.run_id == run_id
            )
            if (
                self._committed_by_run.get(run_id, 0) + active_for_run
                > self.per_run_token_limit
            ):
                raise ValueError(f"snapshot exceeds per-run ceiling: {run_id}")
            active_calls_for_run = sum(
                1 for item in self._active.values() if item.run_id == run_id
            )
            payload = runs.get(run_id)
            if not isinstance(payload, Mapping):
                raise ValueError(f"missing run snapshot: {run_id}")
            committed_for_run = self._committed_by_run.get(run_id, 0)
            accounted_for_run = committed_for_run + active_for_run
            if (
                _non_negative_int(
                    payload.get("active_reserved_tokens"),
                    field=f"runs.{run_id}.active_reserved_tokens",
                )
                != active_for_run
            ):
                raise ValueError(
                    f"run active reservation total is inconsistent: {run_id}"
                )
            if (
                _non_negative_int(
                    payload.get("accounted_tokens"),
                    field=f"runs.{run_id}.accounted_tokens",
                )
                != accounted_for_run
            ):
                raise ValueError(f"run accounted total is inconsistent: {run_id}")
            if _non_negative_int(
                payload.get("remaining_tokens"),
                field=f"runs.{run_id}.remaining_tokens",
            ) != max(0, self.per_run_token_limit - accounted_for_run):
                raise ValueError(f"run remaining total is inconsistent: {run_id}")
            if (
                _non_negative_int(
                    payload.get("active_calls"),
                    field=f"runs.{run_id}.active_calls",
                )
                != active_calls_for_run
            ):
                raise ValueError(f"run active call count is inconsistent: {run_id}")
            if (
                self._dispatched_by_run.get(run_id, 0)
                - self._settled_by_run.get(run_id, 0)
                != active_calls_for_run
            ):
                raise ValueError(f"run call totals are inconsistent: {run_id}")
            if self._errors_by_run.get(run_id, 0) > self._settled_by_run.get(run_id, 0):
                raise ValueError(f"run error calls exceed settled calls: {run_id}")
