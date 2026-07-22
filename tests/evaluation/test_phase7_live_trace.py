from __future__ import annotations

from uuid import uuid4

from open_deep_research.evaluation.live_trace import LiveTraceCollector
from open_deep_research.evaluation.trace_adapter import normalize_trace


def test_live_trace_retains_actual_plan_tool_and_retrieval_context():
    collector = LiveTraceCollector(max_output_chars=100)
    run_id = uuid4()
    collector.on_tool_start(
        {"name": "tavily_search"},
        '{"query":"LangGraph"}',
        run_id=run_id,
        parent_run_id=uuid4(),
        name="tavily_search",
    )
    collector.on_tool_end("official result", run_id=run_id)
    collector.add_plan("Inspect config\nVerify tests")
    trace = normalize_trace(collector.events)
    assert trace.plan == ["Inspect config", "Verify tests"]
    assert trace.tool_calls[0]["name"] == "tavily_search"
    assert trace.retrieval_context == ["official result"]
    assert trace.trace_dict["plan"] == trace.plan
    assert trace.trace_dict["children"]


def test_live_trace_bounds_payload_and_records_errors_without_exception_text():
    collector = LiveTraceCollector(max_input_chars=5, max_output_chars=5)
    run_id = uuid4()
    collector.on_tool_start(
        {"name": "governed_retrieval"},
        "123456789",
        run_id=run_id,
        name="governed_retrieval",
    )
    collector.on_tool_error(RuntimeError("private message"), run_id=run_id)
    event = collector.events[0]
    assert "truncated" in event.input["raw"]
    assert event.output == {"error_type": "RuntimeError"}
    assert event.status == "failed"
