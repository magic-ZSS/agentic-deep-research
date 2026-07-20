"""Batching-invariant reducers for stable graph-state references."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _normalize_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        if value.get("type") != "override":
            raise TypeError("mapping reducer updates must use the override contract")
        value = value.get("value", [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        raise TypeError("stable ID reducer updates must be iterable")
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("stable ID reducer accepts non-empty strings only")
        normalized.append(item.strip())
    return normalized


def stable_id_reducer(current_value: Any, new_value: Any) -> list[str]:
    """Merge by canonical ID with deterministic, batching-invariant ordering."""
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return sorted(set(_normalize_ids(new_value)))
    return sorted(set(_normalize_ids(current_value)) | set(_normalize_ids(new_value)))
