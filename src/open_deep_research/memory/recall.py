"""Authorized, freshness-aware and bounded memory recall."""
# ruff: noqa: D101,D102,D107

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from open_deep_research.memory.models import MemoryRecord, MemoryType
from open_deep_research.memory.repositories import MemoryRepository
from open_deep_research.runtime.identity import RuntimeIdentity


class RecallResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    records: tuple[MemoryRecord, ...]
    approximate_tokens: int
    truncated: bool


class MemoryRecall:
    def __init__(self, repository: MemoryRepository, *, evidence_validator: Callable[[tuple[str, ...]], Awaitable[bool]] | None = None) -> None:
        self.repository = repository
        self.evidence_validator = evidence_validator

    async def search(self, query: str, identity: RuntimeIdentity, *, memory_types: tuple[MemoryType, ...], as_of: datetime | None = None, limit: int = 10, token_budget: int = 800) -> RecallResult:
        if limit < 1 or token_budget < 1:
            raise ValueError("recall limits must be positive")
        candidates = []
        for memory_type in memory_types:
            candidates.extend(await self.repository.search(identity.namespace(memory_type.value), query=query, memory_types=(memory_type,), as_of=as_of, limit=limit))
        selected: list[MemoryRecord] = []
        tokens = 0
        for record in sorted({item.memory_id: item for item in candidates}.values(), key=lambda item: (-item.confidence, item.memory_id)):
            if record.memory_type is MemoryType.SEMANTIC and (
                not record.evidence_ids
                or self.evidence_validator is None
                or not await self.evidence_validator(record.evidence_ids)
            ):
                continue
            cost = max(1, len(record.content) // 4)
            if len(selected) >= limit or tokens + cost > token_budget:
                continue
            selected.append(record)
            tokens += cost
        return RecallResult(records=tuple(selected), approximate_tokens=tokens, truncated=len(selected) < len(candidates))
