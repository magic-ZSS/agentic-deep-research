from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from open_deep_research.evaluation.source_gate import (
    EVALUATION_SOURCE_PATHS,
    EvaluationSourceGateError,
    capture_evaluation_source_snapshot,
    require_clean_evaluation_source,
)


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "phase7@example.invalid")
    _git(root, "config", "user.name", "Phase 7 Test")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def test_clean_source_returns_json_persistable_attestation(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    attestation = require_clean_evaluation_source(root)

    assert attestation.clean is True
    assert attestation.git_head == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert attestation.checked_paths == EVALUATION_SOURCE_PATHS
    assert json.loads(json.dumps(attestation.as_dict())) == {
        "git_head": attestation.git_head,
        "clean": True,
        "checked_paths": list(EVALUATION_SOURCE_PATHS),
    }


def test_snapshot_hashes_relevant_untracked_content(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    source = root / "tests" / "evaluation" / "metric.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")

    first = capture_evaluation_source_snapshot(root)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = capture_evaluation_source_snapshot(root)

    assert first.git_head == second.git_head
    assert first.clean is False
    assert first.untracked_file_count == 1
    assert first.source_sha256 != second.source_sha256
    assert len(first.source_sha256) == 64


def test_snapshot_excludes_docs_status_and_generated_artifacts(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = capture_evaluation_source_snapshot(root)
    for relative in (
        "docs/notes.md",
        "progress.md",
        "artifacts/evaluation/smoke/runs.jsonl",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")

    after = capture_evaluation_source_snapshot(root)

    assert after.source_sha256 == before.source_sha256
    assert after.clean is True


def test_snapshot_content_identity_survives_committing_identical_source(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    source = root / "tests" / "evaluation" / "metric.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = capture_evaluation_source_snapshot(root)

    _git(root, "add", source.relative_to(root).as_posix())
    _git(root, "commit", "--quiet", "-m", "add evaluation source")
    after = capture_evaluation_source_snapshot(root)

    assert before.clean is False
    assert after.clean is True
    assert before.git_head != after.git_head
    assert before.source_sha256 == after.source_sha256


@pytest.mark.parametrize("tracked", [False, True])
def test_relevant_untracked_or_tracked_change_fails_closed(
    tmp_path: Path, tracked: bool
) -> None:
    root = _repository(tmp_path)
    source = root / "src" / "open_deep_research" / "changed.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    if tracked:
        _git(root, "add", source.relative_to(root).as_posix())
        _git(root, "commit", "--quiet", "-m", "add relevant source")
        source.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(EvaluationSourceGateError, match="clean relevant source"):
        require_clean_evaluation_source(root)


def test_untracked_nested_evaluation_file_fails_closed(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    test_file = root / "tests" / "evaluation" / "new_metric.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(EvaluationSourceGateError, match="clean relevant source"):
        require_clean_evaluation_source(root)


def test_docs_state_and_artifact_changes_do_not_block(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    for relative in (
        "docs/notes.md",
        "progress.md",
        "session-handoff.md",
        "feature_list.json",
        "artifacts/evaluation/full/report.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("untracked\n", encoding="utf-8")

    attestation = require_clean_evaluation_source(root)

    assert attestation.clean is True


def test_non_repository_and_missing_root_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(EvaluationSourceGateError, match="Git repository root"):
        require_clean_evaluation_source(tmp_path)
    with pytest.raises(EvaluationSourceGateError, match="project root"):
        require_clean_evaluation_source(tmp_path / "missing")


def test_head_change_during_status_check_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    from open_deep_research.evaluation import source_gate

    original = source_gate._run_git
    status_seen = False

    def moving_head(
        project_root: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal status_seen
        result = original(project_root, arguments)
        if arguments and arguments[0] == "status":
            status_seen = True
            (root / "README.md").write_text("second commit\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _git(root, "commit", "--quiet", "-m", "move HEAD")
        return result

    monkeypatch.setattr(source_gate, "_run_git", moving_head)

    with pytest.raises(EvaluationSourceGateError, match="HEAD changed"):
        require_clean_evaluation_source(root)
    assert status_seen is True
