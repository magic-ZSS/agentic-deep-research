import asyncio
import operator
from types import SimpleNamespace
from typing import Annotated, TypedDict
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from open_deep_research.evaluation.models import RunStatus
from open_deep_research.evaluation.telemetry import (
    EvaluationTelemetryCollector,
    ainvoke_with_evaluation_telemetry,
)


class CapturingRunnable:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.input_value = None
        self.config = None

    async def ainvoke(self, input_value, config):
        self.input_value = input_value
        self.config = config
        if self.error is not None:
            raise self.error
        return self.result


def test_disabled_wrapper_is_exact_direct_invocation():
    input_value = {"messages": ["same-object"]}
    config = {"configurable": {"flag": True}}
    result = {"final_report": "same-result"}
    runnable = CapturingRunnable(result=result)

    observed = asyncio.run(
        ainvoke_with_evaluation_telemetry(
            runnable, input_value, config, enabled=False
        )
    )

    assert observed is result
    assert runnable.input_value is input_value
    assert runnable.config is config


def test_disabled_wrapper_preserves_exception_type():
    class ExistingError(RuntimeError):
        pass

    error = ExistingError("unchanged")
    with pytest.raises(ExistingError) as exc_info:
        asyncio.run(
            ainvoke_with_evaluation_telemetry(
                CapturingRunnable(error=error), {}, {}, enabled=False
            )
        )
    assert exc_info.value is error


def test_enabled_wrapper_does_not_mutate_config_and_records_wall_time():
    original = {"configurable": {"flag": True}}
    collector = EvaluationTelemetryCollector()
    runnable = RunnableLambda(lambda value: {**value, "done": True})

    result = asyncio.run(
        ainvoke_with_evaluation_telemetry(
            runnable,
            {"value": 1},
            original,
            enabled=True,
            collector=collector,
        )
    )

    assert result == {"value": 1, "done": True}
    assert original == {"configurable": {"flag": True}}
    assert collector.telemetry is not None
    assert collector.telemetry.status is RunStatus.COMPLETED
    assert collector.telemetry.wall_time_ms >= 0


def test_parallel_tool_spans_and_ids_are_deduplicated():
    collector = EvaluationTelemetryCollector()
    collector.start()
    callback = collector.callback
    model_id = uuid4()
    first_tool_id = uuid4()
    second_tool_id = uuid4()
    callback.on_chat_model_start({"name": "fake-model"}, [[]], run_id=model_id)
    callback.on_chat_model_start({"name": "fake-model"}, [[]], run_id=model_id)
    callback.on_tool_start(
        {"name": "tavily_search"}, "q", run_id=first_tool_id, parent_run_id=model_id
    )
    callback.on_tool_start(
        {"name": "tavily_search"}, "q", run_id=first_tool_id, parent_run_id=model_id
    )
    callback.on_tool_start(
        {"name": "think_tool"}, "x", run_id=second_tool_id, parent_run_id=model_id
    )
    callback.on_tool_end("done", run_id=second_tool_id)
    callback.on_tool_end("done", run_id=first_tool_id)
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "tavily_search",
                                "args": {"query": "q"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        ],
        llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 5}},
    )
    callback.on_llm_end(response, run_id=model_id)
    callback.on_llm_end(response, run_id=model_id)

    telemetry = collector.finish(RunStatus.COMPLETED)

    assert telemetry.model_calls == 1
    assert telemetry.tool_requests_by_name == {"tavily_search": 1}
    assert telemetry.tool_calls_by_name == {"tavily_search": 1, "think_tool": 1}
    assert telemetry.search_calls == 1
    assert telemetry.input_tokens == 7
    assert telemetry.output_tokens == 5
    assert telemetry.total_tokens == 12
    assert len([span for span in telemetry.spans if span.kind == "tool"]) == 2


def test_incomplete_model_usage_is_null_not_partial_number():
    collector = EvaluationTelemetryCollector()
    collector.start()
    callback = collector.callback
    first = uuid4()
    second = uuid4()
    callback.on_llm_start({"name": "m"}, ["a"], run_id=first)
    callback.on_llm_end(
        SimpleNamespace(
            generations=[],
            llm_output={"token_usage": {"prompt_tokens": 2, "completion_tokens": 3}},
        ),
        run_id=first,
    )
    callback.on_llm_start({"name": "m"}, ["b"], run_id=second)
    callback.on_llm_end(SimpleNamespace(generations=[], llm_output={}), run_id=second)

    telemetry = collector.finish(RunStatus.COMPLETED)

    assert telemetry.model_calls == 2
    assert telemetry.model_calls_with_usage == 1
    assert telemetry.input_tokens is None
    assert telemetry.output_tokens is None
    assert telemetry.total_tokens is None
    assert telemetry.estimated_cost is None


def test_enabled_wrapper_records_failure_and_reraises_original():
    class ExistingError(ValueError):
        pass

    error = ExistingError("failure")
    collector = EvaluationTelemetryCollector()
    with pytest.raises(ExistingError) as exc_info:
        asyncio.run(
            ainvoke_with_evaluation_telemetry(
                CapturingRunnable(error=error),
                {},
                {},
                enabled=True,
                collector=collector,
            )
        )

    assert exc_info.value is error
    assert collector.telemetry is not None
    assert collector.telemetry.status is RunStatus.FAILED
    assert collector.telemetry.error_type == "ExistingError"


def test_enabled_wrapper_records_cancellation():
    collector = EvaluationTelemetryCollector()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ainvoke_with_evaluation_telemetry(
                CapturingRunnable(error=asyncio.CancelledError()),
                {},
                {},
                enabled=True,
                collector=collector,
            )
        )

    assert collector.telemetry is not None
    assert collector.telemetry.status is RunStatus.CANCELLED
    assert collector.telemetry.error_type == "CancelledError"


def test_langgraph_parallel_tool_messages_are_not_double_counted():
    class FakeState(TypedDict):
        messages: Annotated[list[ToolMessage], operator.add]

    @tool
    async def tavily_search(query: str) -> str:
        """Return a deterministic local result."""
        await asyncio.sleep(0)
        return f"result:{query}"

    async def researcher_one(state, config):
        del state
        result = await tavily_search.ainvoke({"query": "one"}, config=config)
        return {
            "messages": [ToolMessage(content=result, tool_call_id="call-one")]
        }

    async def researcher_two(state, config):
        del state
        result = await tavily_search.ainvoke({"query": "two"}, config=config)
        return {
            "messages": [ToolMessage(content=result, tool_call_id="call-two")]
        }

    builder = StateGraph(FakeState)
    builder.add_node("researcher_one", researcher_one)
    builder.add_node("researcher_two", researcher_two)
    builder.add_edge(START, "researcher_one")
    builder.add_edge(START, "researcher_two")
    builder.add_edge("researcher_one", END)
    builder.add_edge("researcher_two", END)
    graph = builder.compile()
    collector = EvaluationTelemetryCollector()

    result = asyncio.run(
        ainvoke_with_evaluation_telemetry(
            graph,
            {"messages": []},
            enabled=True,
            collector=collector,
        )
    )

    assert {message.tool_call_id for message in result["messages"]} == {
        "call-one",
        "call-two",
    }
    assert collector.telemetry is not None
    assert collector.telemetry.tool_calls_by_name == {"tavily_search": 2}
    assert collector.telemetry.search_calls == 2
    assert not collector.telemetry.search_calls_complete
    assert collector.telemetry.researcher_runs is None


def test_research_control_tools_are_not_counted_as_search():
    collector = EvaluationTelemetryCollector()
    collector.start()
    callback = collector.callback
    first = uuid4()
    second = uuid4()
    callback.on_tool_start({"name": "ResearchComplete"}, "", run_id=first)
    callback.on_tool_end("done", run_id=first)
    callback.on_tool_start({"name": "ConductResearch"}, "", run_id=second)
    callback.on_tool_end("done", run_id=second)

    telemetry = collector.finish(RunStatus.COMPLETED)

    assert telemetry.tool_calls_by_name == {
        "ConductResearch": 1,
        "ResearchComplete": 1,
    }
    assert telemetry.search_calls == 0
