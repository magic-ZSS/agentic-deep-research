"""Offline graph recovery tests for Phase 3 programmatic gates."""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from open_deep_research import deep_researcher as graph_module
from open_deep_research.research import (
    CompletionDecision,
    RequirementMaterializer,
    ResearchCompletionDecision,
)
from open_deep_research.state import ResearchQuestion


class _BriefModel:
    def with_structured_output(self, _schema):
        return self

    def with_retry(self, *, stop_after_attempt):
        return self

    def with_config(self, _config):
        return self

    async def ainvoke(self, _messages):
        return ResearchQuestion(research_brief="Verify the governed requirement.")


class _FakeTool:
    def __init__(self, name: str, calls: list[str], output: str = "result"):
        self.name = name
        self._calls = calls
        self._output = output

    async def ainvoke(self, _args, _config):
        self._calls.append(self.name)
        return self._output


def _completion(decision: CompletionDecision) -> ResearchCompletionDecision:
    gaps = () if decision is CompletionDecision.COMPLETE else ("required gap",)
    return ResearchCompletionDecision(
        audit_id=f"completion-{decision.value}",
        plan_id="plan-1",
        decision=decision,
        covered_requirement_ids=() if gaps else ("req-1",),
        missing_requirement_ids=("req-1",) if gaps else (),
        remaining_budget=1 if decision is CompletionDecision.CONTINUE else 0,
        explicit_gaps=gaps,
        reasons=("fixture",),
    )


def _requirement_payload(run_id: str = "run-1") -> dict:
    requirement_set = asyncio.run(
        RequirementMaterializer().materialize(
            research_brief="Verify the governed requirement.",
            scope_id="scope-1",
            run_id=run_id,
        )
    )
    return requirement_set.model_dump(mode="json")


def test_write_research_brief_materializes_stable_requirement_set(monkeypatch):
    monkeypatch.setattr(graph_module, "configurable_model", _BriefModel())
    state = {"messages": [HumanMessage(content="Research this requirement.")]}
    config = {
        "configurable": {
            "enable_agentic_rag": True,
            "knowledge_tenant_id": "tenant",
            "knowledge_project_id": "project",
            "thread_id": "thread-1",
            "research_model": "fake:model",
        }
    }

    first = asyncio.run(graph_module.write_research_brief(state, config))
    second = asyncio.run(graph_module.write_research_brief(state, config))

    first_plan = first.update["requirement_set"]
    second_plan = second.update["requirement_set"]
    assert first_plan["plan_id"] == second_plan["plan_id"]
    assert first_plan["requirements"] == second_plan["requirements"]
    assert first_plan["requirements"]
    assert first.update["requirement_ids"] == [
        item["requirement_id"] for item in first_plan["requirements"]
    ]
    assert first.update["research_run_id"]


def test_supervisor_completion_gate_refuses_required_gaps(monkeypatch):
    async def fake_completion(*_args, **_kwargs):
        return _completion(CompletionDecision.CONTINUE)

    monkeypatch.setattr(graph_module, "_programmatic_completion", fake_completion)
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ResearchComplete",
                "args": {},
                "id": "complete-1",
                "type": "tool_call",
            }
        ],
    )
    command = asyncio.run(
        graph_module.supervisor_tools(
            {
                "supervisor_messages": [message],
                "research_iterations": 1,
            },
            {
                "configurable": {
                    "enable_agentic_rag": True,
                    "max_researcher_iterations": 5,
                }
            },
        )
    )

    assert command.goto == "supervisor"
    assert command.update["completion_decision_ids"] == ["completion-continue"]
    assert command.update["research_gaps"]["value"] == ["required gap"]
    assert command.update["supervisor_messages"][0].name == "ResearchComplete"
    assert "continue" in command.update["supervisor_messages"][0].content


def test_supervisor_executes_conduct_research_before_same_round_completion(
    monkeypatch,
):
    calls: list[str] = []

    class FakeSubgraph:
        async def ainvoke(self, state, _config):
            calls.append(state["research_topic"])
            return {
                "compressed_research": "governed finding",
                "raw_notes": ["trace"],
                "evidence_ids": ["evidence-1"],
            }

    async def fake_completion(*_args, **_kwargs):
        assert calls == ["required topic"]
        return _completion(CompletionDecision.COMPLETE)

    monkeypatch.setattr(graph_module, "researcher_subgraph", FakeSubgraph())
    monkeypatch.setattr(graph_module, "_programmatic_completion", fake_completion)
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ConductResearch",
                "args": {"research_topic": "required topic"},
                "id": "research-1",
                "type": "tool_call",
            },
            {
                "name": "ResearchComplete",
                "args": {},
                "id": "complete-1",
                "type": "tool_call",
            },
        ],
    )
    command = asyncio.run(
        graph_module.supervisor_tools(
            {
                "supervisor_messages": [message],
                "research_iterations": 1,
                "research_brief": "brief",
                "requirement_set": _requirement_payload(),
            },
            {
                "configurable": {
                    "enable_agentic_rag": True,
                    "max_researcher_iterations": 5,
                    "max_concurrent_research_units": 2,
                }
            },
        )
    )

    assert command.goto == "__end__"
    assert calls == ["required topic"]
    assert command.update["evidence_ids"] == ["evidence-1"]
    assert [item.name for item in command.update["supervisor_messages"]] == [
        "ConductResearch",
        "ResearchComplete",
    ]
    assert command.update["notes"] == ["governed finding"]


def test_supervisor_preserves_partial_parallel_results(monkeypatch):
    class FakeSubgraph:
        async def ainvoke(self, state, _config):
            if state["research_topic"] == "fails":
                raise TimeoutError("fixture timeout")
            return {
                "compressed_research": "successful finding",
                "raw_notes": ["successful trace"],
            }

    monkeypatch.setattr(graph_module, "researcher_subgraph", FakeSubgraph())
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ConductResearch",
                "args": {"research_topic": topic},
                "id": f"call-{index}",
                "type": "tool_call",
            }
            for index, topic in enumerate(("succeeds", "fails"))
        ],
    )
    command = asyncio.run(
        graph_module.supervisor_tools(
            {"supervisor_messages": [message], "research_iterations": 1},
            {
                "configurable": {
                    "max_researcher_iterations": 5,
                    "max_concurrent_research_units": 2,
                }
            },
        )
    )

    outputs = command.update["supervisor_messages"]
    assert command.goto == "supervisor"
    assert outputs[0].content == "successful finding"
    assert "TimeoutError" in outputs[1].content
    assert command.update["raw_notes"] == ["successful trace"]


def test_researcher_executes_governed_tool_before_same_round_completion(
    monkeypatch,
):
    calls: list[str] = []
    tool = _FakeTool("governed_retrieval", calls, "fixture governed JSON")

    async def fake_tools(_config):
        return [tool]

    async def fake_completion(*_args, **_kwargs):
        assert calls == ["governed_retrieval"]
        return _completion(CompletionDecision.COMPLETE)

    monkeypatch.setattr(graph_module, "get_all_tools", fake_tools)
    monkeypatch.setattr(graph_module, "_programmatic_completion", fake_completion)
    monkeypatch.setattr(
        graph_module,
        "_governed_artifact_updates",
        lambda _messages: {"evidence_ids": ["evidence-1"]},
    )
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "governed_retrieval",
                "args": {"query": "required topic"},
                "id": "search-1",
                "type": "tool_call",
            },
            {
                "name": "ResearchComplete",
                "args": {},
                "id": "complete-1",
                "type": "tool_call",
            },
        ],
    )
    command = asyncio.run(
        graph_module.researcher_tools(
            {
                "researcher_messages": [message],
                "tool_call_iterations": 1,
            },
            {
                "configurable": {
                    "enable_agentic_rag": True,
                    "max_react_tool_calls": 5,
                    "max_concurrent_researcher_tool_calls": 2,
                }
            },
        )
    )

    assert command.goto == "compress_research"
    assert calls == ["governed_retrieval"]
    assert command.update["evidence_ids"] == ["evidence-1"]
    assert [item.name for item in command.update["researcher_messages"]] == [
        "governed_retrieval",
        "ResearchComplete",
    ]


def test_compression_retries_with_compression_model(monkeypatch):
    calls: list[list] = []
    checked_models: list[str] = []

    class FakeCompressionModel:
        def with_config(self, _config):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                raise RuntimeError("fixture context length")
            return AIMessage(content="compressed on retry")

    def fake_is_token_limit(_error, model_name):
        checked_models.append(model_name)
        return True

    monkeypatch.setattr(graph_module, "configurable_model", FakeCompressionModel())
    monkeypatch.setattr(
        graph_module, "is_token_limit_exceeded", fake_is_token_limit
    )
    original = [
        HumanMessage(content="topic"),
        AIMessage(content="older analysis"),
        ToolMessage(content="finding", tool_call_id="tool-1", name="search"),
    ]
    result = asyncio.run(
        graph_module.compress_research(
            {"researcher_messages": original},
            {
                "configurable": {
                    "compression_model": "fake:compression-model",
                    "compression_max_retries": 2,
                }
            },
        )
    )

    assert result["compressed_research"] == "compressed on retry"
    assert len(calls) == 2
    assert checked_models == ["fake:compression-model"]
    assert len(original) == 3
    assert len(calls[1]) < len(calls[0])


def test_compression_filters_diagnostic_tool_messages(monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "get_governed_results_from_tool_calls",
        lambda messages: [object()]
        if messages and messages[0].content == "valid governed result"
        else [],
    )
    cleaned = graph_module._compression_trace(
        [
            AIMessage(content="analysis"),
            ToolMessage(
                content="reflection", tool_call_id="think-1", name="think_tool"
            ),
            ToolMessage(
                content="Error executing tool [TimeoutError]",
                tool_call_id="web-1",
                name="governed_retrieval",
            ),
            ToolMessage(
                content="valid governed result",
                tool_call_id="web-2",
                name="governed_retrieval",
            ),
        ],
        agentic=True,
    )

    rendered = "\n".join(str(item.content) for item in cleaned)
    assert "reflection" not in rendered
    assert "TimeoutError" not in rendered
    assert "valid governed result" in rendered
    assert all(not isinstance(item, ToolMessage) for item in cleaned)


def test_governed_artifact_recovery_ignores_diagnostics() -> None:
    messages = [
        ToolMessage(
            content="reflection", tool_call_id="think-1", name="think_tool"
        ),
        ToolMessage(
            content="Error executing tool [TimeoutError]: offline",
            tool_call_id="search-1",
            name="governed_retrieval",
        ),
        ToolMessage(
            content='{"evidence_ids":["fabricated"]}',
            tool_call_id="unknown-1",
            name="unknown_tool",
        ),
    ]

    assert graph_module._governed_artifact_updates(messages) == {}
