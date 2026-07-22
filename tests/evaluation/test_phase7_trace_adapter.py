from open_deep_research.evaluation.full_metrics import metric_result_from_deepeval
from open_deep_research.evaluation.trace_adapter import TraceEvent, normalize_trace


def test_parallel_trace_preserves_parent_and_stable_order():
    trace = normalize_trace(
        [
            TraceEvent(event_id="b", parent_id="root", kind="tool", name="two", sequence=1, input={"q": 2}),
            TraceEvent(event_id="a", parent_id="root", kind="tool", name="one", sequence=1, input={"q": 1}),
            TraceEvent(event_id="p", kind="plan", name="plan", sequence=0, output=["search", "write"]),
            TraceEvent(event_id="r", parent_id="a", kind="retriever", name="kb", sequence=2, output=["ctx"]),
            TraceEvent(event_id="e", kind="tool", name="bad", sequence=3, status="failed"),
        ]
    )
    assert [item["name"] for item in trace.tool_calls] == [
        "one",
        "two",
        "kb",
        "bad",
    ]
    assert trace.tool_calls[0]["parent_id"] == "root"
    assert trace.plan == ["search", "write"]
    assert trace.retrieval_context == ["ctx"]
    assert trace.errors == ["e:failed"]


def test_missing_plan_cannot_pass_plan_adherence_default():
    class FakePlanAdherenceMetric:
        score = 1.0
        threshold = 0.5
        reason = "upstream default"
        error = None
        evaluation_model = "fake"
        evaluation_cost = None

    result = metric_result_from_deepeval(FakePlanAdherenceMetric(), plan_present=False)
    assert result.score == 0
    assert result.status.value == "failed"
    assert "absent" in result.reason
