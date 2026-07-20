"""Deterministic candidate-validation contracts for governed retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_deep_research.evidence.models import (
    EvidenceDirectness,
    EvidenceRelation,
)
from open_deep_research.knowledge.ids import stable_id


class CandidateValidationStatus(StrEnum):
    """Terminal policy result for one candidate snapshot."""

    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"


class ValidationRuleResult(BaseModel):
    """One observable hard-rule outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class CandidateValidationDecision(BaseModel):
    """Auditable decision shared by local and Web candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = ""
    policy_version: str = Field(min_length=1)
    requirement_id: str
    evidence_id: str
    version_id: str
    status: CandidateValidationStatus
    rule_results: tuple[ValidationRuleResult, ...]
    proposed_relation: EvidenceRelation
    proposed_directness: EvidenceDirectness
    proposed_confidence: float = Field(ge=0, le=1)
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def normalize_evaluated_at(cls, value: datetime) -> datetime:
        """Keep policy decisions unambiguous across local time zones."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return value.astimezone(UTC)

    def model_post_init(self, _context: object) -> None:
        expected = stable_id(
            "validation",
            self.policy_version,
            self.requirement_id,
            self.evidence_id,
            self.version_id,
            self.status.value,
            *(f"{item.rule}:{item.passed}:{item.detail}" for item in self.rule_results),
        )
        if self.decision_id and self.decision_id != expected:
            raise ValueError("decision_id does not match validation inputs")
        object.__setattr__(self, "decision_id", expected)

    @property
    def accepted(self) -> bool:
        """Return whether every hard rule accepted the candidate."""
        return self.status is CandidateValidationStatus.ACCEPTED
