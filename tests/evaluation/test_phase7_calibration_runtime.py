from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from open_deep_research.evaluation.calibration_runtime import (
    CalibrationConfigurationError,
    build_variant_config,
    inject_governed_runtime,
    runtime_tool_names,
    validate_calibration_matrix,
)
from open_deep_research.evaluation.variants import load_variants
from open_deep_research.knowledge.retrieval.runtime import (
    clear_governed_runtime_cache,
)
from open_deep_research.reporting.pipeline import CitationPipeline

ROOT = Path(__file__).resolve().parents[2]


def plan() -> dict:
    return json.loads(
        (ROOT / "tests/evaluation/full_plan.v1.json").read_text(encoding="utf-8")
    )


def variants():
    return load_variants(ROOT / "tests/evaluation/ablations.v1.json")


def models() -> dict[str, str]:
    return {
        "summarization": "openai:qwen3.7-plus",
        "research": "openai:qwen3.7-plus",
        "compression": "openai:qwen3.7-plus",
        "final_report": "openai:qwen3.7-plus",
        "judge": "openai:qwen3.7-plus",
    }


def test_calibration_matrix_is_exact_and_budget_contract_has_no_drift():
    selected = validate_calibration_matrix(
        plan(), variants(), ["baseline", "citation_validator"]
    )
    assert [item.variant_id for item in selected] == [
        "baseline",
        "citation_validator",
    ]
    with pytest.raises(CalibrationConfigurationError):
        validate_calibration_matrix(plan(), variants(), ["baseline"])


def test_budget_manifest_drift_fails_before_dispatch():
    changed = copy.deepcopy(plan())
    changed["runtime_limits"]["max_react_tool_calls"] = 3
    with pytest.raises(CalibrationConfigurationError, match="drift"):
        validate_calibration_matrix(
            changed, variants(), ["baseline", "citation_validator"]
        )
    changed = copy.deepcopy(plan())
    changed["runtime_limits"]["provider_max_retries"] = 1
    with pytest.raises(CalibrationConfigurationError, match="exactly zero"):
        validate_calibration_matrix(
            changed, variants(), ["baseline", "citation_validator"]
        )


def test_variant_config_is_path_isolated_and_contains_no_credentials(tmp_path):
    variant = variants()[-1]
    config = build_variant_config(
        plan=plan(),
        variant=variant,
        models=models(),
        run_id="stable-run",
        runtime_root=tmp_path,
        experiment_id="experiment",
    )
    payload = json.dumps(config, default=str)
    assert "API_KEY" not in payload
    assert "secret" not in payload
    assert config["configurable"]["max_researcher_iterations"] == 4
    assert config["configurable"]["max_react_tool_calls"] == 4
    assert config["configurable"]["max_retries"] == 0
    assert config["configurable"]["_evaluation_final_report_max_attempts"] == 1
    assert str(tmp_path.resolve()) in config["configurable"]["knowledge_db_path"]


@pytest.mark.asyncio
async def test_real_registry_and_citation_pipeline_are_injected(tmp_path):
    clear_governed_runtime_cache()
    selected = validate_calibration_matrix(
        plan(), variants(), ["baseline", "citation_validator"]
    )
    baseline = build_variant_config(
        plan=plan(),
        variant=selected[0],
        models=models(),
        run_id="baseline-run",
        runtime_root=tmp_path,
        experiment_id="experiment",
    )
    governed = build_variant_config(
        plan=plan(),
        variant=selected[1],
        models=models(),
        run_id="governed-run",
        runtime_root=tmp_path,
        experiment_id="experiment",
    )
    assert await runtime_tool_names(baseline) == [
        "ResearchComplete",
        "think_tool",
        "tavily_search",
    ]
    assert await runtime_tool_names(governed) == [
        "ResearchComplete",
        "think_tool",
        "governed_retrieval",
    ]
    runtime = inject_governed_runtime(governed)
    assert runtime is not None
    assert isinstance(governed["configurable"]["citation_pipeline"], CitationPipeline)
    assert runtime.orchestrator.run_store is not None
    clear_governed_runtime_cache()
