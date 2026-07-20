"""Exact lifecycle transition policy for canonical DocumentVersion rows."""

from __future__ import annotations

from open_deep_research.knowledge.models import VersionLifecycleStatus
from open_deep_research.knowledge.repositories import InvalidTransitionError


ALLOWED_VERSION_TRANSITIONS: dict[
    VersionLifecycleStatus, frozenset[VersionLifecycleStatus]
] = {
    VersionLifecycleStatus.CANDIDATE: frozenset(
        {
            VersionLifecycleStatus.ACTIVE,
            VersionLifecycleStatus.QUARANTINED,
            VersionLifecycleStatus.ARCHIVED,
        }
    ),
    VersionLifecycleStatus.ACTIVE: frozenset(
        {
            VersionLifecycleStatus.STALE,
            VersionLifecycleStatus.SUPERSEDED,
            VersionLifecycleStatus.QUARANTINED,
            VersionLifecycleStatus.ARCHIVED,
        }
    ),
    VersionLifecycleStatus.STALE: frozenset(
        {
            VersionLifecycleStatus.ACTIVE,
            VersionLifecycleStatus.SUPERSEDED,
            VersionLifecycleStatus.ARCHIVED,
        }
    ),
    VersionLifecycleStatus.QUARANTINED: frozenset(
        {
            VersionLifecycleStatus.CANDIDATE,
            VersionLifecycleStatus.ARCHIVED,
        }
    ),
    VersionLifecycleStatus.SUPERSEDED: frozenset(
        {VersionLifecycleStatus.ARCHIVED}
    ),
    VersionLifecycleStatus.ARCHIVED: frozenset(),
}


def ensure_version_transition(
    before: VersionLifecycleStatus,
    after: VersionLifecycleStatus,
) -> bool:
    """Validate one transition; return False for an idempotent same-state call."""
    if before is after:
        return False
    if after not in ALLOWED_VERSION_TRANSITIONS[before]:
        raise InvalidTransitionError(
            f"invalid document-version transition: {before.value}->{after.value}"
        )
    return True
