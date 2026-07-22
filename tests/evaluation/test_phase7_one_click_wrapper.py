from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from open_deep_research.evaluation.calibration_state import (
    CalibrationJournalStore,
    CalibrationRunDefinition,
    capture_experiment_identity,
)
from open_deep_research.evaluation.source_gate import EVALUATION_SOURCE_PATHS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POWERSHELL_WRAPPER = PROJECT_ROOT / "scripts/run_phase7_full.ps1"
CMD_WRAPPER = PROJECT_ROOT / "scripts/run_phase7_full.cmd"


def test_one_click_wrapper_orders_calibration_preflight_and_full() -> None:
    script = POWERSHELL_WRAPPER.read_text(encoding="ascii")

    required = {
        "-ConfirmCost",
        '[string]$EnvironmentName = "open-deep-research"',
        '[string]$CalibrationOutput = "artifacts/evaluation/calibration-v4"',
        "[switch]$ResumeCalibration",
        "Require-CleanEvaluationSourceStatus",
        "Get-CalibrationResumeInspection",
        "Review and commit the listed evaluation files before a paid run.",
        '"--mode", "calibration"',
        '"baseline,citation_validator"',
        '"--max-total-tokens", "3000000"',
        '"--preflight-only"',
        '"ready_for_separate_full_authorization"',
        'Read-Host',
        '"--mode", "full"',
        '"--max-total-tokens", "42000000"',
        '"--tracking", $Tracking',
        '"--resume"',
        '"scripts/render_eval_report.py"',
        '"scripts/validate_phase.py", "--phase", "7"',
    }
    assert not {item for item in required if item not in script}

    calibration = script.index('"--mode", "calibration"')
    preflight = script.index('"--preflight-only"')
    full = script.index('"--mode", "full"', preflight + 1)
    assert calibration < preflight < full
    assert script.index('$env:ODR_EVAL_MODE = "full"') < calibration
    assert script.index('$env:RUN_FULL_EVAL = "1"') < calibration


def test_one_click_wrapper_preserves_resume_and_cost_safety() -> None:
    script = POWERSHELL_WRAPPER.read_text(encoding="ascii")
    command = CMD_WRAPPER.read_text(encoding="ascii")

    assert "Remove-Item" not in script
    assert "Start-Sleep" not in script
    assert "while (" not in script.lower()
    assert "LANGSMITH_API_KEY" in script
    assert "Existing paid state found; keep it and continue with resume." not in script
    assert "if ($CalibrationExists -and $ResumeCalibration)" in script
    assert "current_identity_mismatch" in script
    assert "token_ledger_fail_closed" in script
    assert "token_usage_unknown" in script
    assert "The existing directory was preserved" in script
    assert "choose a fresh -CalibrationOutput" in script
    assert "Paid steps are never retried automatically." in script
    assert "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass" in command
    assert "%*" in command
    assert "scripts/run_phase7_full.ps1" in EVALUATION_SOURCE_PATHS
    assert "scripts/run_phase7_full.cmd" in EVALUATION_SOURCE_PATHS


def _resume_inspection_program() -> str:
    script = POWERSHELL_WRAPPER.read_text(encoding="ascii")
    marker = "$InspectionProgram = @'\n"
    start = script.index(marker) + len(marker)
    end = script.index("\n'@", start)
    return script[start:end]


def test_resume_inspection_rejects_stopped_fail_closed_state_without_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "stopped-calibration"
    output.mkdir()
    environment = dict(os.environ)
    for key in (
        "SUMMARIZATION_MODEL",
        "RESEARCH_MODEL",
        "COMPRESSION_MODEL",
        "FINAL_REPORT_MODEL",
        "EVALUATION_JUDGE_MODEL",
    ):
        environment[key] = "openai:qwen3.7-plus"

    identity = capture_experiment_identity(
        PROJECT_ROOT,
        plan_path=PROJECT_ROOT / "tests/evaluation/full_plan.v1.json",
        ablation_path=PROJECT_ROOT / "tests/evaluation/ablations.v1.json",
        dataset_id="v1",
        model_ids={
            "compression": "openai:qwen3.7-plus",
            "final_report": "openai:qwen3.7-plus",
            "judge": "openai:qwen3.7-plus",
            "protocol": "phase7-calibration-v1",
            "provenance": "live",
            "research": "openai:qwen3.7-plus",
            "summarization": "openai:qwen3.7-plus",
        },
    )
    CalibrationJournalStore.create(
        output / "journal.json",
        identity=identity,
        runs=[
            CalibrationRunDefinition(
                case_id="simple-001",
                variant_id="baseline",
                repeat=1,
            )
        ],
        judge_metric_names=["claim_citation_scorer"],
    )
    (output / "experiment.json").write_text(
        json.dumps({"status": "stopped"}), encoding="utf-8"
    )
    (output / "budget.json").write_text(
        json.dumps(
            {
                "calibration_identity": identity.model_dump(mode="json"),
                "ledger": {
                    "fail_closed": True,
                    "unknown_usage": True,
                    "active_calls": 0,
                    "active_reserved_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    before = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }

    completed = subprocess.run(
        [sys.executable, "-c", _resume_inspection_program(), str(output)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    inspection = json.loads(completed.stdout)
    assert inspection["safe"] is False
    assert "experiment_stopped" in inspection["reasons"]
    assert "token_ledger_fail_closed" in inspection["reasons"]
    assert "token_usage_unknown" in inspection["reasons"]
    after = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }
    assert after == before


def test_resume_inspection_rejects_terminal_failure_crash_window_without_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "terminal-failure-calibration"
    output.mkdir()
    environment = dict(os.environ)
    for key in (
        "SUMMARIZATION_MODEL",
        "RESEARCH_MODEL",
        "COMPRESSION_MODEL",
        "FINAL_REPORT_MODEL",
        "EVALUATION_JUDGE_MODEL",
    ):
        environment[key] = "openai:qwen3.7-plus"

    identity = capture_experiment_identity(
        PROJECT_ROOT,
        plan_path=PROJECT_ROOT / "tests/evaluation/full_plan.v1.json",
        ablation_path=PROJECT_ROOT / "tests/evaluation/ablations.v1.json",
        dataset_id="v1",
        model_ids={
            "compression": "openai:qwen3.7-plus",
            "final_report": "openai:qwen3.7-plus",
            "judge": "openai:qwen3.7-plus",
            "protocol": "phase7-calibration-v1",
            "provenance": "live",
            "research": "openai:qwen3.7-plus",
            "summarization": "openai:qwen3.7-plus",
        },
    )
    store = CalibrationJournalStore.create(
        output / "journal.json",
        identity=identity,
        runs=[
            CalibrationRunDefinition(
                case_id="simple-001",
                variant_id="baseline",
                repeat=1,
            )
        ],
        judge_metric_names=["claim_citation_scorer"],
    )
    run_id = store.load().runs[0].run_id
    fingerprint = "a" * 64
    store.start_research(run_id)
    store.complete_research(
        run_id,
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        error_fingerprint=fingerprint,
    )
    store.complete_run(
        run_id,
        status="failed",
        error_fingerprint=fingerprint,
    )
    assert store.resume_summary().can_resume is False
    assert store.resume_summary().terminal_noncompleted_run_ids == [run_id]

    (output / "experiment.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8"
    )
    (output / "budget.json").write_text(
        json.dumps(
            {
                "calibration_identity": identity.model_dump(mode="json"),
                "ledger": {
                    "fail_closed": False,
                    "unknown_usage": False,
                    "active_calls": 0,
                    "active_reserved_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    before = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }

    completed = subprocess.run(
        [sys.executable, "-c", _resume_inspection_program(), str(output)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    inspection = json.loads(completed.stdout)
    assert inspection["safe"] is False
    assert inspection["reasons"] == ["terminal_noncompleted_runs"]
    after = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }
    assert after == before


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None and shutil.which("pwsh") is None,
    reason="PowerShell is unavailable on this platform",
)
def test_one_click_wrapper_refuses_before_conda_without_cost_confirmation() -> None:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    assert executable is not None

    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_WRAPPER),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert "Cost confirmation is required" in completed.stderr
    assert "conda environment" not in completed.stderr.lower()
