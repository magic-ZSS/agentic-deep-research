"""Tests for the local Deep Research runner."""

import asyncio

from langchain_core.messages import AIMessage

from open_deep_research import run


class FakeGraph:
    """Return one clarification response followed by a final report."""

    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config):
        self.calls.append((list(state["messages"]), config))
        if len(self.calls) == 1:
            return {
                "messages": [
                    *state["messages"],
                    AIMessage(content="请说明时间范围。"),
                ]
            }
        return {"messages": state["messages"], "final_report": "完成"}


def test_main_continues_after_clarification(monkeypatch, capsys):
    fake_graph = FakeGraph()
    monkeypatch.setattr(run, "deep_researcher", fake_graph)
    monkeypatch.setattr(run, "get_question", lambda: "初始问题")
    monkeypatch.setattr(run, "get_clarification_answer", lambda _question: "2020 年后")

    asyncio.run(run.main())

    assert len(fake_graph.calls) == 2
    assert [message.content for message in fake_graph.calls[1][0]] == [
        "初始问题",
        "请说明时间范围。",
        "2020 年后",
    ]
    first_config = fake_graph.calls[0][1]
    assert first_config is fake_graph.calls[1][1]
    assert first_config["configurable"]["allow_clarification"] is True
    assert first_config["configurable"]["thread_id"]
    assert first_config["run_name"] == "open-deep-research-cli"
    assert capsys.readouterr().out == "完成\n"
