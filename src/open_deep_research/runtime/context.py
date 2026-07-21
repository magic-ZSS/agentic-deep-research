"""Recovery contracts for compact state references to transient evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.evidence.run_store import (
    RunEvidenceContext,
    SQLiteRunEvidenceStore,
)
from open_deep_research.knowledge.ids import scope_id_for
from open_deep_research.runtime.identity import RuntimeIdentity


class RunEvidenceReference(BaseModel):
    """Small checkpoint-safe pointer; never embeds retrieved source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    retention_status: str = "active"


async def reopen_run_evidence(
    reference: RunEvidenceReference,
    *,
    identity: RuntimeIdentity,
    database_path: str,
) -> tuple[SQLiteRunEvidenceStore, list]:
    """Reopen one authorized run store and resolve every checkpointed ID."""
    scope_id = scope_id_for(identity.tenant_id, identity.project_id, identity.user_id, "private")
    context = RunEvidenceContext(scope_id=scope_id, run_id=reference.run_id)
    store = SQLiteRunEvidenceStore(database_path)
    bundles = []
    for evidence_id in reference.evidence_ids:
        bundles.append(await store.resolve(context, evidence_id))
    return store, bundles
