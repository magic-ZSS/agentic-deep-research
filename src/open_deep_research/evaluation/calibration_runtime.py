"""Isolated runtime assembly for the explicitly authorized calibration matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from open_deep_research.configuration import Configuration
from open_deep_research.evaluation.experiment_models import ExperimentVariant


class CalibrationConfigurationError(RuntimeError):
    """Reject a paid run before external clients are constructed."""


def validate_calibration_matrix(
    plan: dict[str, Any],
    variants: list[ExperimentVariant],
    requested_variant_ids: list[str] | None,
) -> list[ExperimentVariant]:
    """Require the exact authorized matrix and a single frozen budget contract."""
    expected = list(plan["calibration"]["variants"])
    requested = requested_variant_ids or expected
    if requested != expected:
        raise CalibrationConfigurationError(
            f"calibration variants must be exactly {expected} in that order"
        )
    by_id = {item.variant_id: item for item in variants}
    try:
        selected = [by_id[item] for item in expected]
    except KeyError as exc:
        raise CalibrationConfigurationError(
            f"calibration variant is missing from the ablation manifest: {exc.args[0]}"
        ) from exc
    limits = plan["runtime_limits"]
    if (
        isinstance(limits.get("provider_max_retries"), bool)
        or limits.get("provider_max_retries") != 0
    ):
        raise CalibrationConfigurationError(
            "calibration provider_max_retries must be exactly zero"
        )
    if limits.get("evaluation_final_report_max_attempts") != 1:
        raise CalibrationConfigurationError(
            "calibration final report must use exactly one attempt"
        )
    if limits.get("max_structured_output_retries") != 1:
        raise CalibrationConfigurationError(
            "calibration structured output must use exactly one attempt"
        )
    if limits.get("compression_max_retries") != 1:
        raise CalibrationConfigurationError(
            "calibration compression must use exactly one attempt"
        )
    for variant in variants:
        if variant.budget["max_researcher_iterations"] != limits[
            "max_researcher_iterations"
        ]:
            raise CalibrationConfigurationError(
                "ablation/full-plan max_researcher_iterations drift"
            )
        if variant.budget["max_react_tool_calls"] != limits[
            "max_react_tool_calls"
        ]:
            raise CalibrationConfigurationError(
                "ablation/full-plan max_react_tool_calls drift"
            )
        if variant.budget["timeout_seconds"] != limits["timeout_seconds"]:
            raise CalibrationConfigurationError("ablation/full-plan timeout drift")
    return selected


def build_variant_config(
    *,
    plan: dict[str, Any],
    variant: ExperimentVariant,
    models: dict[str, str],
    run_id: str,
    runtime_root: str | Path,
    experiment_id: str,
) -> dict[str, Any]:
    """Build one path-isolated config without exposing credentials."""
    root = Path(runtime_root).resolve() / run_id
    limits = plan["runtime_limits"]
    configurable: dict[str, Any] = {
        **variant.feature_flags,
        "search_api": variant.search_config["provider"],
        "summarization_model": models["summarization"],
        "research_model": models["research"],
        "compression_model": models["compression"],
        "final_report_model": models["final_report"],
        "summarization_model_max_tokens": limits[
            "summarization_model_max_tokens"
        ],
        "research_model_max_tokens": limits["research_model_max_tokens"],
        "compression_model_max_tokens": limits["compression_model_max_tokens"],
        "final_report_model_max_tokens": limits["final_report_model_max_tokens"],
        "max_concurrent_research_units": limits["max_concurrent_research_units"],
        "max_concurrent_researcher_tool_calls": limits[
            "max_concurrent_researcher_tool_calls"
        ],
        "max_researcher_iterations": limits["max_researcher_iterations"],
        "max_react_tool_calls": limits["max_react_tool_calls"],
        "max_structured_output_retries": limits[
            "max_structured_output_retries"
        ],
        "compression_max_retries": limits["compression_max_retries"],
        "max_retries": limits["provider_max_retries"],
        "_evaluation_final_report_max_attempts": limits[
            "evaluation_final_report_max_attempts"
        ],
        "max_queries_per_search_call": limits["max_queries_per_search_call"],
        "max_results_per_tavily": limits["max_results_per_tavily"],
        "max_web_results_per_query": variant.search_config[
            "max_results_per_query"
        ],
        "max_concurrent_web_requests": limits["max_concurrent_research_units"],
        "allow_clarification": False,
        "print_process_info": False,
        "enable_filesystem_mcp": False,
        "enable_knowledge_mcp": False,
        "mcp_servers": {},
        "mcp_config": None,
        "thread_id": run_id,
        "research_run_id": run_id,
        "knowledge_tenant_id": "phase7-evaluation",
        "knowledge_project_id": experiment_id,
        "knowledge_repository_backend": "sqlite",
        "knowledge_db_path": str(root / "knowledge.db"),
        "knowledge_blob_dir": str(root / "knowledge-blobs"),
        "paperqa_index_dir": str(root / "paperqa-index"),
        "run_evidence_store_backend": "sqlite",
        "run_evidence_db_path": str(root / "run-evidence.db"),
        "checkpointer_backend": "sqlite",
        "checkpoint_db_path": str(root / "checkpoints.db"),
        "checkpoint_store_db_path": str(root / "checkpoint-store.db"),
        "memory_repository_backend": "sqlite",
        "memory_db_path": str(root / "memory.db"),
    }
    config = {"configurable": configurable}
    resolved = Configuration.from_runnable_config(config)
    for name in (
        "max_concurrent_research_units",
        "max_concurrent_researcher_tool_calls",
        "max_researcher_iterations",
        "max_react_tool_calls",
        "max_structured_output_retries",
        "compression_max_retries",
    ):
        if getattr(resolved, name) != configurable[name]:
            raise CalibrationConfigurationError(
                f"environment overrides frozen calibration field: {name}"
            )
    return config


def inject_governed_runtime(config: dict[str, Any]) -> Any | None:
    """Inject the exact same-run retrieval and citation resolution boundary."""
    configuration = Configuration.from_runnable_config(config)
    if not configuration.enable_agentic_rag:
        return None

    from open_deep_research.evidence.validation.resolver import EvidenceResolver
    from open_deep_research.evidence.validation.retriever import ClaimEvidenceRetriever
    from open_deep_research.evidence.validation.validator import CitationValidator
    from open_deep_research.knowledge.models import AuthorityClass
    from open_deep_research.knowledge.retrieval.runtime import get_governed_runtime
    from open_deep_research.reporting.pipeline import CitationPipeline

    configurable = config["configurable"]
    run_id = str(configurable["research_run_id"])
    runtime = get_governed_runtime(config, run_id=run_id)
    configurable["_governed_runtime"] = runtime
    if configuration.citation_validation_mode != "off":
        resolver = EvidenceResolver(
            repository=runtime.repository,
            access=runtime.access,
            scope=runtime.scope,
            run_store=runtime.orchestrator.run_store,
            run_id=runtime.run_id,
        )
        configurable["citation_pipeline"] = CitationPipeline(
            retriever=ClaimEvidenceRetriever(resolver),
            validator=CitationValidator(
                min_entailment=configuration.citation_min_entailment,
                min_authority=AuthorityClass(
                    configuration.citation_min_source_authority
                ),
                require_temporal_validity=(
                    configuration.citation_require_temporal_validity
                ),
                policy_version=configuration.citation_policy_version,
                unsupported_action=configuration.citation_unsupported_action,
            ),
            policy_version=configuration.citation_policy_version,
        )
    return runtime


async def runtime_tool_names(config: dict[str, Any]) -> list[str]:
    """Return the actual Researcher registry without constructing model clients."""
    from open_deep_research.utils import get_all_tools

    tools = await get_all_tools(config)
    return [
        item.name
        if hasattr(item, "name")
        else str(item.get("name", item.get("type", "unknown")))
        for item in tools
    ]
