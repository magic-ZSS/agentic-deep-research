"""Governed knowledge lifecycle public API.

Policy/service imports are lazy so repository Protocol imports remain cycle-free.
"""

from typing import Any

from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalAction,
    LifecycleProposalStatus,
    LifecycleTargetType,
)

__all__ = [
    "ALLOWED_VERSION_TRANSITIONS",
    "KnowledgeLifecycleService",
    "LifecycleProposal",
    "LifecycleProposalAction",
    "LifecycleProposalStatus",
    "LifecycleTargetType",
    "ensure_version_transition",
]


def __getattr__(name: str) -> Any:
    """Resolve repository-dependent exports without eager import cycles."""
    if name in {"ALLOWED_VERSION_TRANSITIONS", "ensure_version_transition"}:
        from open_deep_research.knowledge.lifecycle import policy

        return getattr(policy, name)
    if name == "KnowledgeLifecycleService":
        from open_deep_research.knowledge.lifecycle.service import (
            KnowledgeLifecycleService,
        )

        return KnowledgeLifecycleService
    raise AttributeError(name)
