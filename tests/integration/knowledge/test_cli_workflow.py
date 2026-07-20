from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "knowledge"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_parses_four_fixtures_without_writes(tmp_path: Path) -> None:
    database = tmp_path / "dry-run.db"
    completed = _run(
        "scripts/ingest_knowledge.py",
        "--source",
        str(FIXTURE_ROOT),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--db",
        str(database),
        "--dry-run",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "dry_run"
    assert payload["writes"] == 0
    assert len(payload["documents"]) == 4
    assert {item["parser"] for item in payload["documents"]} == {
        "pymupdf_text",
        "markdown_heading",
        "html_snapshot",
        "verified_past_query",
    }
    assert not database.exists()
    assert not (tmp_path / "blobs").exists()


def test_cli_import_reopen_and_candidate_inspection(tmp_path: Path) -> None:
    source = tmp_path / "fixtures"
    shutil.copytree(FIXTURE_ROOT, source)
    database = tmp_path / "knowledge.db"
    blob_root = tmp_path / "blobs"
    index_root = tmp_path / "derived-index"
    imported = _run(
        "scripts/ingest_knowledge.py",
        "--source",
        str(source),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--db",
        str(database),
        "--blob-dir",
        str(blob_root),
        "--index-dir",
        str(index_root),
        "--json",
    )
    assert imported.returncode == 0, imported.stderr
    payload = json.loads(imported.stdout)
    assert payload["status"] == "succeeded"
    assert payload["index_strategy"] == "repository_rehydrate_on_demand"
    assert len(payload["documents"]) == 4
    assert {item["lifecycle_status"] for item in payload["documents"]} == {
        "candidate"
    }
    assert {item["index_status"] for item in payload["documents"]} == {
        "not_requested"
    }
    assert database.is_file()
    assert list(blob_root.rglob("*.blob"))
    assert not index_root.exists()
    assert "internal_storage_ref" not in imported.stdout

    active_only = _run(
        "scripts/search_knowledge.py",
        "--db",
        str(database),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--query",
        "storage evidence",
        "--json",
    )
    assert active_only.returncode == 0, active_only.stderr
    active_payload = json.loads(active_only.stdout)
    assert active_payload["artifact"]["hits"] == []
    assert active_payload["artifact"]["empty_reason"] == "no_matching_knowledge"

    inspection = _run(
        "scripts/search_knowledge.py",
        "--db",
        str(database),
        "--index-dir",
        str(index_root),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--include-candidate",
        "--query",
        "storage evidence",
        "--json",
    )
    assert inspection.returncode == 0, inspection.stderr
    inspection_payload = json.loads(inspection.stdout)
    hits = inspection_payload["artifact"]["hits"]
    assert hits
    assert all(hit["inspection_only"] and not hit["citable"] for hit in hits)
    assert all(hit["lifecycle_status"] == "candidate" for hit in hits)
    assert "internal_storage_ref" not in inspection.stdout


@pytest.mark.skipif(
    importlib.util.find_spec("paperqa") is None,
    reason="the optional Phase 2 knowledge dependency is not installed",
)
def test_cli_paperqa_opt_in_rehydrates_without_an_answer_agent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(FIXTURE_ROOT / "sample.md", source / "sample.md")
    database = tmp_path / "knowledge.db"
    imported = _run(
        "scripts/ingest_knowledge.py",
        "--source",
        str(source),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--db",
        str(database),
        "--blob-dir",
        str(tmp_path / "blobs"),
        "--json",
    )
    assert imported.returncode == 0, imported.stderr

    completed = _run(
        "scripts/search_knowledge.py",
        "--db",
        str(database),
        "--index-dir",
        str(tmp_path / "paperqa-index"),
        "--tenant",
        "tenant-a",
        "--scope",
        "project-a",
        "--include-candidate",
        "--paperqa",
        "--query",
        "storage evidence",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["artifact"]["backend"] == "paperqa-native"
    assert payload["artifact"]["hits"]
    assert all(hit["inspection_only"] for hit in payload["artifact"]["hits"])
