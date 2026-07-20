"""Deterministic evidence coverage policy for materialized requirements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from open_deep_research.evidence.models import (
    Evidence,
    EvidenceDirectness,
    EvidenceRelation,
    EvidenceValidationStatus,
)
from open_deep_research.knowledge.models import (
    AuthorityClass,
    Chunk,
    Document,
    DocumentVersion,
    DomainModel,
    Source,
    VersionLifecycleStatus,
    utc_now,
)
from open_deep_research.research.requirements import (
    PlannedRequirement,
    RequirementSet,
)


class CoverageStatus(StrEnum):
    """Programmatic coverage outcome for one requirement."""

    COVERED = "covered"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTED = "contradicted"


class GovernedEvidenceRef(DomainModel):
    """Complete resolvable Evidence-to-Source chain used by hard gates."""

    evidence: Evidence
    chunk: Chunk
    version: DocumentVersion
    document: Document
    source: Source
    bound_requirement_id: str | None = None
    run_id: str | None = None
    validated_for_run: bool = False

    @model_validator(mode="after")
    def validate_chain(self) -> Self:
        """Reject incomplete, cross-scope, or incorrectly linked chains."""
        if not (
            self.evidence.scope_id
            == self.chunk.scope_id
            == self.version.scope_id
            == self.document.scope_id
            == self.source.scope_id
        ):
            raise ValueError("evidence chain crosses knowledge scopes")
        if not (
            self.evidence.chunk_id == self.chunk.chunk_id
            and self.chunk.version_id == self.version.version_id
            and self.version.document_id == self.document.document_id
            and self.document.source_id == self.source.source_id
        ):
            raise ValueError("evidence chain IDs are not resolvable")
        if self.validated_for_run:
            if not self.run_id:
                raise ValueError("run-validated evidence requires run_id")
            if self.version.lifecycle_status is not VersionLifecycleStatus.CANDIDATE:
                raise ValueError("run-validated evidence must remain candidate")
            if self.evidence.validation_status is not EvidenceValidationStatus.PENDING:
                raise ValueError("run-validated evidence keeps its canonical status pending")
        return self


class RequirementCoverage(DomainModel):
    """Observable policy assessment for a single planned requirement."""

    requirement_id: str
    required: bool
    status: CoverageStatus
    evidence_ids: tuple[str, ...] = ()
    direct_evidence_count: int = Field(default=0, ge=0)
    distinct_source_count: int = Field(default=0, ge=0)
    coverage_score: float = Field(default=0, ge=0, le=1)
    missing_aspects: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    policy_version: str = Field(min_length=1)


class CoverageReport(DomainModel):
    """Run-level collection of per-requirement coverage assessments."""

    plan_id: str
    scope_id: str
    run_id: str
    policy_version: str = Field(min_length=1)
    as_of: datetime = Field(default_factory=utc_now)
    assessments: tuple[RequirementCoverage, ...]

    @model_validator(mode="after")
    def normalize_and_validate(self) -> Self:
        """Require unique assessments and an aware evaluation timestamp."""
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        ids = tuple(item.requirement_id for item in self.assessments)
        if len(set(ids)) != len(ids):
            raise ValueError("coverage assessments must be unique by requirement")
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))
        return self

    @property
    def covered_requirement_ids(self) -> tuple[str, ...]:
        """Return covered requirement IDs in plan order."""
        return tuple(
            item.requirement_id
            for item in self.assessments
            if item.status is CoverageStatus.COVERED
        )

    @property
    def required_gap_ids(self) -> tuple[str, ...]:
        """Return required IDs that failed the coverage gate."""
        return tuple(
            item.requirement_id
            for item in self.assessments
            if item.required and item.status is not CoverageStatus.COVERED
        )

    @property
    def missing_aspects(self) -> tuple[str, ...]:
        """Return a deterministic flattened list of explicit research gaps."""
        return tuple(
            aspect
            for item in self.assessments
            if item.required and item.status is not CoverageStatus.COVERED
            for aspect in item.missing_aspects
        )

    @property
    def required_complete(self) -> bool:
        """Report whether all required requirements passed the gate."""
        return not self.required_gap_ids


class CoveragePolicy(DomainModel):
    """Versioned hard thresholds that no prompt or extractor can bypass."""

    policy_version: str = "coverage-policy-v1"
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    min_direct_evidence: int = Field(default=1, ge=1)
    min_distinct_sources: int = Field(default=1, ge=1)
    accepted_authorities: tuple[AuthorityClass, ...] = (
        AuthorityClass.OFFICIAL,
        AuthorityClass.PRIMARY,
        AuthorityClass.SECONDARY,
    )
    max_evidence_age_days: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Normalize accepted authority values and reject an empty allowlist."""
        if not self.policy_version.strip():
            raise ValueError("policy_version cannot be blank")
        authorities = tuple(
            sorted(set(self.accepted_authorities), key=lambda item: item.value)
        )
        if not authorities:
            raise ValueError("accepted_authorities cannot be empty")
        object.__setattr__(self, "accepted_authorities", authorities)
        return self

    def assess_requirement(
        self,
        requirement: PlannedRequirement,
        evidence_refs: tuple[GovernedEvidenceRef, ...],
        *,
        as_of: datetime,
    ) -> RequirementCoverage:
        """Assess only evidence explicitly bound to this requirement ID."""
        instant = self._aware_utc(as_of)
        relevant = tuple(
            ref
            for ref in evidence_refs
            if (ref.bound_requirement_id or ref.evidence.requirement_id)
            == requirement.requirement_id
            and (not ref.validated_for_run or ref.run_id == requirement.run_id)
        )
        usable_supports: list[GovernedEvidenceRef] = []
        usable_conflicts: list[GovernedEvidenceRef] = []
        rejection_reasons: set[str] = set()

        for ref in relevant:
            failures = self._quality_failures(ref, instant)
            if failures:
                rejection_reasons.update(failures)
                continue
            if ref.evidence.relation is EvidenceRelation.CONTRADICTS:
                usable_conflicts.append(ref)
            elif ref.evidence.relation is EvidenceRelation.SUPPORTS:
                usable_supports.append(ref)
            else:
                rejection_reasons.add("evidence_is_context_only")

        if usable_conflicts:
            conflict_ids = tuple(
                sorted(ref.evidence.evidence_id for ref in usable_conflicts)
            )
            return RequirementCoverage(
                requirement_id=requirement.requirement_id,
                required=requirement.required,
                status=CoverageStatus.CONTRADICTED,
                evidence_ids=conflict_ids,
                direct_evidence_count=len(usable_supports),
                distinct_source_count=len(
                    {ref.source.source_id for ref in usable_supports}
                ),
                coverage_score=0,
                missing_aspects=("resolve_conflicting_evidence",),
                reasons=("validated_direct_contradiction_present",),
                policy_version=self.policy_version,
            )

        direct_count = len(usable_supports)
        source_count = len({ref.source.source_id for ref in usable_supports})
        enough_direct = direct_count >= self.min_direct_evidence
        enough_sources = source_count >= self.min_distinct_sources
        evidence_ids = tuple(
            sorted(ref.evidence.evidence_id for ref in usable_supports)
        )
        coverage_score = min(
            1.0,
            min(
                direct_count / self.min_direct_evidence,
                source_count / self.min_distinct_sources,
            ),
        )
        if enough_direct and enough_sources:
            return RequirementCoverage(
                requirement_id=requirement.requirement_id,
                required=requirement.required,
                status=CoverageStatus.COVERED,
                evidence_ids=evidence_ids,
                direct_evidence_count=direct_count,
                distinct_source_count=source_count,
                coverage_score=coverage_score,
                reasons=("hard_coverage_thresholds_satisfied",),
                policy_version=self.policy_version,
            )

        missing_aspects: list[str] = []
        if not enough_direct:
            missing_aspects.append("direct_validated_evidence")
        if not enough_sources:
            missing_aspects.append("distinct_authoritative_sources")
        status = CoverageStatus.PARTIAL if relevant else CoverageStatus.MISSING
        reasons = rejection_reasons or {"no_evidence_for_requirement"}
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            required=requirement.required,
            status=status,
            evidence_ids=evidence_ids,
            direct_evidence_count=direct_count,
            distinct_source_count=source_count,
            coverage_score=coverage_score,
            missing_aspects=tuple(missing_aspects),
            reasons=tuple(sorted(reasons)),
            policy_version=self.policy_version,
        )

    def assess(
        self,
        requirement_set: RequirementSet,
        evidence_refs: tuple[GovernedEvidenceRef, ...],
        *,
        as_of: datetime | None = None,
    ) -> CoverageReport:
        """Assess all requirements in their stable plan order."""
        instant = self._aware_utc(as_of or utc_now())
        return CoverageReport(
            plan_id=requirement_set.plan_id,
            scope_id=requirement_set.scope_id,
            run_id=requirement_set.run_id,
            policy_version=self.policy_version,
            as_of=instant,
            assessments=tuple(
                self.assess_requirement(item, evidence_refs, as_of=instant)
                for item in requirement_set.requirements
            ),
        )

    def _quality_failures(
        self, ref: GovernedEvidenceRef, as_of: datetime
    ) -> tuple[str, ...]:
        """Return all hard-gate failures for an otherwise linked chain."""
        failures: list[str] = []
        if (
            not ref.validated_for_run
            and ref.evidence.validation_status is not EvidenceValidationStatus.VALIDATED
        ):
            failures.append("evidence_not_validated")
        if ref.evidence.directness is not EvidenceDirectness.DIRECT:
            failures.append("evidence_not_direct")
        if ref.evidence.confidence < self.min_confidence:
            failures.append("evidence_below_confidence_threshold")
        if (
            not ref.validated_for_run
            and ref.version.lifecycle_status is not VersionLifecycleStatus.ACTIVE
        ):
            failures.append("version_not_active")
        if ref.source.authority_class not in self.accepted_authorities:
            failures.append("source_authority_not_accepted")
        if any(
            item.soft_deleted_at is not None
            for item in (
                ref.source,
                ref.document,
                ref.version,
                ref.chunk,
                ref.evidence,
            )
        ):
            failures.append("evidence_chain_soft_deleted")
        if ref.version.valid_from and as_of < ref.version.valid_from:
            failures.append("version_not_yet_valid")
        if ref.version.valid_to and as_of > ref.version.valid_to:
            failures.append("version_no_longer_valid")
        if self.max_evidence_age_days is not None:
            dated_at = ref.version.published_at or ref.version.retrieved_at
            if dated_at > as_of:
                failures.append("evidence_date_is_in_future")
            elif as_of - dated_at > timedelta(days=self.max_evidence_age_days):
                failures.append("evidence_too_old")
        return tuple(failures)

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        """Require and normalize an aware evaluation timestamp."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("coverage timestamps must be timezone-aware")
        return value.astimezone(UTC)
