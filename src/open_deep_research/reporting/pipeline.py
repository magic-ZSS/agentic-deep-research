"""Independent citation-validation pipeline and LangGraph node adapter."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from open_deep_research.configuration import Configuration
from open_deep_research.evidence.validation.retriever import ClaimEvidenceRetriever
from open_deep_research.evidence.validation.validator import CitationValidator
from open_deep_research.reporting.extraction import (
    ClaimExtractionAdapter,
    DeterministicClaimExtractor,
    parse_draft,
)
from open_deep_research.reporting.models import (
    CitationPipelineOutput,
    CitationValidationArtifact,
    RepairPatch,
    text_hash,
)
from open_deep_research.reporting.registry import SourceRegistryBuilder
from open_deep_research.reporting.renderer import ReportRenderer
from open_deep_research.reporting.repair import ReportRepairer


class CitationPipeline:
    """Coordinate extraction, validation, local repair, registry and render."""

    def __init__(
        self,
        *,
        retriever: ClaimEvidenceRetriever,
        validator: CitationValidator,
        extractor: ClaimExtractionAdapter | None = None,
        repairer: ReportRepairer | None = None,
        registry_builder: SourceRegistryBuilder | None = None,
        renderer: ReportRenderer | None = None,
        policy_version: str = "citation-policy-v1",
    ) -> None:
        """Compose the independently injectable reporting stages."""
        self.retriever = retriever
        self.validator = validator
        self.extractor = extractor or DeterministicClaimExtractor()
        self.repairer = repairer or ReportRepairer()
        self.registry_builder = registry_builder or SourceRegistryBuilder()
        self.renderer = renderer or ReportRenderer()
        self.policy_version = policy_version

    async def run(
        self,
        draft_text: str,
        *,
        mode: Literal["audit", "enforce"],
        requirement_ids: tuple[str, ...] = (),
        as_of: datetime | None = None,
        supplemental_evidence_ids: Mapping[str, tuple[str, ...]] | None = None,
    ) -> CitationPipelineOutput:
        """Run deterministically; enforce never silently returns an unsafe draft."""
        instant = as_of or datetime.now(UTC)
        draft = parse_draft(draft_text)
        claims_value = self.extractor.extract(draft, requirement_ids)
        claims = await claims_value if inspect.isawaitable(claims_value) else claims_value
        claims = tuple(claims)
        supplemental = supplemental_evidence_ids or {}
        results = []
        resolved_by_id = {}
        for claim in claims:
            candidates = await self.retriever.retrieve(
                claim,
                as_of=instant,
                supplemental_evidence_ids=supplemental.get(claim.claim_id, ()),
            )
            for resolved, _ in candidates:
                resolved_by_id[resolved.evidence.evidence_id] = resolved
            results.append(
                await self.validator.validate(claim, candidates, as_of=instant)
            )
        result_tuple = tuple(results)
        registry = self.registry_builder.build(result_tuple, resolved_by_id)
        patches: tuple[RepairPatch, ...] = ()
        if mode == "audit":
            final_report = draft_text
        else:
            patches = self.repairer.create_patches(draft, claims, result_tuple)
            sections = self.repairer.apply(draft, patches)
            final_report = self.renderer.render(
                sections, claims, result_tuple, registry
            )
        artifact = CitationValidationArtifact(
            mode=mode,
            draft_id=draft.draft_id,
            claims=claims,
            results=result_tuple,
            patches=patches,
            registry=registry,
            final_report_hash=text_hash(final_report),
            policy_version=self.policy_version,
        )
        return CitationPipelineOutput(final_report=final_report, artifact=artifact)


async def citation_validation_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """Feature-gated graph node; off is a byte-preserving no-op."""
    configuration = Configuration.from_runnable_config(config)
    if configuration.citation_validation_mode == "off":
        return {}
    configurable = config.get("configurable", {})
    pipeline = configurable.get("citation_pipeline")
    if not isinstance(pipeline, CitationPipeline):
        if configuration.citation_validation_mode == "audit":
            return {
                "citation_validation_error": "citation_pipeline_unavailable"
            }
        return {
            "final_report": "Evidence validation unavailable; unsupported conclusions were withheld.",
            "citation_validation_error": "citation_pipeline_unavailable",
        }
    output = await pipeline.run(
        state.get("final_report", ""),
        mode=configuration.citation_validation_mode,
        requirement_ids=tuple(state.get("requirement_ids", ())),
    )
    return {
        "final_report": output.final_report,
        "citation_validation_artifact": output.artifact.model_dump(mode="json"),
        "citation_claim_ids": [claim.claim_id for claim in output.artifact.claims],
        "citation_validation_result_ids": [
            result.result_id for result in output.artifact.results
        ],
        "citation_registry_keys": [
            entry.citation_key.value for entry in output.artifact.registry
        ],
    }
