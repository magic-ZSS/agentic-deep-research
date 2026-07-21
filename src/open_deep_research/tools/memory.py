"""Read-only LangChain memory search tool."""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from open_deep_research.memory.models import MemoryType
from open_deep_research.memory.recall import MemoryRecall
from open_deep_research.runtime.identity import RuntimeIdentity


def _trusted(config: RunnableConfig | None):
    context = (config or {}).get("configurable", {}).get("_memory_context", {})
    recall, identity = context.get("recall"), context.get("identity")
    if not isinstance(recall, MemoryRecall) or not isinstance(identity, RuntimeIdentity):
        raise PermissionError("trusted memory runtime is unavailable")
    return recall, identity


@tool("memory_search")
async def memory_search(query: str, limit: int = 5, *, config: RunnableConfig) -> str:
    """Search authorized active memories; this tool cannot write or choose a namespace."""
    recall, identity = _trusted(config)
    result = await recall.search(query, identity, memory_types=(MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.PREFERENCE), limit=limit)
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


MEMORY_TOOLS = (memory_search,)
