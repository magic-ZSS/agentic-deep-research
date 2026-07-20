"""Deterministic append-only audit construction for retry-safe transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from open_deep_research.evidence.models import AuditEvent


def governed_audit_event(
    *,
    scope_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    actor_type: str,
    reason: str,
    before_status: str | None,
    after_status: str | None,
    correlation_id: str,
    policy_version: str,
    rule_results: Sequence[str],
    run_id: str | None,
    proposal_id: str | None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> AuditEvent:
    identity = json.dumps(
        {
            "action": action,
            "after": after_status,
            "before": before_status,
            "correlation_id": correlation_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "proposal_id": proposal_id,
            "scope_id": scope_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return AuditEvent(
        event_id="audit_" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        scope_id=scope_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_type=actor_type,
        reason=reason,
        before_status=before_status,
        after_status=after_status,
        correlation_id=correlation_id,
        metadata={
            "policy_version": policy_version,
            "proposal_id": proposal_id,
            "rule_results": list(rule_results),
            "run_id": run_id,
            **dict(extra_metadata or {}),
        },
    )
