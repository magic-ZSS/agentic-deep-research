from itertools import product

import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.lifecycle.models import (
    LifecycleProposal,
    LifecycleProposalAction,
    LifecycleTargetType,
)
from open_deep_research.knowledge.lifecycle.policy import (
    ALLOWED_VERSION_TRANSITIONS,
    ensure_version_transition,
)
from open_deep_research.knowledge.models import VersionLifecycleStatus
from open_deep_research.knowledge.repositories import InvalidTransitionError


EXPECTED = {
    VersionLifecycleStatus.CANDIDATE: {
        VersionLifecycleStatus.ACTIVE,
        VersionLifecycleStatus.QUARANTINED,
        VersionLifecycleStatus.ARCHIVED,
    },
    VersionLifecycleStatus.ACTIVE: {
        VersionLifecycleStatus.STALE,
        VersionLifecycleStatus.SUPERSEDED,
        VersionLifecycleStatus.QUARANTINED,
        VersionLifecycleStatus.ARCHIVED,
    },
    VersionLifecycleStatus.STALE: {
        VersionLifecycleStatus.ACTIVE,
        VersionLifecycleStatus.SUPERSEDED,
        VersionLifecycleStatus.ARCHIVED,
    },
    VersionLifecycleStatus.QUARANTINED: {
        VersionLifecycleStatus.CANDIDATE,
        VersionLifecycleStatus.ARCHIVED,
    },
    VersionLifecycleStatus.SUPERSEDED: {VersionLifecycleStatus.ARCHIVED},
    VersionLifecycleStatus.ARCHIVED: set(),
}


def test_exact_transition_matrix() -> None:
    assert {key: set(value) for key, value in ALLOWED_VERSION_TRANSITIONS.items()} == EXPECTED
    for before, after in product(VersionLifecycleStatus, repeat=2):
        if before is after:
            assert ensure_version_transition(before, after) is False
        elif after in EXPECTED[before]:
            assert ensure_version_transition(before, after) is True
        else:
            with pytest.raises(InvalidTransitionError):
                ensure_version_transition(before, after)


def test_proposal_surface_is_strict_and_has_no_hard_delete() -> None:
    assert {item.value for item in LifecycleProposalAction} == {
        "propose_stale",
        "propose_quarantine",
        "propose_supersede",
        "propose_soft_delete",
        "propose_ingest",
    }
    with pytest.raises(ValueError):
        LifecycleProposalAction("hard_delete")
    with pytest.raises(ValidationError):
        LifecycleProposal(
            scope_id="scope",
            target_entity_type=LifecycleTargetType.EVIDENCE,
            target_id="evidence",
            action=LifecycleProposalAction.PROPOSE_STALE,
            reason="old",
            proposed_by="agent",
            run_id="run",
            correlation_id="correlation",
        )


def test_proposal_id_is_stable() -> None:
    values = dict(
        scope_id="scope",
        target_entity_type=LifecycleTargetType.DOCUMENT_VERSION,
        target_id="version",
        action=LifecycleProposalAction.PROPOSE_STALE,
        reason="  source is old\r\n",
        proposed_by="agent",
        run_id="run",
        correlation_id="correlation",
    )
    assert LifecycleProposal(**values).proposal_id == LifecycleProposal(**values).proposal_id
