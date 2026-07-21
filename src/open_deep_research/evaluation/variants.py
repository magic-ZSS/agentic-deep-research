"""Fixed and fairness-checked Phase 7 ablation variants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from open_deep_research.evaluation.experiment_models import ExperimentVariant

VARIANT_ORDER = (
    "baseline",
    "paperqa",
    "agentic_rag",
    "memory",
    "citation_validator",
)
FEATURE_KEYS = (
    "enable_knowledge_base",
    "enable_paperqa_retrieval",
    "enable_knowledge_tools",
    "enable_agentic_rag",
    "enable_knowledge_writeback",
    "enable_memory",
    "enable_memory_writes",
    "citation_validation_mode",
)


def load_variants(path: str | Path) -> list[ExperimentVariant]:
    """Load and validate the immutable five-row matrix."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    variants = [ExperimentVariant.model_validate(item) for item in payload["variants"]]
    if tuple(item.variant_id for item in variants) != VARIANT_ORDER:
        raise ValueError("ablation variants must use the fixed order")
    validate_fairness(variants)
    return variants


def validate_fairness(variants: list[ExperimentVariant]) -> None:
    """Require identical dataset/model/search/budget and exact feature progression."""
    first = variants[0]
    for item in variants[1:]:
        for field in ("dataset_version", "model_settings", "search_config", "budget"):
            if getattr(item, field) != getattr(first, field):
                raise ValueError(f"unfair ablation drift in {field}")
    expected = {
        "baseline": (False, False, False, False, False, False, False, "off"),
        "paperqa": (True, True, True, False, False, False, False, "off"),
        "agentic_rag": (True, True, True, True, True, False, False, "off"),
        "memory": (True, True, True, True, True, True, True, "off"),
        "citation_validator": (True, True, True, True, True, True, True, "enforce"),
    }
    for item in variants:
        actual = tuple(item.feature_flags.get(key) for key in FEATURE_KEYS)
        if actual != expected[item.variant_id]:
            raise ValueError(f"invalid feature matrix for {item.variant_id}")
        if len(item.available_tools) != len(set(item.available_tools)):
            raise ValueError(f"duplicate registry tool for {item.variant_id}")


async def validate_registry_snapshots(
    variants: list[ExperimentVariant],
    tool_loader: Callable[[dict[str, Any]], Awaitable[list[Any]]],
) -> None:
    """Compare the manifest to each variant's real tool-routing registry."""
    for variant in variants:
        config = {
            "configurable": {
                **variant.feature_flags,
                "search_api": variant.search_config["provider"],
                "enable_filesystem_mcp": False,
                "enable_knowledge_mcp": False,
            }
        }
        tools = await tool_loader(config)
        names = [
            item.name
            if hasattr(item, "name")
            else item.get("name", item.get("type", "unknown"))
            for item in tools
        ]
        if names != variant.available_tools:
            raise ValueError(
                f"tool registry drift for {variant.variant_id}: "
                f"manifest={variant.available_tools}, runtime={names}"
            )
