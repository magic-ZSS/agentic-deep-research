"""Bound and redact locally persisted evaluation payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEY = re.compile(
    r"^(?:api[_-]?key|authorization|auth[_-]?token|access[_-]?token|"
    r"password|secret|client[_-]?secret|base[_-]?url|api[_-]?base|endpoint)$",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|auth[_-]?token|access[_-]?token|"
    r"password|secret|client[_-]?secret)\b\s*[=:]\s*)"
    r"(?:bearer\s+)?(?:['\"])?[^\s,'\"}\]]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN = re.compile(r"(?i)\b(?:sk|tvly)-[A-Za-z0-9_-]{8,}\b")
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\)[^\s\"'<>|]+"
)
_FILE_URI = re.compile(r"(?i)\bfile:///{0,2}[^\s\]\[(){}<>\"']+")
_POSIX_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9:])/(?:home|users|tmp|var/tmp)/[^\s\]\[(){}<>\"']+",
    re.IGNORECASE,
)


def redact_evaluation_text(value: str) -> str:
    """Remove common credentials and private absolute paths from text."""
    text = _SECRET_ASSIGNMENT.sub(r"\1<redacted>", value)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _KNOWN_TOKEN.sub("<redacted-token>", text)
    text = _FILE_URI.sub("<local-path>", text)
    text = _WINDOWS_PATH.sub("<local-path>", text)
    return _POSIX_PRIVATE_PATH.sub("<local-path>", text)


def sanitize_evaluation_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 8,
    max_items: int = 200,
) -> Any:
    """Recursively redact secrets while keeping metric-relevant structure."""
    if depth >= max_depth:
        return "<max-depth>"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= max_items:
                sanitized["<truncated-items>"] = len(value) - max_items
                break
            key = redact_evaluation_text(str(raw_key))
            sanitized[key] = (
                "<redacted>"
                if _SECRET_KEY.fullmatch(str(raw_key).strip())
                else sanitize_evaluation_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = list(value)
        result = [
            sanitize_evaluation_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            result.append(f"<truncated-items:{len(items) - max_items}>")
        return result
    if isinstance(value, str):
        return redact_evaluation_text(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    return redact_evaluation_text(str(value))


__all__ = ["redact_evaluation_text", "sanitize_evaluation_value"]
