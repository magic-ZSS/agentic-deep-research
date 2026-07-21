"""Controlled boundary: LangMem extraction output becomes proposals only."""
# ruff: noqa: D102,D107

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from open_deep_research.memory.models import MemoryType, MemoryWriteProposal
from open_deep_research.runtime.identity import RuntimeIdentity


class LangMemProposalAdapter:
    """Never receives a repository/store and therefore cannot put or delete."""

    def __init__(self, extractor: Callable[[Any], Awaitable[list[Any]]]) -> None:
        self.extractor = extractor

    async def extract(self, payload: Any, *, identity: RuntimeIdentity, memory_type: MemoryType, origin_run_id: str) -> list[MemoryWriteProposal]:
        extracted = await self.extractor(payload)
        proposals = []
        for item in extracted:
            content = getattr(item, "content", item)
            if hasattr(content, "model_dump_json"):
                content = content.model_dump_json()
            proposals.append(MemoryWriteProposal(namespace=identity.namespace(memory_type.value), memory_type=memory_type, content=str(content), provenance={"adapter": "langmem", "proposal_only": True}, origin_run_ids=(origin_run_id,)))
        return proposals
