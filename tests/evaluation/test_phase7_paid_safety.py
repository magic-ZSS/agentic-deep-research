from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_deep_research.deep_researcher import configurable_model
from open_deep_research.evaluation.artifact_safety import (
    redact_evaluation_text,
    sanitize_evaluation_value,
)
from open_deep_research.evaluation.calibration_runner import CalibrationBudgetStore
from open_deep_research.evaluation.calibration_state import make_experiment_identity
from open_deep_research.evaluation.live_budget import (
    LiveTokenReservationLedger,
    TokenUsageCategory,
)
from open_deep_research.utils import _evaluation_provider_retry_kwargs


def _identity():
    return make_experiment_identity(
        git_head_value="1" * 40,
        dirty_diff_sha256="2" * 64,
        plan_sha256="3" * 64,
        ablation_sha256="4" * 64,
        dataset_id="v1",
        model_ids={"judge": "openai:qwen3.7-plus"},
    )


def test_budget_store_rejects_stale_snapshot_overwrite(tmp_path: Path):
    ledger = LiveTokenReservationLedger(
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    store = CalibrationBudgetStore(
        tmp_path / "budget.json",
        identity=_identity(),
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    store.create(ledger.snapshot())
    reservation = ledger.reserve_before_call(
        run_id="run-1",
        category=TokenUsageCategory.RESEARCH,
        input_upper_bound=100,
        output_upper_bound=50,
    )
    stale = ledger.snapshot()
    ledger.settle_success(
        reservation.reservation_id,
        actual_input_tokens=10,
        actual_output_tokens=5,
    )
    newest = ledger.snapshot()
    store.persist(newest)
    store.persist(stale)
    assert store.load()["revision"] == newest["revision"]
    assert store.load()["committed_tokens"] == 15


def test_budget_store_binds_identity_and_limits(tmp_path: Path):
    ledger = LiveTokenReservationLedger(
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    path = tmp_path / "budget.json"
    store = CalibrationBudgetStore(
        path,
        identity=_identity(),
        hard_token_limit=3_000_000,
        per_run_token_limit=800_000,
    )
    store.create(ledger.snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["calibration_identity"]["experiment_id"] = "cal-" + "0" * 32
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity"):
        CalibrationBudgetStore(
            path,
            identity=_identity(),
            hard_token_limit=3_000_000,
            per_run_token_limit=800_000,
        ).load()


def test_artifact_safety_redacts_credentials_endpoints_and_local_paths():
    payload = sanitize_evaluation_value(
        {
            "api_key": "sk-example-secret-value",
            "base_url": "https://private.example/v1",
            "nested": (
                "Authorization: Bearer abcdefghijklmnop "
                r"C:\Users\person\private\source.pdf tvly-secretvalue"
            ),
        }
    )
    encoded = json.dumps(payload)
    assert "sk-example" not in encoded
    assert "private.example" not in encoded
    assert "abcdefghijklmnop" not in encoded
    assert "C:\\Users" not in encoded
    assert "tvly-secretvalue" not in encoded
    assert redact_evaluation_text("https://public.example/paper") == (
        "https://public.example/paper"
    )


def test_paid_research_model_retry_override_is_opt_in_and_zero():
    configured = configurable_model._model(
        {
            "configurable": {
                "model": "openai:qwen3.7-plus",
                "api_key": "test-only",
                "max_tokens": 8,
                "max_retries": 0,
            }
        }
    )
    assert configured.max_retries == 0
    assert configured.root_client.max_retries == 0
    assert configured.root_async_client.max_retries == 0

    baseline = configurable_model._model(
        {
            "configurable": {
                "model": "openai:qwen3.7-plus",
                "api_key": "test-only",
                "max_tokens": 8,
            }
        }
    )
    assert baseline.max_retries is None
    assert baseline.root_client.max_retries == 2
    assert baseline.root_async_client.max_retries == 2


def test_summarizer_retry_override_rejects_nonzero_and_preserves_default():
    assert _evaluation_provider_retry_kwargs({"configurable": {}}) == {}
    assert _evaluation_provider_retry_kwargs(
        {"configurable": {"max_retries": 0}}
    ) == {"max_retries": 0}
    for invalid in (True, 1, -1, "0"):
        with pytest.raises(ValueError, match="exactly 0"):
            _evaluation_provider_retry_kwargs(
                {"configurable": {"max_retries": invalid}}
            )
