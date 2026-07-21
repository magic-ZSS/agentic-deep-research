"""Repository contract and authorization errors for long-term memory."""
# ruff: noqa: D101,D102

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from open_deep_research.memory.models import (
    MemoryAuditEvent,
    MemoryGateDecision,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    MemoryWriteProposal,
)


class MemoryRepositoryError(RuntimeError):
    pass


class MemoryAccessError(MemoryRepositoryError, PermissionError):
    pass


class MemoryConflictError(MemoryRepositoryError):
    pass


@runtime_checkable
class MemoryRepository(Protocol):
    async def propose(self, proposal: MemoryWriteProposal, *, actor: str) -> MemoryWriteProposal: ...
    async def get_proposal(self, namespace: tuple[str, ...], proposal_id: str) -> MemoryWriteProposal: ...
    async def apply_decision(self, proposal: MemoryWriteProposal, decision: MemoryGateDecision, *, actor: str) -> MemoryRecord | None: ...
    async def get(self, namespace: tuple[str, ...], memory_id: str, *, include_deleted: bool = False) -> MemoryRecord: ...
    async def search(self, namespace: tuple[str, ...], *, query: str, memory_types: tuple[MemoryType, ...] = (), statuses: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE,), as_of: datetime | None = None, limit: int = 10) -> list[MemoryRecord]: ...
    async def mark_status(self, namespace: tuple[str, ...], memory_id: str, status: MemoryStatus, *, actor: str, reason: str) -> MemoryRecord: ...
    async def soft_delete(self, namespace: tuple[str, ...], memory_id: str, *, actor: str, reason: str) -> MemoryRecord: ...
    async def list_audit(self, namespace: tuple[str, ...], entity_id: str) -> list[MemoryAuditEvent]: ...
