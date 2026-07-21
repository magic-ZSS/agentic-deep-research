"""Immutable memory records, proposals, decisions, and audit data."""
# ruff: noqa: D101,D102,D103

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY_VERSION = "phase5-memory-gate-v1"


def utc_now() -> datetime:
    return datetime.now(UTC)


def stable_memory_id(namespace: tuple[str, ...], memory_type: str, content: str) -> str:
    payload = json.dumps(
        [*namespace, memory_type, " ".join(content.split()).casefold()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "mem_" + hashlib.sha256(payload.encode()).hexdigest()


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryType(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    STALE = "stale"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class GateOutcome(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    QUARANTINE = "quarantine"
    NEEDS_REVIEW = "needs_review"


class GateCheck(MemoryModel):
    name: Literal[
        "importance", "source", "dedupe", "freshness", "sensitivity", "quality", "policy"
    ]
    passed: bool
    reason: str = Field(min_length=1)


class MemoryWriteProposal(MemoryModel):
    proposal_id: str = ""
    namespace: tuple[str, ...]
    memory_type: MemoryType
    content: str = Field(min_length=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    sensitivity: Literal["public", "internal", "sensitive"] = "public"
    evidence_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    origin_run_ids: tuple[str, ...] = ()
    outcome_score: float | None = Field(default=None, ge=0, le=1)
    success_count: int = Field(default=0, ge=0)
    regression_passed: bool = False
    approved: bool = False
    explicit_statement_ref: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize(self) -> Self:
        content = " ".join(self.content.split())
        if not content:
            raise ValueError("proposal content cannot be blank")
        expected = stable_memory_id(self.namespace, self.memory_type.value, content)
        proposal_id = "proposal_" + expected.removeprefix("mem_")
        if self.proposal_id and self.proposal_id != proposal_id:
            raise ValueError("proposal_id does not match stable content identity")
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))
        object.__setattr__(self, "source_ids", tuple(sorted(set(self.source_ids))))
        object.__setattr__(self, "origin_run_ids", tuple(sorted(set(self.origin_run_ids))))
        return self


class MemoryGateDecision(MemoryModel):
    decision_id: str
    proposal_id: str
    outcome: GateOutcome
    checks: tuple[GateCheck, ...]
    policy_version: str = POLICY_VERSION
    reason: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=utc_now)


class MemoryRecord(MemoryModel):
    memory_id: str
    namespace: tuple[str, ...]
    memory_type: MemoryType
    status: MemoryStatus
    content: str
    content_hash: str
    confidence: float = Field(ge=0, le=1)
    sensitivity: str
    evidence_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    origin_run_ids: tuple[str, ...] = ()
    usage_count: int = Field(default=0, ge=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    soft_deleted_at: datetime | None = None


class MemoryAuditEvent(MemoryModel):
    audit_id: str
    namespace: tuple[str, ...]
    entity_id: str
    action: str
    actor: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class WorkingMemory(MemoryModel):
    """Checkpointed thread state; never written to the long-term repository."""

    thread_id: str
    brief: str | None = None
    requirement_ids: tuple[str, ...] = ()
    tool_result_refs: tuple[str, ...] = ()
    agent_status: str | None = None


class EpisodicMemory(MemoryModel):
    """A scored research experience eligible for cross-thread reuse."""

    task_type: str
    brief_fingerprint: str
    plan_summary: str
    useful_tools: tuple[str, ...] = ()
    failure_causes: tuple[str, ...] = ()
    outcome_score: float = Field(ge=0, le=1)
    reusable_lessons: tuple[str, ...] = ()


class SemanticMemory(MemoryModel):
    """A fact that always carries evidence and source provenance."""

    fact: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    observed_at: datetime
    valid_at: datetime
    confidence: float = Field(ge=0, le=1)


class ProceduralMemory(MemoryModel):
    """A strategy supported by repeated independent successful runs."""

    strategy: str
    applicable_when: str
    supporting_run_ids: tuple[str, ...] = Field(min_length=3)
    success_count: int = Field(ge=3)
    regression_passed: bool
    approved: bool


class UserPreferenceMemory(MemoryModel):
    """A preference backed by an explicit user statement."""

    preference: str
    scope: str
    explicit_statement_ref: str = Field(min_length=1)
    confirmed_at: datetime
