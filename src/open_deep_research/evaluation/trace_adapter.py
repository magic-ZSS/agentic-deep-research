"""Project trace normalization and optional DeepEval conversion."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.evaluation.deepeval_adapter import (
    DeepEvalUnavailableError,
    _guarded_deepeval_import,
    deepeval_version,
)
from open_deep_research.evaluation.experiment_models import MergedEvaluationCase


class TraceEvent(BaseModel):
    """Small stable trace contract independent of DeepEval internals."""

    model_config = ConfigDict(extra="forbid")
    event_id: str
    parent_id: str | None = None
    kind: str
    name: str
    sequence: int = Field(ge=0)
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    status: str = "completed"


class NormalizedTrace(BaseModel):
    """Comparable view used by smoke metrics and the optional adapter."""

    model_config = ConfigDict(extra="forbid")
    plan: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_context: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def normalize_trace(events: list[TraceEvent]) -> NormalizedTrace:
    """Sort parallel events deterministically and retain their parent identities."""
    ordered = sorted(events, key=lambda item: (item.sequence, item.event_id))
    children: dict[str | None, list[str]] = defaultdict(list)
    for item in ordered:
        children[item.parent_id].append(item.event_id)
    tool_calls: list[dict[str, Any]] = []
    contexts: list[str] = []
    plans: list[str] = []
    errors: list[str] = []
    for item in ordered:
        if item.kind == "plan":
            value = item.output if isinstance(item.output, list) else [str(item.output)]
            plans.extend(str(step) for step in value if str(step).strip())
        if item.kind == "tool":
            tool_calls.append(
                {
                    "name": item.name,
                    "input_parameters": item.input,
                    "output": item.output,
                    "parent_id": item.parent_id,
                }
            )
        if item.kind == "retriever":
            values = item.output if isinstance(item.output, list) else [item.output]
            contexts.extend(str(value) for value in values if value)
        if item.status != "completed":
            errors.append(f"{item.event_id}:{item.status}")
    return NormalizedTrace(
        plan=plans,
        tool_calls=tool_calls,
        retrieval_context=contexts,
        errors=errors,
    )


def to_deepeval_full_case(
    case: MergedEvaluationCase,
    output: str,
    trace: NormalizedTrace,
    *,
    available_tools: list[str],
    variant_id: str,
) -> Any:
    """Build a public DeepEval LLMTestCase only after an explicit full request."""
    installed = deepeval_version()
    if installed != "4.1.1":
        raise DeepEvalUnavailableError("full evaluation requires deepeval==4.1.1")
    expected = case.expected_tools_by_variant.get(variant_id, [])
    unknown = set(expected) - set(available_tools)
    if unknown:
        raise ValueError(f"expected tools are absent from registry: {sorted(unknown)}")
    with _guarded_deepeval_import():
        from deepeval.test_case import LLMTestCase, ToolCall  # type: ignore[import-not-found]

        return LLMTestCase(
            input=case.prompt,
            actual_output=output,
            expected_output=case.expected_output,
            retrieval_context=trace.retrieval_context or None,
            tools_called=[
                ToolCall(
                    name=item["name"],
                    input_parameters=item["input_parameters"],
                    output=item["output"],
                )
                for item in trace.tool_calls
            ],
            expected_tools=[ToolCall(name=name) for name in expected],
            metadata={
                "case_id": case.case_id,
                "variant_id": variant_id,
                "plan": trace.plan,
                "available_tools": available_tools,
            },
        )
