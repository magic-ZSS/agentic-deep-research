from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from open_deep_research.evaluation.process_lease import (
    EvaluationProcessLeaseError,
    evaluation_process_lease,
    evaluation_process_lease_path,
)

ROOT = Path(__file__).resolve().parents[2]


def _child_attempt(project_root: Path, output: Path, kind: str) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from open_deep_research.evaluation.process_lease import evaluation_process_lease
try:
    with evaluation_process_lease(
        project_root=sys.argv[1], output=sys.argv[2], kind=sys.argv[3]
    ):
        print('acquired')
except Exception as error:
    print(type(error).__name__ + ':' + str(error))
    raise SystemExit(23)
raise SystemExit(0)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(project_root),
            str(output),
            kind,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_same_process_conflict_then_release_allows_reacquire(tmp_path: Path):
    project_root = tmp_path / "project"
    output = tmp_path / "artifacts" / "full"
    with evaluation_process_lease(
        project_root=project_root, output=output, kind="full"
    ) as first:
        assert first.output_path == output.resolve()
        assert first.lock_path.is_file()
        with pytest.raises(EvaluationProcessLeaseError, match="already owns"):
            with evaluation_process_lease(
                project_root=project_root, output=output, kind="full"
            ):
                pytest.fail("a competing lease must not enter its context")

    with evaluation_process_lease(
        project_root=project_root, output=output, kind="full"
    ) as reacquired:
        assert reacquired.identity_sha256 == first.identity_sha256


def test_child_process_conflict_and_release(tmp_path: Path):
    project_root = tmp_path / "project"
    output = tmp_path / "artifacts" / "calibration"
    with evaluation_process_lease(
        project_root=project_root, output=output, kind="calibration"
    ):
        blocked = _child_attempt(project_root, output, "calibration")
    assert blocked.returncode == 23
    assert "EvaluationProcessLeaseError" in blocked.stdout
    assert "already owns" in blocked.stdout

    released = _child_attempt(project_root, output, "calibration")
    assert released.returncode == 0
    assert released.stdout.strip() == "acquired"


def test_kind_and_absolute_output_are_part_of_lock_identity(tmp_path: Path):
    project_root = tmp_path / "project"
    output = tmp_path / "result"
    first = evaluation_process_lease_path(
        project_root=project_root, output=output, kind="full"
    )
    same = evaluation_process_lease_path(
        project_root=project_root,
        output=output.parent / "." / output.name,
        kind="full",
    )
    different_kind = evaluation_process_lease_path(
        project_root=project_root, output=output, kind="calibration"
    )
    different_output = evaluation_process_lease_path(
        project_root=project_root, output=tmp_path / "other", kind="full"
    )
    assert first == same
    assert len(first.stem.rsplit("-", 1)[-1]) == 64
    assert len({first, different_kind, different_output}) == 3

    with pytest.raises(ValueError, match="safe lowercase"):
        evaluation_process_lease_path(
            project_root=project_root, output=output, kind="../full"
        )
