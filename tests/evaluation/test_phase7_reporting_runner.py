import json
import socket

from open_deep_research.evaluation.experiment_models import ExperimentRun
from open_deep_research.evaluation.reporting import (
    render_readme_section,
    update_readme_from_report,
    validate_artifact_manifest,
)
from open_deep_research.evaluation.runner import run_smoke
from open_deep_research.evaluation.storage import load_jsonl
from scripts import run_eval


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
    assert all(item.scorer_version == "evaluation-claim-scorer-v1" for item in records)
    assert all(item.telemetry.total_tokens is None for item in records)
    assert validate_artifact_manifest(output) == []
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
