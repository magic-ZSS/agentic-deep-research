"""Path-free security audit records for MCP allow/deny decisions."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class MCPAuditRecord(BaseModel):
    """A sanitized security event that cannot contain a local path or secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str
    request_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    allowed: bool
    reason_code: str = Field(min_length=1)
    root_id: str | None = None
    entity_id: str | None = None
    created_at: datetime


class MCPAuditSink(Protocol):
    """Minimal append/query contract used by filesystem and knowledge services."""

    def record(
        self,
        *,
        request_id: str,
        actor: str,
        action: str,
        allowed: bool,
        reason_code: str,
        root_id: str | None = None,
        entity_id: str | None = None,
    ) -> MCPAuditRecord: ...

    def list_records(self, *, request_id: str | None = None) -> tuple[MCPAuditRecord, ...]: ...


class InMemoryMCPAuditSink:
    """Thread-safe, retry-idempotent security audit sink."""

    def __init__(self) -> None:
        self._records: dict[str, MCPAuditRecord] = {}
        self._lock = threading.RLock()

    def record(
        self,
        *,
        request_id: str,
        actor: str,
        action: str,
        allowed: bool,
        reason_code: str,
        root_id: str | None = None,
        entity_id: str | None = None,
    ) -> MCPAuditRecord:
        payload = json.dumps(
            {
                "action": action,
                "actor": actor,
                "allowed": allowed,
                "entity_id": entity_id,
                "reason_code": reason_code,
                "request_id": request_id,
                "root_id": root_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        audit_id = "mcp_audit_" + hashlib.sha256(payload.encode()).hexdigest()
        record = MCPAuditRecord(
            audit_id=audit_id,
            request_id=request_id,
            actor=actor,
            action=action,
            allowed=allowed,
            reason_code=reason_code,
            root_id=root_id,
            entity_id=entity_id,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            return self._records.setdefault(audit_id, record)

    def list_records(self, *, request_id: str | None = None) -> tuple[MCPAuditRecord, ...]:
        with self._lock:
            values = [
                value
                for value in self._records.values()
                if request_id is None or value.request_id == request_id
            ]
            return tuple(sorted(values, key=lambda item: item.audit_id))
