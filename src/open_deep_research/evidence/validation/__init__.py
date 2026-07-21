"""Claim-level evidence resolution and validation."""

from open_deep_research.evidence.validation.resolver import (
    EvidenceResolver,
    ResolvedEvidence,
)
from open_deep_research.evidence.validation.validator import CitationValidator

__all__ = ["CitationValidator", "EvidenceResolver", "ResolvedEvidence"]
