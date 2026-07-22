"""Fail-closed source reproducibility gate for paid Phase 7 evaluation."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

EVALUATION_SOURCE_PATHS: tuple[str, ...] = (
    "src/open_deep_research",
    "scripts/run_eval.py",
    "scripts/validate_phase.py",
    "scripts/compare_ablations.py",
    "scripts/render_eval_report.py",
    "tests/evaluation",
    "tests/baseline/cases.jsonl",
    "pyproject.toml",
    "langgraph.json",
    "environment.phase7.yml",
    "constraints/evaluation-py311.txt",
)

_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class EvaluationSourceGateError(RuntimeError):
    """Reject paid evaluation when its relevant source is not reproducible."""


@dataclass(frozen=True, slots=True)
class EvaluationSourceAttestation:
    """Persistable facts attesting to one clean evaluation source snapshot."""

    git_head: str
    clean: bool
    checked_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible attestation payload."""
        return {
            "git_head": self.git_head,
            "clean": self.clean,
            "checked_paths": list(self.checked_paths),
        }


@dataclass(frozen=True, slots=True)
class EvaluationSourceSnapshot:
    """Content identity for the Phase 7-relevant working source tree.

    ``git_head`` records the commit observed during capture. ``source_sha256``
    hashes the current bytes and paths of relevant tracked and untracked files
    independently of Git status, so committing an otherwise identical source
    tree does not change the content identity. Generated artifacts and status
    documents are outside the explicit path allowlist.
    """

    git_head: str
    source_sha256: str
    clean: bool
    checked_paths: tuple[str, ...]
    untracked_file_count: int

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible snapshot payload."""
        return {
            "git_head": self.git_head,
            "source_sha256": self.source_sha256,
            "clean": self.clean,
            "checked_paths": list(self.checked_paths),
            "untracked_file_count": self.untracked_file_count,
        }


def _run_git(project_root: Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=False,
    )


def _read_head(project_root: Path) -> str:
    try:
        result = _run_git(project_root, ["rev-parse", "--verify", "HEAD"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvaluationSourceGateError(
            "evaluation source HEAD could not be inspected safely"
        ) from exc
    if result.returncode != 0 or result.stderr:
        raise EvaluationSourceGateError(
            "evaluation source HEAD could not be inspected safely"
        )
    try:
        head = result.stdout.decode("ascii").strip().lower()
    except UnicodeDecodeError as exc:
        raise EvaluationSourceGateError(
            "evaluation source HEAD is not a valid Git object id"
        ) from exc
    if not _GIT_OBJECT_ID.fullmatch(head):
        raise EvaluationSourceGateError(
            "evaluation source HEAD is not a valid Git object id"
        )
    return head


def _require_repository_root(project_root: Path) -> None:
    try:
        result = _run_git(project_root, ["rev-parse", "--show-toplevel"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvaluationSourceGateError(
            "evaluation project root is not a Git repository root"
        ) from exc
    if result.returncode != 0 or result.stderr:
        raise EvaluationSourceGateError(
            "evaluation project root is not a Git repository root"
        )
    try:
        discovered = Path(result.stdout.decode("utf-8").strip()).resolve(strict=False)
    except (UnicodeDecodeError, OSError, ValueError) as exc:
        raise EvaluationSourceGateError(
            "evaluation project root could not be resolved safely"
        ) from exc
    if discovered != project_root:
        raise EvaluationSourceGateError(
            "evaluation project root is not the selected Git repository root"
        )


def _scoped_git_output(
    project_root: Path,
    arguments: list[str],
    *,
    failure_message: str,
) -> bytes:
    """Run one scoped Git inspection and fail closed on ambiguous output."""
    try:
        result = _run_git(project_root, arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvaluationSourceGateError(failure_message) from exc
    if result.returncode != 0 or result.stderr:
        raise EvaluationSourceGateError(failure_message)
    return result.stdout


def _safe_source_path(project_root: Path, raw_path: bytes) -> tuple[str, Path]:
    """Resolve one Git-reported source file without following symlinks."""
    try:
        relative = raw_path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationSourceGateError(
            "evaluation source contains a path that cannot be identified safely"
        ) from exc
    if not relative or "\\" in relative:
        raise EvaluationSourceGateError(
            "evaluation source contains an unsafe path"
        )
    parts = relative.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise EvaluationSourceGateError(
            "evaluation source contains an unsafe path"
        )

    candidate = project_root.joinpath(*parts)
    cursor = project_root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise EvaluationSourceGateError(
                "evaluation source contains an unsupported symlink"
            )
    try:
        candidate.resolve(strict=True).relative_to(project_root)
    except (OSError, ValueError) as exc:
        raise EvaluationSourceGateError(
            "evaluation source contains an inaccessible file"
        ) from exc
    if not candidate.is_file():
        raise EvaluationSourceGateError(
            "evaluation source contains an unsupported entry"
        )
    return relative, candidate


def _source_material(project_root: Path) -> tuple[str, bool, int]:
    """Hash current relevant file bytes without including Git state or artifacts."""
    status = _scoped_git_output(
        project_root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *EVALUATION_SOURCE_PATHS,
        ],
        failure_message="evaluation source status could not be inspected safely",
    )
    tracked_output = _scoped_git_output(
        project_root,
        [
            "ls-files",
            "-z",
            "--",
            *EVALUATION_SOURCE_PATHS,
        ],
        failure_message="evaluation tracked source could not be inspected safely",
    )
    untracked_output = _scoped_git_output(
        project_root,
        [
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *EVALUATION_SOURCE_PATHS,
        ],
        failure_message="evaluation untracked source could not be inspected safely",
    )
    tracked_paths = {path for path in tracked_output.split(b"\0") if path}
    untracked_paths = {path for path in untracked_output.split(b"\0") if path}
    if tracked_paths & untracked_paths:
        raise EvaluationSourceGateError(
            "evaluation source path classification is inconsistent"
        )
    raw_paths = sorted(tracked_paths | untracked_paths)

    digest = hashlib.sha256()
    digest.update(b"open-deep-research/evaluation-source-snapshot/v2\0")
    for raw_path in raw_paths:
        relative, candidate = _safe_source_path(project_root, raw_path)
        try:
            content = candidate.read_bytes()
        except OSError as exc:
            raise EvaluationSourceGateError(
                "evaluation source content could not be read safely"
            ) from exc
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), not status, len(untracked_paths)


def capture_evaluation_source_snapshot(
    project_root: str | Path,
) -> EvaluationSourceSnapshot:
    """Capture a stable, non-circular identity for evaluation-relevant source.

    Relevant tracked and untracked file paths/bytes are hashed twice while the
    observed ``HEAD`` is checked for stability. A concurrent source or commit
    change therefore fails closed. The allowlist deliberately excludes
    generated evaluation artifacts, documentation, and lifecycle status files.
    """
    root = Path(project_root).resolve(strict=False)
    if not root.is_dir():
        raise EvaluationSourceGateError(
            "evaluation project root is not an accessible directory"
        )

    _require_repository_root(root)
    head_before = _read_head(root)
    first = _source_material(root)
    second = _source_material(root)
    head_after = _read_head(root)
    if head_after != head_before:
        raise EvaluationSourceGateError(
            "evaluation source HEAD changed during snapshot capture"
        )
    if first != second:
        raise EvaluationSourceGateError(
            "evaluation source changed during snapshot capture"
        )
    source_sha256, clean, untracked_file_count = second
    return EvaluationSourceSnapshot(
        git_head=head_after,
        source_sha256=source_sha256,
        clean=clean,
        checked_paths=EVALUATION_SOURCE_PATHS,
        untracked_file_count=untracked_file_count,
    )


def require_clean_evaluation_source(
    project_root: str | Path,
) -> EvaluationSourceAttestation:
    """Require a stable ``HEAD`` and clean Phase 7-relevant source paths.

    Only the explicit allowlist in :data:`EVALUATION_SOURCE_PATHS` is checked.
    Documentation, status files, and generated artifacts therefore do not
    block a paid evaluation.  Git failures and concurrent ``HEAD`` movement
    fail closed before any external call can be dispatched.
    """
    root = Path(project_root).resolve(strict=False)
    if not root.is_dir():
        raise EvaluationSourceGateError(
            "evaluation project root is not an accessible directory"
        )

    _require_repository_root(root)
    head_before = _read_head(root)
    try:
        status = _run_git(
            root,
            [
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                *EVALUATION_SOURCE_PATHS,
            ],
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvaluationSourceGateError(
            "evaluation source status could not be inspected safely"
        ) from exc
    if status.returncode != 0 or status.stderr:
        raise EvaluationSourceGateError(
            "evaluation source status could not be inspected safely"
        )
    if status.stdout:
        raise EvaluationSourceGateError(
            "paid Phase 7 evaluation requires clean relevant source paths"
        )

    head_after = _read_head(root)
    if head_after != head_before:
        raise EvaluationSourceGateError(
            "evaluation source HEAD changed during reproducibility checks"
        )
    return EvaluationSourceAttestation(
        git_head=head_after,
        clean=True,
        checked_paths=EVALUATION_SOURCE_PATHS,
    )


__all__ = [
    "EVALUATION_SOURCE_PATHS",
    "EvaluationSourceAttestation",
    "EvaluationSourceGateError",
    "EvaluationSourceSnapshot",
    "capture_evaluation_source_snapshot",
    "require_clean_evaluation_source",
]
