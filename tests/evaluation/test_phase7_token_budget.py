import pytest

from open_deep_research.evaluation.budget import (
    EvaluationBudgetError,
    FailureCircuitBreaker,
    RetryBudgetLedger,
    TokenBudgetLedger,
    load_full_plan,
    project_total_tokens,
    resolve_models,
    validate_requested_budget,
)

PLAN = "tests/evaluation/full_plan.v1.json"


def test_qwen_plan_has_calibration_and_hard_token_limits():
    plan = load_full_plan(PLAN)
    assert plan["calibration"]["research_runs"] == 6
    assert plan["calibration"]["hard_token_limit"] == 3_000_000
    assert plan["token_budget"]["soft_stop_tokens"] == 36_000_000
    assert plan["token_budget"]["hard_stop_tokens"] == 42_000_000
    assert plan["token_budget"]["minimum_reserved_tokens"] == 8_000_000
    assert plan["models"]["expected_model_id"] == "qwen3.7-plus"
    assert plan["evaluation_environment"] == {
        "conda_file": "environment.phase7.yml",
        "constraints_file": "constraints/evaluation-py311.txt",
        "python": "3.11",
        "deepeval": "4.1.1",
        "require_pip_check": True,
    }


def test_models_resolve_from_identifiers_without_credentials():
    plan = load_full_plan(PLAN)
    environment = {
        "SUMMARIZATION_MODEL": "openai:qwen3.7-plus",
        "RESEARCH_MODEL": "openai:qwen3.7-plus",
        "COMPRESSION_MODEL": "openai:qwen3.7-plus",
        "FINAL_REPORT_MODEL": "openai:qwen3.7-plus",
        "OPENAI_API_KEY": "must-never-be-returned",
    }
    resolved = resolve_models(plan, environment)
    assert set(resolved.values()) == {"openai:qwen3.7-plus"}
    assert "must-never-be-returned" not in repr(resolved)


def test_missing_or_wrong_model_fails_closed():
    plan = load_full_plan(PLAN)
    with pytest.raises(EvaluationBudgetError, match="missing model"):
        resolve_models(plan, {})
    wrong = {
        key: "openai:another-model"
        for key in (
            "SUMMARIZATION_MODEL",
            "RESEARCH_MODEL",
            "COMPRESSION_MODEL",
            "FINAL_REPORT_MODEL",
        )
    }
    with pytest.raises(EvaluationBudgetError, match="do not match"):
        resolve_models(plan, wrong)


def test_requested_calibration_and_full_ceilings_are_enforced():
    plan = load_full_plan(PLAN)
    assert validate_requested_budget(
        plan,
        run_kind="calibration",
        repeats=1,
        requested_max_tokens=3_000_000,
    ) == 3_000_000
    with pytest.raises(EvaluationBudgetError, match="exceeds"):
        validate_requested_budget(
            plan,
            run_kind="calibration",
            repeats=1,
            requested_max_tokens=3_000_001,
        )
    with pytest.raises(EvaluationBudgetError, match="fixed plan"):
        validate_requested_budget(
            plan,
            run_kind="full",
            repeats=1,
            requested_max_tokens=42_000_000,
        )
    with pytest.raises(EvaluationBudgetError, match="fixed token ceiling"):
        validate_requested_budget(
            plan,
            run_kind="full",
            repeats=3,
            requested_max_tokens=41_999_999,
        )
    with pytest.raises(EvaluationBudgetError, match="fixed plan"):
        validate_requested_budget(
            plan,
            run_kind="full",
            repeats=4,
            requested_max_tokens=42_000_000,
        )


def test_token_ledger_stops_on_unknown_per_run_soft_and_hard_limits():
    ledger = TokenBudgetLedger(
        soft_stop_tokens=400,
        hard_stop_tokens=500,
        per_run_tokens=250,
    )
    with pytest.raises(EvaluationBudgetError, match="unknown"):
        ledger.record_run(None)
    ledger.record_run(200)
    assert ledger.can_dispatch()
    ledger.record_run(200)
    assert not ledger.can_dispatch()
    with pytest.raises(EvaluationBudgetError, match="per-run"):
        ledger.record_run(251)


def test_error_signature_and_consecutive_failure_breakers_stop_waste():
    breaker = FailureCircuitBreaker(
        max_consecutive_failures=2,
        same_error_signature_limit=2,
        failure_rate_min_runs=4,
        max_failure_rate=0.25,
        max_failed_run_tokens=4_000_000,
    )
    breaker.record(success=False, total_tokens=100_000, error_signature="timeout")
    with pytest.raises(EvaluationBudgetError, match="consecutive"):
        breaker.record(success=False, total_tokens=100_000, error_signature="timeout")


def test_retry_budget_is_at_most_ten_percent_and_unknown_fails_closed():
    retries = RetryBudgetLedger(hard_stop_tokens=1_000, max_retry_fraction=0.1)
    retries.record(100)
    with pytest.raises(EvaluationBudgetError, match="retry token"):
        retries.record(1)
    with pytest.raises(EvaluationBudgetError, match="unknown"):
        RetryBudgetLedger(1_000, 0.1).record(None)


def test_calibration_projection_gates_main_wave():
    assert project_total_tokens(
        [100, 120, 130, 140, 150, 160],
        remaining_runs=48,
        safety_multiplier=1.25,
        hard_stop_tokens=20_000,
    ) == 10_400
    with pytest.raises(EvaluationBudgetError, match="projection"):
        project_total_tokens(
            [100, 120, 130, 140, 150, 160],
            remaining_runs=48,
            safety_multiplier=1.25,
            hard_stop_tokens=10_000,
        )
