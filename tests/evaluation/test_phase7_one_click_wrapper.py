from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from open_deep_research.evaluation.source_gate import EVALUATION_SOURCE_PATHS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POWERSHELL_WRAPPER = PROJECT_ROOT / "scripts/run_phase7_full.ps1"
CMD_WRAPPER = PROJECT_ROOT / "scripts/run_phase7_full.cmd"


def test_one_click_wrapper_orders_calibration_preflight_and_full() -> None:
    script = POWERSHELL_WRAPPER.read_text(encoding="ascii")

    required = {
        "-ConfirmCost",
        '[string]$EnvironmentName = "open-deep-research"',
        "Require-CleanEvaluationSourceStatus",
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
    assert "Existing paid state found; keep it and continue with resume." in script
    assert "Paid steps are never retried automatically." in script
    assert "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass" in command
    assert "%*" in command
    assert "scripts/run_phase7_full.ps1" in EVALUATION_SOURCE_PATHS
    assert "scripts/run_phase7_full.cmd" in EVALUATION_SOURCE_PATHS


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
