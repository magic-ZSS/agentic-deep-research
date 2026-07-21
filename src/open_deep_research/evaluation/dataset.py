"""Canonical case and golden-overlay loading with drift checks."""

from __future__ import annotations

from pathlib import Path

from open_deep_research.evaluation.baseline import load_cases
from open_deep_research.evaluation.experiment_models import (
    EvaluationGolden,
    MergedEvaluationCase,
)
from open_deep_research.evaluation.storage import load_jsonl


def merge_evaluation_dataset(
    canonical_path: str | Path,
    overlay_path: str | Path,
    *,
    dataset_version: str,
) -> list[MergedEvaluationCase]:
    """Merge supplemental goldens without allowing duplicated canonical fields."""
    canonical = load_cases(canonical_path)
    goldens = load_jsonl(overlay_path, EvaluationGolden)
    by_id = {case.id: case for case in canonical}
    if len(by_id) != len(canonical):
        raise ValueError("duplicate canonical case id")
    overlay_by_id: dict[str, EvaluationGolden] = {}
    for golden in goldens:
        if golden.case_id not in by_id:
            raise ValueError(f"unknown golden case: {golden.case_id}")
        if golden.case_id in overlay_by_id:
            raise ValueError(f"duplicate golden case: {golden.case_id}")
        if golden.dataset_version != dataset_version:
            raise ValueError(f"dataset version drift for {golden.case_id}")
        overlay_by_id[golden.case_id] = golden
    missing = set(by_id) - set(overlay_by_id)
    if missing:
        raise ValueError(f"missing golden overlays: {sorted(missing)}")
    merged: list[MergedEvaluationCase] = []
    for case in canonical:
        golden = overlay_by_id[case.id]
        expected_tools = golden.expected_tools_by_variant
        merged.append(
            MergedEvaluationCase(
                case_id=case.id,
                difficulty=case.difficulty.value,
                prompt=case.prompt,
                expected_requirements=[
                    item.model_dump(mode="json") for item in case.expected_requirements
                ],
                network_policy=case.network_policy.value,
                budget_class=case.budget_class.value,
                canonical_fixture_version=case.fixture_version,
                dataset_version=dataset_version,
                expected_output=golden.expected_output,
                reference_sources=golden.reference_sources,
                temporal_context=golden.temporal_context,
                memory_setup=golden.memory_setup,
                tags=list(dict.fromkeys([*case.tags, *golden.tags])),
                full_rag_metrics=golden.full_rag_metrics,
                expected_tools_by_variant=expected_tools,
            )
        )
    return merged


def validate_tool_expectations(
    cases: list[MergedEvaluationCase], available_by_variant: dict[str, set[str]]
) -> None:
    """Reject expected tools absent from that exact variant registry."""
    for case in cases:
        for variant, expected in case.expected_tools_by_variant.items():
            if variant not in available_by_variant:
                raise ValueError(f"unknown variant in tool policy: {variant}")
            unknown = set(expected) - available_by_variant[variant]
            if unknown:
                raise ValueError(
                    f"{case.case_id}/{variant} expects unavailable tools: {sorted(unknown)}"
                )

