import asyncio
import json
import sys
import types
import uuid
from pathlib import Path

import pytest

# These offline registry tests do not exercise UUIDv7 semantics. Keep the
# compatibility shim local so the paid-environment import smoke remains strict.
try:
    import uuid_utils  # noqa: F401
except ImportError:
    uuid_fallback = types.ModuleType("uuid_utils")
    uuid_compat = types.ModuleType("uuid_utils.compat")
    uuid_fallback.uuid7 = uuid.uuid4
    uuid_fallback.compat = uuid_compat
    uuid_compat.uuid7 = uuid.uuid4
    sys.modules["uuid_utils"] = uuid_fallback
    sys.modules["uuid_utils.compat"] = uuid_compat

from open_deep_research.evaluation.dataset import (
    merge_evaluation_dataset,
    validate_tool_expectations,
)
from open_deep_research.evaluation.experiment_models import EvaluationGolden
from open_deep_research.evaluation.variants import (
    load_variants,
    validate_registry_snapshots,
)
from open_deep_research.utils import get_all_tools

ROOT_CASES = "tests/baseline/cases.jsonl"
OVERLAY = "tests/evaluation/goldens.v1.jsonl"
VARIANTS = "tests/evaluation/ablations.v1.json"


def test_canonical_dataset_is_only_prompt_and_requirement_source():
    raw = [
        json.loads(line)
        for line in Path(OVERLAY).read_text(encoding="utf-8").splitlines()
    ]
    assert len(raw) == 9
    assert all("prompt" not in item and "input" not in item for item in raw)
    assert all("expected_requirements" not in item for item in raw)
    merged = merge_evaluation_dataset(ROOT_CASES, OVERLAY, dataset_version="v1")
    assert {item.difficulty for item in merged} == {"simple", "medium", "complex"}
    assert all(sum(item.difficulty == level for item in merged) == 3 for level in ("simple", "medium", "complex"))


def test_overlay_rejects_unknown_duplicate_field_and_version_drift(tmp_path):
    bad = tmp_path / "bad.jsonl"
    payload = EvaluationGolden(
        case_id="simple-001", dataset_version="other"
    ).model_dump(mode="json")
    bad.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="version drift"):
        merge_evaluation_dataset(ROOT_CASES, bad, dataset_version="v1")
    payload["prompt"] = "forbidden duplicate"
    with pytest.raises(ValueError):
        EvaluationGolden.model_validate(payload)


def test_fixed_ablation_matrix_is_fair_and_matches_runtime_registry():
    variants = load_variants(VARIANTS)
    assert [item.variant_id for item in variants] == [
        "baseline", "paperqa", "agentic_rag", "memory", "citation_validator"
    ]
    asyncio.run(validate_registry_snapshots(variants, get_all_tools))


def test_variant_specific_tool_policy_rejects_cross_variant_expectation():
    cases = merge_evaluation_dataset(ROOT_CASES, OVERLAY, dataset_version="v1")
    variants = load_variants(VARIANTS)
    available = {item.variant_id: set(item.available_tools) for item in variants}
    validate_tool_expectations(cases, available)
    changed = cases[0].model_copy(
        update={"expected_tools_by_variant": {"baseline": ["governed_retrieval"]}}
    )
    with pytest.raises(ValueError, match="unavailable"):
        validate_tool_expectations([changed], available)


def test_unfair_budget_drift_is_rejected(tmp_path):
    payload = json.loads(Path(VARIANTS).read_text(encoding="utf-8"))
    payload["variants"][1]["budget"]["timeout_seconds"] = 1
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        load_variants(path)
