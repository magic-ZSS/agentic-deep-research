import json
import socket
import sys
import types
import uuid

import pytest

# Smoke/report tests need no UUIDv7 behavior. The production paid-environment
# import smoke deliberately does not install this process-local fallback.
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

from open_deep_research.evaluation.custom_metrics import SCORER_VERSION
from open_deep_research.evaluation.experiment_models import ExperimentRun
from open_deep_research.evaluation.reporting import (
    render_readme_section,
    update_readme_from_report,
    validate_artifact_manifest,
    write_artifact_manifest,
)
from open_deep_research.evaluation.runner import (
    deterministic_experiment_id,
    run_smoke,
)
from open_deep_research.evaluation.storage import load_jsonl
from scripts import run_eval
from scripts.validate_phase import _check_phase7_calibration


def test_offline_smoke_runs_all_cases_variants_and_writes_consistent_artifacts(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    output = tmp_path / "smoke"
    records = run_smoke(project_root=".", output_dir=output)
    loaded = load_jsonl(output / "runs.jsonl", ExperimentRun)
    assert loaded == records
    assert len(records) == 45
    assert {item.difficulty for item in records} == {"simple", "medium", "complex"}
    assert {item.variant_id for item in records} == {
        "baseline", "paperqa", "agentic_rag", "memory", "citation_validator"
    }
    assert all(item.scorer_version == SCORER_VERSION for item in records)
    assert all(item.telemetry.total_tokens is None for item in records)
    experiment = json.loads(
        (output / "experiment.json").read_text(encoding="utf-8")
    )
    snapshot = experiment["source_snapshot"]
    assert len(snapshot["source_sha256"]) == 64
    assert {item.trace["source_snapshot_sha256"] for item in records} == {
        snapshot["source_sha256"]
    }
    assert experiment["experiment_id"] == deterministic_experiment_id(
        source_snapshot_sha256=snapshot["source_sha256"],
        dataset_version="v1",
        variant_ids=[
            "baseline",
            "paperqa",
            "agentic_rag",
            "memory",
            "citation_validator",
        ],
        mode="smoke",
        repeats=1,
        scorer_version=SCORER_VERSION,
    )
    assert validate_artifact_manifest(output) == []
    assert all(
        b"\r\n" not in (output / name).read_bytes()
        for name in (
            "runs.jsonl",
            "report.json",
            "report.md",
            "experiment.json",
            "manifest.json",
        )
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["run_count"] == 45
    assert "do not establish live quality" in " ".join(report["limitations"])
    assert "| baseline | complex |" in (output / "report.md").read_text(encoding="utf-8")


def test_full_cli_refuses_before_output_or_external_call(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ODR_EVAL_MODE", raising=False)
    monkeypatch.delenv("RUN_FULL_EVAL", raising=False)
    output = tmp_path / "must-not-exist"
    code = run_eval.main(["--mode", "full", "--variants", "all", "--repeats", "3", "--output", str(output)])
    assert code == 3
    assert not output.exists()
    assert "not_run_no_authorization" in capsys.readouterr().err


def test_calibration_cli_refuses_above_plan_limit(tmp_path, capsys):
    output = tmp_path / "must-not-exist"
    code = run_eval.main(
        [
            "--mode",
            "calibration",
            "--repeats",
            "1",
            "--max-total-tokens",
            "3000001",
            "--confirm-cost",
            "--output",
            str(output),
        ]
    )
    assert code == 3
    assert not output.exists()
    assert "exceeds plan limit 3000000" in capsys.readouterr().err


def test_smoke_cli_default_generates_machine_and_markdown_reports(tmp_path):
    output = tmp_path / "cli"
    assert run_eval.main(["--output", str(output)]) == 0
    assert (output / "runs.jsonl").is_file()
    assert (output / "report.json").is_file()
    assert (output / "report.md").is_file()
    assert (output / "manifest.json").is_file()


def test_readme_section_is_generated_from_machine_report(tmp_path):
    output = tmp_path / "artifacts"
    run_smoke(project_root=".", output_dir=output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")
    update_readme_from_report(readme, report)
    content = readme.read_text(encoding="utf-8")
    assert render_readme_section(report) in content
    assert "| baseline | 9 | 9 |" in content


def test_artifact_manifest_rejects_unlisted_and_escaping_paths(tmp_path):
    output = tmp_path / "artifacts"
    run_smoke(project_root=".", output_dir=output)
    (output / "unlisted.json").write_text("{}\n", encoding="utf-8")
    assert "unlisted:unlisted.json" in validate_artifact_manifest(output)

    (output / "unlisted.json").unlink()
    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../outside.json"
    (output / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    errors = validate_artifact_manifest(output)
    assert "unsafe:../outside.json" in errors


def test_optional_calibration_requires_report_and_durable_budget_to_match(tmp_path):
    output = tmp_path / "artifacts" / "evaluation" / "calibration"
    output.mkdir(parents=True)
    identity = {
        "experiment_id": "cal-test",
        "dataset_id": "v1",
        "git_head": "0" * 40,
    }
    ledger = {
        "committed_tokens": 20,
        "hard_token_limit": 3_000_000,
        "active_calls": 0,
        "active_reserved_tokens": 0,
    }
    payloads = {
        "budget.json": {
            "calibration_identity": identity,
            "ledger": ledger,
        },
        "experiment.json": {
            "experiment_id": "cal-test",
            "status": "stopped",
            "hard_token_limit": 3_000_000,
            "completed_run_records": 1,
            "claims": {"full_matrix_complete": False},
        },
        "journal.json": {"identity": identity},
        "report.json": {
            "experiment_id": "cal-test",
            "calibration_status": "stopped",
            "stopped_reason": "budget_guard",
            "planned_runs": 6,
            "completed_run_records": 1,
            "token_budget": ledger,
        },
    }
    for name, payload in payloads.items():
        (output / name).write_text(json.dumps(payload), encoding="utf-8")
    (output / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    write_artifact_manifest(
        output,
        experiment_id="cal-test",
        dataset_version="v1",
        project_commit="0" * 40,
    )

    assert "20 committed tokens" in _check_phase7_calibration(tmp_path)

    payloads["budget.json"]["ledger"] = {**ledger, "committed_tokens": 19}
    (output / "budget.json").write_text(
        json.dumps(payloads["budget.json"]), encoding="utf-8"
    )
    write_artifact_manifest(
        output,
        experiment_id="cal-test",
        dataset_version="v1",
        project_commit="0" * 40,
    )
    with pytest.raises(ValueError, match="differs from durable ledger"):
        _check_phase7_calibration(tmp_path)
