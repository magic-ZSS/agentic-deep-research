"""Tests for the offline Phase 2 PaperQA dependency compatibility gate."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

from scripts import check_phase2_dependencies as dependency_smoke


def _write_fake_project(root: Path, adapter_source: str) -> None:
    pins = "\n".join(
        f'    "{name}=={version}",'
        for name, version in dependency_smoke.EXPECTED_VERSIONS.items()
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0'\n"
        "[project.optional-dependencies]\nknowledge = [\n"
        f"{pins}\n]\n",
        encoding="utf-8",
    )
    refs = root / "doc" / "reference"
    refs.mkdir(parents=True)
    (refs / "refs.lock.json").write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "id": "paper-qa",
                        "commit": dependency_smoke.REFERENCE_COMMIT,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    adapter = root / "src" / "open_deep_research" / "knowledge"
    adapter.mkdir(parents=True)
    (adapter / "paperqa_adapter.py").write_text(adapter_source, encoding="utf-8")


class _FakeSettings:
    def __init__(self, *, parsing, answer, agent):
        assert agent["index"]["index_directory"]
        self.parsing = SimpleNamespace(
            use_doc_details=parsing["use_doc_details"],
            defer_embedding=parsing["defer_embedding"],
            should_parse_and_enrich_media=(False, False),
        )
        self.answer = SimpleNamespace(
            evidence_skip_summary=answer["evidence_skip_summary"]
        )
        self.agent = SimpleNamespace(
            index=SimpleNamespace(**agent["index"]),
            rebuild_index=agent["rebuild_index"],
        )


def _fake_importer(name: str):
    if name == "paperqa":
        return SimpleNamespace(
            Docs=type("Docs", (), {}),
            Doc=type("Doc", (), {}),
            Text=type("Text", (), {}),
            Context=type("Context", (), {}),
            Settings=_FakeSettings,
        )
    if name == "paperqa_pypdf":
        return SimpleNamespace(parse_pdf_to_pages=lambda *_args, **_kwargs: None)
    if name == "tantivy":
        return SimpleNamespace(Index=type("Index", (), {}))
    raise ImportError(name)


def test_fully_compatible_report_is_deterministic_without_network(tmp_path):
    _write_fake_project(tmp_path, "class NativePaperQABackend:\n    pass\n")
    versions = dict(dependency_smoke.EXPECTED_VERSIONS)
    report = dependency_smoke.collect_report(
        tmp_path,
        version_getter=versions.__getitem__,
        module_importer=_fake_importer,
        system_name="Windows",
        python_version=(3, 11, 9),
    )

    assert report["status"] == "compatible"
    assert report["exit_code"] == 0
    assert report["errors"] == []
    assert report["offline_settings"]["ok"] is True
    assert report["adapter_static_check"]["ok"] is True
    assert report["network_used"] is False
    assert report["installation_attempted"] is False


def test_wrong_installed_version_is_incompatible_not_missing(tmp_path):
    _write_fake_project(tmp_path, "def retrieve_texts():\n    return []\n")
    versions = dict(dependency_smoke.EXPECTED_VERSIONS)
    versions["litellm"] = "1.81.14"
    report = dependency_smoke.collect_report(
        tmp_path,
        version_getter=versions.__getitem__,
        module_importer=_fake_importer,
        system_name="Windows",
        python_version=(3, 11, 9),
    )

    assert report["status"] == "incompatible"
    assert report["exit_code"] == 1
    assert any(
        error["code"] == "version_mismatch" and error["component"] == "litellm"
        for error in report["errors"]
    )


def test_adapter_static_check_rejects_answer_and_agent_apis(tmp_path):
    path = tmp_path / "adapter.py"
    path.write_text(
        "from paperqa.agents import PaperSearch\n"
        "async def bad(docs):\n"
        "    return await docs.aquery('question')\n",
        encoding="utf-8",
    )
    result, errors = dependency_smoke.check_adapter_forbidden_apis(path)

    assert result["ok"] is False
    assert {finding["name"] for finding in result["findings"]} >= {
        "paperqa.agents",
        "paperqa.agents.PaperSearch",
        "docs.aquery",
    }
    assert all(error["code"] == "forbidden_paperqa_api" for error in errors)


def test_project_knowledge_extra_has_the_complete_exact_matrix():
    result, errors = dependency_smoke.check_pyproject_pins(
        dependency_smoke.PROJECT_ROOT
    )
    assert errors == []
    assert result["ok"] is True
    assert result["pins"] == dependency_smoke.EXPECTED_VERSIONS


def test_missing_dependencies_are_structured_not_skipped(monkeypatch, capsys):
    def missing_version(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    def missing_import(name: str):
        raise ImportError(f"offline fixture missing {name}")

    report = dependency_smoke.collect_report(
        version_getter=missing_version,
        module_importer=missing_import,
    )
    assert report["status"] == "missing_dependencies"
    assert report["exit_code"] == 2
    assert "paper-qa" in report["missing_distributions"]
    assert any(error["code"] == "missing_distribution" for error in report["errors"])
    assert report["status"] != "skipped"
    assert all(error["code"] != "skipped" for error in report["errors"])

    monkeypatch.setattr(dependency_smoke, "collect_report", lambda: report)
    exit_code = dependency_smoke.main(["--json"])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["status"] == "missing_dependencies"
    assert output["exit_code"] == 2
    assert output["network_used"] is False
    assert output["installation_attempted"] is False


def test_missing_distribution_has_priority_exit_two(tmp_path):
    _write_fake_project(tmp_path, "def retrieve_texts():\n    return []\n")

    def versions(name: str) -> str:
        if name == "paper-qa":
            raise importlib.metadata.PackageNotFoundError(name)
        return dependency_smoke.EXPECTED_VERSIONS[name]

    report = dependency_smoke.collect_report(
        tmp_path,
        version_getter=versions,
        module_importer=_fake_importer,
        system_name="Linux",
        python_version=(3, 10, 14),
    )
    assert report["exit_code"] == 2
    assert report["status"] == "missing_dependencies"
    assert "paper-qa" in report["missing_distributions"]
    assert any(error["code"] == "unsupported_platform" for error in report["errors"])
    assert any(error["code"] == "unsupported_python" for error in report["errors"])
