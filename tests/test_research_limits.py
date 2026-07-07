import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from open_deep_research import deep_researcher as deep_researcher_module
from open_deep_research import utils as utils_module


class FakeTool:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    async def ainvoke(self, args, config):
        self.calls.append((self.name, args))
        return f"{self.name}:{args['value']}"


def test_researcher_tools_limits_parallel_tool_calls(monkeypatch):
    calls = []
    tools = [
        FakeTool("tool_a", calls),
        FakeTool("tool_b", calls),
        FakeTool("tool_c", calls),
    ]

    async def fake_get_all_tools(config):
        return tools

    monkeypatch.setattr(deep_researcher_module, "get_all_tools", fake_get_all_tools)

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "tool_a", "args": {"value": "a"}, "id": "call_a", "type": "tool_call"},
            {"name": "tool_b", "args": {"value": "b"}, "id": "call_b", "type": "tool_call"},
            {"name": "tool_c", "args": {"value": "c"}, "id": "call_c", "type": "tool_call"},
        ],
    )
    config = {
        "configurable": {
            "max_concurrent_researcher_tool_calls": 2,
            "max_react_tool_calls": 5,
        }
    }

    command = asyncio.run(
        deep_researcher_module.researcher_tools(
            {"researcher_messages": [message], "tool_call_iterations": 1},
            config,
        )
    )

    tool_outputs = command.update["researcher_messages"]
    assert command.goto == "researcher"
    assert calls == [
        ("tool_a", {"value": "a"}),
        ("tool_b", {"value": "b"}),
    ]
    assert len(tool_outputs) == 3
    assert all(isinstance(output, ToolMessage) for output in tool_outputs)
    assert tool_outputs[2].tool_call_id == "call_c"
    assert "maximum number of concurrent tool calls" in tool_outputs[2].content


def test_tavily_search_limits_queries_and_summary_concurrency(monkeypatch):
    captured_queries = []
    active_summaries = 0
    max_active_summaries = 0

    async def fake_tavily_search_async(search_queries, **kwargs):
        captured_queries.extend(search_queries)
        return [
            {
                "query": query,
                "results": [
                    {
                        "url": f"https://example.com/{index}",
                        "title": f"Source {index}",
                        "content": f"Content {index}",
                        "raw_content": f"Raw content {index}",
                    }
                ],
            }
            for index, query in enumerate(search_queries)
        ]

    async def fake_summarize_webpage(model, webpage_content):
        nonlocal active_summaries, max_active_summaries
        active_summaries += 1
        max_active_summaries = max(max_active_summaries, active_summaries)
        await asyncio.sleep(0)
        active_summaries -= 1
        return f"Summary for {webpage_content}"

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        def with_retry(self, stop_after_attempt):
            return self

    monkeypatch.setattr(utils_module, "tavily_search_async", fake_tavily_search_async)
    monkeypatch.setattr(utils_module, "summarize_webpage", fake_summarize_webpage)
    monkeypatch.setattr(utils_module, "init_chat_model", lambda **kwargs: FakeModel())

    output = asyncio.run(
        utils_module.tavily_search.coroutine(
            queries=["q1", "q2", "q3", "q4"],
            config={
                "configurable": {
                    "max_queries_per_search_call": 2,
                    "summarization_model": "openai:gpt-4.1-mini",
                }
            },
        )
    )

    assert captured_queries == ["q1", "q2"]
    assert max_active_summaries <= 2
    assert "Skipped 2 search queries" in output
    assert "Summary for Raw content 0" in output
    assert "Summary for Raw content 1" in output
