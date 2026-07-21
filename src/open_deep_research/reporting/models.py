"""Checkpoint-safe schemas for citation validation and report repair."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def stable_hash(prefix: str, payload: Any) -> str:
    """Return a deterministic identifier for JSON-compatible data."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def text_hash(text: str) -> str:
    """Hash text without normalizing away meaningful report bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReportingModel(BaseModel):
    """Strict JSON/checkpoint-safe reporting base model."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class CitationKey(ReportingModel):
    """Globally stable citation identity; locators do not alter identity."""

    source_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)

    @property
    def value(self) -> str:
        """Return a reversible string representation."""
        return f"{self.source_id}|{self.version_id}"


class ClaimType(StrEnum):
    """Deterministic claim categories used by hard validation rules."""

    FACTUAL = "factual"
    NUMERIC = "numeric"
    CORPORATE_ATTRIBUTION = "corporate_attribution"
    SUBJECTIVE = "subjective"


class LinkRelation(StrEnum):
    """Claim-level evidence relationship."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class LinkOrigin(StrEnum):
    """Whether a link came from the draft or later retrieval."""

    EXPLICIT_DRAFT_CITATION = "explicit_draft_citation"
    SUPPLEMENTAL_RETRIEVAL = "supplemental_retrieval"
    REPAIR_REBIND = "repair_rebind"


class TemporalStatus(StrEnum):
    """Time applicability of evidence for a claim."""

    CURRENT = "current"
    STALE = "stale"
    FUTURE = "future"
    UNKNOWN = "unknown"


class AuthorityStatus(StrEnum):
    """Authority decision independent from entailment."""

    SUFFICIENT = "sufficient"
    SELF_REPORTED_ONLY = "self_reported_only"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class ValidationStatus(StrEnum):
    """Required five-way claim validation vocabulary."""

    FULLY_SUPPORTED = "fully_supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_CHECKABLE = "not_checkable"


class RequiredAction(StrEnum):
    """Programmatic disposition selected from validation status."""

    KEEP = "keep"
    QUALIFY = "qualify"
    REMOVE = "remove"
    MARK_INSUFFICIENT = "mark_insufficient"
    REPAIR_REBIND = "repair_rebind"


class ReportSection(ReportingModel):
    """Stable report section boundary used for local repair."""

    section_id: str = ""
    ordinal: int = Field(ge=0)
    heading: str
    text: str
    canonical_hash: str = ""

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive and validate the stable section identity and hash."""
        expected_hash = text_hash(self.text)
        expected_id = stable_hash(
            "section", {"heading": self.heading, "ordinal": self.ordinal}
        )
        if self.section_id and self.section_id != expected_id:
            raise ValueError("section_id does not match section identity")
        if self.canonical_hash and self.canonical_hash != expected_hash:
            raise ValueError("canonical_hash does not match section text")
        object.__setattr__(self, "section_id", expected_id)
        object.__setattr__(self, "canonical_hash", expected_hash)
        return self


class DraftReport(ReportingModel):
    """Legacy Writer output split into stable sections."""

    draft_id: str = ""
    raw_text: str
    sections: tuple[ReportSection, ...]

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive and validate the draft identity."""
        expected = stable_hash(
            "draft", {"raw_text": self.raw_text, "sections": self.sections}
        )
        if self.draft_id and self.draft_id != expected:
            raise ValueError("draft_id does not match draft contents")
        if len({section.section_id for section in self.sections}) != len(self.sections):
            raise ValueError("draft section IDs must be unique")
        object.__setattr__(self, "draft_id", expected)
        return self


class AtomicClaim(ReportingModel):
    """One independently validated factual assertion in one section."""

    claim_id: str = ""
    requirement_ids: tuple[str, ...] = ()
    section_id: str
    text: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    claim_type: ClaimType = ClaimType.FACTUAL
    temporal_scope: datetime | None = None
    cited_evidence_ids: tuple[str, ...] = ()
    cited_citation_keys: tuple[CitationKey, ...] = ()
    extraction_version: str = "deterministic-v1"

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Normalize temporal scope and derive the stable claim ID."""
        if self.span_end <= self.span_start:
            raise ValueError("claim span must advance")
        temporal = self.temporal_scope
        if temporal is not None:
            if temporal.tzinfo is None or temporal.utcoffset() is None:
                raise ValueError("temporal_scope must be timezone-aware")
            object.__setattr__(self, "temporal_scope", temporal.astimezone(UTC))
        expected = stable_hash(
            "claim",
            {
                "section_id": self.section_id,
                "span_start": self.span_start,
                "span_end": self.span_end,
                "text": self.text,
                "version": self.extraction_version,
            },
        )
        if self.claim_id and self.claim_id != expected:
            raise ValueError("claim_id does not match claim identity")
        object.__setattr__(self, "claim_id", expected)
        return self


class ClaimEvidenceLink(ReportingModel):
    """Auditable result for one claim/evidence pair."""

    link_id: str = ""
    claim_id: str
    evidence_id: str
    chunk_id: str
    citation_key: CitationKey
    relation: LinkRelation
    origin: LinkOrigin
    entailment_score: float = Field(ge=0, le=1)
    directness: str
    temporal_status: TemporalStatus
    authority_status: AuthorityStatus
    locator: str
    rationale: str
    validator_version: str
    accepted: bool = False

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive and validate the stable claim-evidence link ID."""
        expected = stable_hash(
            "link",
            {
                "claim_id": self.claim_id,
                "evidence_id": self.evidence_id,
                "chunk_id": self.chunk_id,
                "origin": self.origin,
                "validator_version": self.validator_version,
            },
        )
        if self.link_id and self.link_id != expected:
            raise ValueError("link_id does not match link identity")
        object.__setattr__(self, "link_id", expected)
        return self


class ValidationResult(ReportingModel):
    """Final five-way result for exactly one AtomicClaim."""

    result_id: str = ""
    claim_id: str
    status: ValidationStatus
    links: tuple[ClaimEvidenceLink, ...]
    failed_checks: tuple[str, ...] = ()
    required_action: RequiredAction
    confidence: float = Field(ge=0, le=1)
    policy_version: str
    audit_id: str = ""

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive stable validation and audit identities."""
        payload = {
            "claim_id": self.claim_id,
            "links": self.links,
            "policy_version": self.policy_version,
            "status": self.status,
        }
        expected = stable_hash("validation", payload)
        audit = stable_hash("citation_audit", payload)
        if self.result_id and self.result_id != expected:
            raise ValueError("result_id does not match validation identity")
        if self.audit_id and self.audit_id != audit:
            raise ValueError("audit_id does not match validation identity")
        object.__setattr__(self, "result_id", expected)
        object.__setattr__(self, "audit_id", audit)
        return self


class RepairPatch(ReportingModel):
    """Hash-guarded replacement for one report section."""

    patch_id: str = ""
    section_id: str
    original_hash: str
    target_claim_ids: tuple[str, ...]
    replacement_text: str
    preserved_claim_ids: tuple[str, ...] = ()
    reason: str

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive and validate the hash-guarded patch identity."""
        expected = stable_hash(
            "patch",
            {
                "original_hash": self.original_hash,
                "replacement_text": self.replacement_text,
                "section_id": self.section_id,
                "targets": self.target_claim_ids,
            },
        )
        if self.patch_id and self.patch_id != expected:
            raise ValueError("patch_id does not match patch identity")
        object.__setattr__(self, "patch_id", expected)
        return self


class SourceRegistryEntry(ReportingModel):
    """One source/version entry in the programmatic bibliography."""

    citation_key: CitationKey
    display_number: int = Field(ge=1)
    title: str
    publisher: str | None = None
    canonical_uri: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    locators_used: tuple[str, ...] = ()


class CitationValidationArtifact(ReportingModel):
    """Complete checkpointable reporting audit artifact."""

    artifact_id: str = ""
    mode: Literal["audit", "enforce"]
    draft_id: str
    claims: tuple[AtomicClaim, ...]
    results: tuple[ValidationResult, ...]
    patches: tuple[RepairPatch, ...]
    registry: tuple[SourceRegistryEntry, ...]
    final_report_hash: str
    policy_version: str

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Derive the checkpoint-stable artifact identity."""
        expected = stable_hash(
            "citation_artifact",
            {
                "draft_id": self.draft_id,
                "mode": self.mode,
                "policy_version": self.policy_version,
                "results": self.results,
            },
        )
        if self.artifact_id and self.artifact_id != expected:
            raise ValueError("artifact_id does not match artifact identity")
        object.__setattr__(self, "artifact_id", expected)
        return self


class CitationPipelineOutput(ReportingModel):
    """Final report plus its machine-readable audit artifact."""

    final_report: str
    artifact: CitationValidationArtifact
