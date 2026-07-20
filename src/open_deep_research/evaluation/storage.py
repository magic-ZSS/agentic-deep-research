"""Defensive JSONL persistence for baseline artifacts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


class JsonlLoadError(ValueError):
    """Describe the exact corrupt line in a JSONL artifact."""


def _path_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.Lock())


def load_jsonl(path: str | Path, model_type: type[ModelT]) -> list[ModelT]:
    """Load and validate every non-empty JSONL line."""
    source = Path(path)
    records: list[ModelT] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise JsonlLoadError(f"cannot read {source}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(model_type.model_validate_json(line))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise JsonlLoadError(
                f"invalid JSONL record in {source} at line {line_number}: {exc}"
            ) from exc
    return records


def write_jsonl_atomic(path: str | Path, records: Iterable[BaseModel]) -> None:
    """Replace a JSONL file atomically using a temporary sibling file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        record.model_dump_json(exclude_none=False) + "\n" for record in records
    )

    with _path_lock(target):
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)


def append_jsonl_atomic(
    path: str | Path,
    record: ModelT,
    model_type: type[ModelT],
) -> None:
    """Validate existing records and atomically append one complete line."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(target):
        existing = load_jsonl(target, model_type) if target.exists() else []
        payload = "".join(
            item.model_dump_json(exclude_none=False) + "\n"
            for item in [*existing, record]
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)
