import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from open_deep_research import deep_researcher as deep_researcher_module
from open_deep_research import utils as utils_module
from open_deep_research.state import Summary


class FakeTool:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    async def ainvoke(self, args, config):
        self.calls.append((self.name, args))
        return f"{self.name}:{args['value']}"


def test_process_print_disabled_is_silent(monkeypatch, capsys):
    monkeypatch.delenv("PRINT_PROCESS_INFO", raising=False)

    utils_module.process_print(
        {"configurable": {"print_process_info": False}},
        event="researcher",
        name="tool_calls",
        concurrency_id="researcher:1/tool:0",
        tools=["tavily_search"],
    )

    assert capsys.readouterr().out == ""


def test_process_print_enabled_outputs_compact_block(monkeypatch, capsys):
    monkeypatch.delenv("PRINT_PROCESS_INFO", raising=False)

    utils_module.process_print(
        {"configurable": {"print_process_info": True}},
        event="researcher",
        name="tool_calls",
        round_id="researcher:2",
        item_id="R2",
        concurrency_id="researcher:2/tool:0",
        tools=["tavily_search", "think_tool"],
    )

    output = capsys.readouterr().out
    assert "──────────────────────────────" in output
    assert "[TRACE #000]" in output
    assert "name=tool_calls" in output
    assert "concurrent=researcher:2/tool:0" in output
    assert "tools=2: tavily_search, think_tool" in output


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


def test_tavily_search_limits_queries_and_summary_concurrency(monkeypatch, capsys):
    monkeypatch.delenv("PRINT_PROCESS_INFO", raising=False)
    captured_queries = []
    captured_max_results = []
    active_summaries = 0
    max_active_summaries = 0

    async def fake_tavily_search_async(search_queries, **kwargs):
        captured_queries.extend(search_queries)
        captured_max_results.append(kwargs["max_results"])
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

    async def fake_summarize_webpage(model, webpage_content, **kwargs):
        nonlocal active_summaries, max_active_summaries
        utils_module.process_print(
            kwargs["config"],
            event="summary",
            name="summarize_webpage",
            title=kwargs["source_title"],
            round_id=kwargs["search_id"],
            item_id=kwargs["summary_id"],
            concurrency_id=kwargs["concurrency_id"],
        )
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
                    "print_process_info": True,
                    "max_queries_per_search_call": 2,
                    "summarization_model": "openai:gpt-4.1-mini",
                }
            },
        )
    )

    assert captured_queries == ["q1", "q2"]
    assert captured_max_results == [3]
    assert max_active_summaries <= 2
    assert "Skipped 2 search queries" in output
    assert "Summary for Raw content 0" in output
    assert "Summary for Raw content 1" in output
    trace_output = capsys.readouterr().out
    assert "event=search" in trace_output
    assert "name=tavily_search" in trace_output
    assert "id=S0" in trace_output
    assert "title=queries=2; first=q1" in trace_output
    assert "event=summary" in trace_output
    assert "id=S0.0" in trace_output
    assert "id=S0.1" in trace_output
    assert "parent=S0" in trace_output


def test_researcher_prints_tool_count_and_names(monkeypatch, capsys):
    monkeypatch.delenv("PRINT_PROCESS_INFO", raising=False)

    async def fake_get_all_tools(config):
        return [FakeTool("tavily_search", []), FakeTool("think_tool", [])]

    class FakeResearchModel:
        def bind_tools(self, tools):
            return self

        def with_retry(self, stop_after_attempt):
            return self

        def with_config(self, model_config):
            return self

        async def ainvoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "tavily_search",
                        "args": {"queries": ["q1"]},
                        "id": "call_search",
                        "type": "tool_call",
                    },
                    {
                        "name": "think_tool",
                        "args": {"reflection": "Need one more source."},
                        "id": "call_think",
                        "type": "tool_call",
                    },
                ],
            )

    monkeypatch.setattr(deep_researcher_module, "get_all_tools", fake_get_all_tools)
    monkeypatch.setattr(deep_researcher_module, "configurable_model", FakeResearchModel())

    command = asyncio.run(
        deep_researcher_module.researcher(
            {
                "researcher_messages": [HumanMessage(content="Research topic")],
                "research_topic": "Research topic",
            },
            {
                "configurable": {
                    "print_process_info": True,
                    "research_model": "openai:gpt-4.1",
                    "max_concurrent_researcher_tool_calls": 2,
                    "max_queries_per_search_call": 2,
                }
            },
        )
    )

    assert command.goto == "researcher_tools"
    trace_output = capsys.readouterr().out
    assert "event=researcher" in trace_output
    assert "round=researcher:1" in trace_output
    assert "name=tool_calls" in trace_output
    assert "tools=2: tavily_search, think_tool" in trace_output


def test_summary_accepts_list_and_legacy_string_key_excerpts():
    list_summary = Summary(
        summary="Short summary",
        key_excerpts=[" First excerpt ", None, "", "Second excerpt"],
    )
    legacy_summary = Summary(
        summary="Short summary",
        key_excerpts="Single legacy excerpt",
    )

    assert list_summary.key_excerpts == ["First excerpt", "Second excerpt"]
    assert legacy_summary.key_excerpts == ["Single legacy excerpt"]


def test_summarize_webpage_formats_list_key_excerpts(caplog):
    class FakeModel:
        async def ainvoke(self, messages):
            return Summary(
                summary="Condensed source summary.",
                key_excerpts=["First important excerpt.", "Second important excerpt."],
            )

    output = asyncio.run(
        utils_module.summarize_webpage(FakeModel(), "Raw webpage content")
    )

    assert "<summary>\nCondensed source summary.\n</summary>" in output
    assert "<key_excerpts>\n- First important excerpt.\n- Second important excerpt.\n</key_excerpts>" in output
    assert "Summarization failed" not in caplog.text
