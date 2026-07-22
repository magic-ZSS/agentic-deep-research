"""Deterministic, network-free state helpers for the Phase 7 full matrix.

The paid-step journal is intentionally shared with calibration.  Full runs add
one protocol dimension (main/cold versus cold-warm/warm) by using a private,
validated journal variant key.  Public artifacts continue to expose the real
ablation variant ID.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from open_deep_research.evaluation.calibration_state import CalibrationRunDefinition

FullRunKind = Literal["main", "cold_warm"]
FullRunPhase = Literal["cold", "warm"]


class FullMatrixStateError(ValueError):
    """The committed full-evaluation matrix is incomplete or has drifted."""


def _stable_protocol_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(
        [prefix, *parts],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class FullRunDefinition:
    """One stable public full-evaluation run and its journal projection."""

    case_id: str
    variant_id: str
    repeat: int
    kind: FullRunKind
    phase: FullRunPhase

    @property
    def journal_variant_id(self) -> str:
        """Include the protocol phase in the calibration journal identity."""
        return f"{self.variant_id}:{self.kind}:{self.phase}"

    @property
    def pair_id(self) -> str:
        """Pair the cold and warm executions for one variant/repeat."""
        return _stable_protocol_id(
            "pair", self.case_id, self.variant_id, self.repeat
        )

    @property
    def paired_key(self) -> str:
        """Pair all five variants for one canonical case/repeat."""
        return _stable_protocol_id("paired", self.case_id, self.repeat)

    def to_journal_definition(self) -> CalibrationRunDefinition:
        """Project to the existing crash-safe paid-step journal contract."""
        return CalibrationRunDefinition(
            case_id=self.case_id,
            variant_id=self.journal_variant_id,
            repeat=self.repeat,
        )


def build_full_run_definitions(plan: dict[str, Any]) -> tuple[FullRunDefinition, ...]:
    """Build the exact 45 paired main runs followed by nine warm runs."""
    case_ids = tuple(str(item) for item in plan.get("case_ids", ()))
    variant_ids = tuple(str(item) for item in plan.get("variants", ()))
    repeats = plan.get("repeats")
    if len(case_ids) != 3 or len(set(case_ids)) != 3:
        raise FullMatrixStateError("full matrix requires exactly three unique cases")
    if len(variant_ids) != 5 or len(set(variant_ids)) != 5:
        raise FullMatrixStateError("full matrix requires exactly five unique variants")
    if isinstance(repeats, bool) or repeats != 3:
        raise FullMatrixStateError("full matrix requires exactly three repeats")

    main = tuple(
        FullRunDefinition(
            case_id=case_id,
            variant_id=variant_id,
            repeat=repeat,
            kind="main",
            phase="cold",
        )
        for case_id in case_ids
        for variant_id in variant_ids
        for repeat in range(1, repeats + 1)
    )

    cold_warm = plan.get("cold_warm")
    if not isinstance(cold_warm, dict):
        raise FullMatrixStateError("full matrix requires a cold_warm contract")
    warm_case = str(cold_warm.get("case_id", ""))
    warm_variants = tuple(str(item) for item in cold_warm.get("variants", ()))
    if warm_case not in case_ids:
        raise FullMatrixStateError("cold/warm case is absent from the main matrix")
    if len(warm_variants) != 3 or len(set(warm_variants)) != 3:
        raise FullMatrixStateError("cold/warm requires exactly three unique variants")
    if any(item not in variant_ids for item in warm_variants):
        raise FullMatrixStateError("cold/warm contains an unknown variant")
    expected_reused = len(warm_variants) * repeats
    if cold_warm.get("cold_runs_reused_from_main") != expected_reused:
        raise FullMatrixStateError("cold/warm reused-run count has drifted")
    if cold_warm.get("warm_runs") != expected_reused:
        raise FullMatrixStateError("cold/warm warm-run count has drifted")

    warm = tuple(
        FullRunDefinition(
            case_id=warm_case,
            variant_id=variant_id,
            repeat=repeat,
            kind="cold_warm",
            phase="warm",
        )
        for variant_id in warm_variants
        for repeat in range(1, repeats + 1)
    )
    definitions = (*main, *warm)
    expected_counts = plan.get("research_runs", {})
    if expected_counts.get("paired_main") != len(main) or len(main) != 45:
        raise FullMatrixStateError("paired main run count must be exactly 45")
    if expected_counts.get("additional_warm_runs") != len(warm) or len(warm) != 9:
        raise FullMatrixStateError("additional warm run count must be exactly nine")
    if expected_counts.get("total") != len(definitions) or len(definitions) != 54:
        raise FullMatrixStateError("full run count must be exactly 54")
    journal_keys = {
        (item.case_id, item.journal_variant_id, item.repeat)
        for item in definitions
    }
    if len(journal_keys) != len(definitions):
        raise FullMatrixStateError("full matrix produced duplicate journal identities")
    return definitions


def cold_definition_for(
    definition: FullRunDefinition,
    definitions: tuple[FullRunDefinition, ...],
) -> FullRunDefinition:
    """Return the exact main/cold source for a warm definition."""
    if definition.phase != "warm":
        raise FullMatrixStateError("only warm runs have a cold source")
    matches = [
        item
        for item in definitions
        if item.kind == "main"
        and item.phase == "cold"
        and item.case_id == definition.case_id
        and item.variant_id == definition.variant_id
        and item.repeat == definition.repeat
    ]
    if len(matches) != 1:
        raise FullMatrixStateError("warm run does not have exactly one cold source")
    return matches[0]


__all__ = [
    "FullMatrixStateError",
    "FullRunDefinition",
    "FullRunKind",
    "FullRunPhase",
    "build_full_run_definitions",
    "cold_definition_for",
]
