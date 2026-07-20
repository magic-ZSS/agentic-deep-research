"""Strict proposal models for governed, non-destructive lifecycle changes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from open_deep_research.knowledge.ids import canonicalize_text
from open_deep_research.knowledge.models import DomainModel, utc_now


class LifecycleProposalAction(StrEnum):
    """The complete set of actions an agent may propose.

    Deliberately no hard-delete or force-promotion action exists.
    """

    PROPOSE_STALE = "propose_stale"
    PROPOSE_QUARANTINE = "propose_quarantine"
    PROPOSE_SUPERSEDE = "propose_supersede"
    PROPOSE_SOFT_DELETE = "propose_soft_delete"


class LifecycleProposalStatus(StrEnum):
    """Review/application state of a proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class LifecycleTargetType(StrEnum):
    """Soft-deletable canonical entity types."""

    SOURCE = "source"
    DOCUMENT = "document"
    DOCUMENT_VERSION = "document_version"
    CHUNK = "chunk"
    REQUIREMENT = "requirement"
    EVIDENCE = "evidence"


def _proposal_id(
    *,
    scope_id: str,
    target_entity_type: LifecycleTargetType,
    target_id: str,
    action: LifecycleProposalAction,
    reason: str,
    proposed_by: str,
    run_id: str | None,
    correlation_id: str,
) -> str:
    payload = json.dumps(
        {
            "action": action.value,
            "correlation_id": correlation_id,
            "proposed_by": proposed_by,
            "reason": reason,
            "run_id": run_id,
            "scope_id": scope_id,
            "target_entity_type": target_entity_type.value,
            "target_id": target_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "proposal_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LifecycleProposal(DomainModel):
    """Scope-bound agent proposal; policy/reviewer owns actual mutations."""

    proposal_id: str = ""
    scope_id: str = Field(min_length=1)
    target_entity_type: LifecycleTargetType
    target_id: str = Field(min_length=1)
    action: LifecycleProposalAction
    reason: str = Field(min_length=1)
    proposed_by: str = Field(min_length=1)
    run_id: str | None = None
    correlation_id: str = Field(min_length=1)
    status: LifecycleProposalStatus = LifecycleProposalStatus.PENDING
    policy_version: str | None = None
    rule_results: tuple[str, ...] = ()
    decision_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> Self:
        reason = canonicalize_text(self.reason).strip()
        proposed_by = self.proposed_by.strip()
        correlation_id = self.correlation_id.strip()
        target_id = self.target_id.strip()
        run_id = self.run_id.strip() if self.run_id else None
        if not all((reason, proposed_by, correlation_id, target_id)):
            raise ValueError("proposal identity fields cannot be blank")
        if (
            self.action is not LifecycleProposalAction.PROPOSE_SOFT_DELETE
            and self.target_entity_type is not LifecycleTargetType.DOCUMENT_VERSION
        ):
            raise ValueError("lifecycle-state proposals require a document_version")
        if self.status is LifecycleProposalStatus.PENDING and any(
            (self.policy_version, self.rule_results, self.decision_reason)
        ):
            raise ValueError("pending proposal cannot contain a decision")
        for name in ("created_at", "updated_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("proposal timestamps must be timezone-aware")
            object.__setattr__(self, name, value.astimezone(UTC))
        expected = _proposal_id(
            scope_id=self.scope_id,
            target_entity_type=self.target_entity_type,
            target_id=target_id,
            action=self.action,
            reason=reason,
            proposed_by=proposed_by,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        if self.proposal_id and self.proposal_id != expected:
            raise ValueError("proposal_id does not match proposal identity")
        object.__setattr__(self, "proposal_id", expected)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "proposed_by", proposed_by)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        return self
