"""Bind each AtomicClaim independently to explicit and supplemental evidence."""

from __future__ import annotations

from datetime import datetime

from open_deep_research.evidence.validation.resolver import (
    EvidenceResolver,
    ResolvedEvidence,
)
from open_deep_research.knowledge.repositories import RepositoryNotFoundError
from open_deep_research.reporting.models import AtomicClaim, LinkOrigin


class ClaimEvidenceRetriever:
    """Resolve draft citations first and retain supplemental provenance."""

    def __init__(self, resolver: EvidenceResolver) -> None:
        """Create a retriever over one scope-bound resolver."""
        self.resolver = resolver

    async def retrieve(
        self,
        claim: AtomicClaim,
        *,
        as_of: datetime | None,
        supplemental_evidence_ids: tuple[str, ...] = (),
    ) -> list[tuple[ResolvedEvidence, LinkOrigin]]:
        """Return deterministic, claim-local evidence candidates."""
        output: list[tuple[ResolvedEvidence, LinkOrigin]] = []
        seen: set[tuple[str, LinkOrigin]] = set()
        for evidence_id in claim.cited_evidence_ids:
            try:
                resolved = await self.resolver.resolve(evidence_id, as_of=as_of)
            except RepositoryNotFoundError:
                continue
            key = (resolved.evidence.evidence_id, LinkOrigin.EXPLICIT_DRAFT_CITATION)
            if key not in seen:
                output.append((resolved, key[1]))
                seen.add(key)
        for citation_key in claim.cited_citation_keys:
            for resolved in await self.resolver.evidence_for_source(
                citation_key.source_id, as_of=as_of
            ):
                if resolved.version.version_id != citation_key.version_id:
                    continue
                key = (resolved.evidence.evidence_id, LinkOrigin.EXPLICIT_DRAFT_CITATION)
                if key not in seen:
                    output.append((resolved, key[1]))
                    seen.add(key)
        for evidence_id in supplemental_evidence_ids:
            try:
                resolved = await self.resolver.resolve(evidence_id, as_of=as_of)
            except RepositoryNotFoundError:
                continue
            key = (resolved.evidence.evidence_id, LinkOrigin.SUPPLEMENTAL_RETRIEVAL)
            if key not in seen:
                output.append((resolved, key[1]))
                seen.add(key)
        return sorted(
            output,
            key=lambda item: (item[1].value, item[0].evidence.evidence_id),
        )
