"""Deterministically materialize a research brief into explicit requirements."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime
from typing import Protocol, Self

from pydantic import Field, model_validator

from open_deep_research.knowledge.ids import (
    canonicalize_text,
    requirement_id_for,
    sha256_bytes,
    stable_id,
)
from open_deep_research.knowledge.models import DomainModel, utc_now


class RequirementDraft(DomainModel):
    """Extractor output before stable identity and ordering are assigned."""

    text: str = Field(min_length=1)
    required: bool = True
    acceptance_hint: str | None = None
    priority: int = Field(default=0, ge=0)
    aspects: tuple[str, ...] = ()

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        """Normalize textual fields while rejecting empty values."""
        text = canonicalize_text(self.text).strip()
        if not text:
            raise ValueError("requirement text cannot be blank")
        hint = (
            canonicalize_text(self.acceptance_hint).strip()
            if self.acceptance_hint
            else None
        )
        aspects = tuple(
            canonicalize_text(aspect).strip() for aspect in self.aspects
        )
        if any(not aspect for aspect in aspects):
            raise ValueError("requirement aspects cannot be blank")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "acceptance_hint", hint or None)
        object.__setattr__(self, "aspects", tuple(sorted(set(aspects))))
        return self


class PlannedRequirement(RequirementDraft):
    """Stable, run-scoped requirement used by retrieval and completion gates."""

    requirement_id: str = ""
    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    brief_sha256: str = Field(min_length=64, max_length=64)
    ordinal: int = Field(ge=0)

    @model_validator(mode="after")
    def populate_identity(self) -> Self:
        """Populate an identity derived only from stable plan inputs."""
        # Use the canonical Requirement identity so Evidence rows can bind to the
        # materialized plan without an ID translation layer.
        expected = requirement_id_for(
            self.scope_id,
            self.run_id,
            None,
            self.text,
            None,
        )
        if self.requirement_id and self.requirement_id != expected:
            raise ValueError("requirement_id does not match requirement inputs")
        object.__setattr__(self, "requirement_id", expected)
        return self


class RequirementSet(DomainModel):
    """Immutable materialized plan and extraction provenance for one run."""

    plan_id: str = ""
    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    research_brief: str = Field(min_length=1)
    brief_sha256: str = Field(min_length=64, max_length=64)
    extractor_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    requirements: tuple[PlannedRequirement, ...] = Field(min_length=1)
    used_fallback: bool = False
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        """Return stable requirement IDs in normalized execution order."""
        return tuple(item.requirement_id for item in self.requirements)

    @property
    def research_brief_hash(self) -> str:
        """Expose the plan-document spelling for brief hash compatibility."""
        return self.brief_sha256

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Validate trace integrity and derive the stable plan ID."""
        brief = canonicalize_text(self.research_brief).strip()
        if not brief:
            raise ValueError("research_brief cannot be blank")
        expected_hash = sha256_bytes(brief.encode("utf-8"))
        if self.brief_sha256 != expected_hash:
            raise ValueError("brief_sha256 does not match research_brief")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.used_fallback != (self.fallback_reason is not None):
            raise ValueError("fallback trace fields are inconsistent")
        if tuple(item.ordinal for item in self.requirements) != tuple(
            range(len(self.requirements))
        ):
            raise ValueError("requirement ordinals must be contiguous")
        if any(
            item.scope_id != self.scope_id
            or item.run_id != self.run_id
            or item.brief_sha256 != self.brief_sha256
            for item in self.requirements
        ):
            raise ValueError("requirements must belong to this plan scope and run")
        if len(set(self.requirement_ids)) != len(self.requirements):
            raise ValueError("requirement IDs must be unique")
        expected_plan_id = stable_id(
            "research_plan",
            self.scope_id,
            self.run_id,
            self.brief_sha256,
            self.extractor_version,
            self.policy_version,
            self.requirement_ids,
        )
        if self.plan_id and self.plan_id != expected_plan_id:
            raise ValueError("plan_id does not match materialized requirements")
        object.__setattr__(self, "research_brief", brief)
        object.__setattr__(self, "plan_id", expected_plan_id)
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        return self


class RequirementExtractor(Protocol):
    """Optional extraction boundary; implementations may be local or model-backed."""

    def extract(
        self,
        *,
        research_brief: str,
        scope_id: str,
        run_id: str,
    ) -> Sequence[RequirementDraft] | Awaitable[Sequence[RequirementDraft]]:
        """Extract zero or more drafts without assigning durable IDs."""
        ...


class RequirementMaterializer:
    """Normalize extractor output and guarantee a stable, non-empty plan."""

    def __init__(
        self,
        *,
        extractor: RequirementExtractor | None = None,
        extractor_version: str = "fallback-v1",
        policy_version: str = "requirement-policy-v1",
    ) -> None:
        self._extractor = extractor
        self._extractor_version = extractor_version.strip()
        self._policy_version = policy_version.strip()
        if not self._extractor_version or not self._policy_version:
            raise ValueError("extractor and policy versions cannot be blank")

    async def materialize(
        self,
        *,
        research_brief: str,
        scope_id: str,
        run_id: str,
        created_at: datetime | None = None,
    ) -> RequirementSet:
        """Create a deterministic RequirementSet, falling back on any failure."""
        brief = canonicalize_text(research_brief).strip()
        scope_id = scope_id.strip()
        run_id = run_id.strip()
        if not brief or not scope_id or not run_id:
            raise ValueError("research_brief, scope_id, and run_id cannot be blank")

        fallback_reason: str | None = None
        drafts: Sequence[RequirementDraft] = ()
        if self._extractor is None:
            fallback_reason = "extractor_not_configured"
        else:
            try:
                result = self._extractor.extract(
                    research_brief=brief,
                    scope_id=scope_id,
                    run_id=run_id,
                )
                if inspect.isawaitable(result):
                    result = await result
                drafts = tuple(result)
                if not drafts:
                    fallback_reason = "extractor_returned_empty"
            except Exception as exc:  # extraction is an optional capability
                fallback_reason = f"extractor_failed:{type(exc).__name__}"

        if fallback_reason is None:
            try:
                drafts = self._normalize_drafts(drafts)
            except Exception as exc:
                fallback_reason = f"extractor_output_invalid:{type(exc).__name__}"
            else:
                if not drafts:
                    fallback_reason = "extractor_normalized_to_empty"
        if fallback_reason is not None:
            drafts = (RequirementDraft(text=brief, required=True),)

        brief_sha256 = sha256_bytes(brief.encode("utf-8"))
        requirements = tuple(
            PlannedRequirement(
                **draft.model_dump(exclude={"schema_version"}),
                scope_id=scope_id,
                run_id=run_id,
                brief_sha256=brief_sha256,
                ordinal=ordinal,
            )
            for ordinal, draft in enumerate(drafts)
        )
        return RequirementSet(
            scope_id=scope_id,
            run_id=run_id,
            research_brief=brief,
            brief_sha256=brief_sha256,
            extractor_version=self._extractor_version,
            policy_version=self._policy_version,
            requirements=requirements,
            used_fallback=fallback_reason is not None,
            fallback_reason=fallback_reason,
            created_at=created_at or utc_now(),
        )

    @staticmethod
    def _normalize_drafts(
        drafts: Sequence[RequirementDraft],
    ) -> tuple[RequirementDraft, ...]:
        """Deduplicate and sort drafts independently of extractor output order."""
        unique: dict[tuple[object, ...], RequirementDraft] = {}
        for draft in drafts:
            if not isinstance(draft, RequirementDraft):
                raise TypeError("extractor must return RequirementDraft instances")
            key = (
                draft.text.casefold(),
                draft.required,
                (draft.acceptance_hint or "").casefold(),
                draft.priority,
                tuple(aspect.casefold() for aspect in draft.aspects),
            )
            unique[key] = draft
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    not item.required,
                    -item.priority,
                    item.text.casefold(),
                    item.acceptance_hint or "",
                    item.aspects,
                ),
            )
        )
