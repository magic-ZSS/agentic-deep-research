"""Cross-process leases for paid Phase 7 evaluation outputs.

The lock file is intentionally persistent.  Removing it after unlock would
allow a third process to lock a new inode while another process still holds
the old one.  Operating-system lock ownership is released when the file
handle closes, including after process termination.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class EvaluationProcessLeaseError(RuntimeError):
    """An evaluation output is already owned or cannot be locked safely."""


@dataclass(frozen=True, slots=True)
class EvaluationProcessLease:
    """Non-secret identity of one held evaluation process lease."""

    kind: str
    output_path: Path
    identity_sha256: str
    lock_path: Path


def _normalized_absolute_path(path: str | Path) -> str:
    resolved = Path(path).resolve(strict=False)
    return os.path.normcase(str(resolved)).replace("\\", "/")


def evaluation_process_lease_path(
    *,
    project_root: str | Path,
    output: str | Path,
    kind: str,
) -> Path:
    """Return the stable ignored lock path for ``kind`` and exact output."""
    if not _SAFE_KIND.fullmatch(kind):
        raise ValueError("evaluation lease kind must be a safe lowercase identifier")
    normalized_output = _normalized_absolute_path(output)
    identity = hashlib.sha256(
        json.dumps(
            [kind, normalized_output],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        Path(project_root).resolve(strict=False)
        / ".phase-validation-tmp"
        / "phase7-process-locks"
        / f"{kind}-{identity}.lock"
    )


def _lock_nonblocking(handle: object) -> None:
    descriptor = handle.fileno()  # type: ignore[attr-defined]
    if os.name == "nt":
        lock_module = importlib.import_module("msvcrt")
        lock_module.locking(descriptor, lock_module.LK_NBLCK, 1)
    else:
        lock_module = importlib.import_module("fcntl")
        lock_module.flock(descriptor, lock_module.LOCK_EX | lock_module.LOCK_NB)


def _unlock(handle: object) -> None:
    descriptor = handle.fileno()  # type: ignore[attr-defined]
    if os.name == "nt":
        lock_module = importlib.import_module("msvcrt")
        lock_module.locking(descriptor, lock_module.LK_UNLCK, 1)
    else:
        lock_module = importlib.import_module("fcntl")
        lock_module.flock(descriptor, lock_module.LOCK_UN)


@contextmanager
def evaluation_process_lease(
    *,
    project_root: str | Path,
    output: str | Path,
    kind: str,
) -> Iterator[EvaluationProcessLease]:
    """Acquire a non-blocking lease held until the context exits.

    Callers must enter this context before reading or creating the output
    journal.  A competing process fails immediately, before it can dispatch a
    paid model or search call.
    """
    output_path = Path(output).resolve(strict=False)
    lock_path = evaluation_process_lease_path(
        project_root=project_root,
        output=output_path,
        kind=kind,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = lock_path.open("a+b")
    except OSError as error:
        raise EvaluationProcessLeaseError(
            "evaluation process lease file cannot be opened safely"
        ) from error

    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _lock_nonblocking(handle)
        except OSError as error:
            raise EvaluationProcessLeaseError(
                "another evaluation process already owns this output lease"
            ) from error
        acquired = True
        identity = lock_path.stem.removeprefix(f"{kind}-")
        yield EvaluationProcessLease(
            kind=kind,
            output_path=output_path,
            identity_sha256=identity,
            lock_path=lock_path,
        )
    finally:
        if acquired:
            try:
                handle.seek(0)
                _unlock(handle)
            finally:
                handle.close()
        else:
            handle.close()


__all__ = [
    "EvaluationProcessLease",
    "EvaluationProcessLeaseError",
    "evaluation_process_lease",
    "evaluation_process_lease_path",
]
