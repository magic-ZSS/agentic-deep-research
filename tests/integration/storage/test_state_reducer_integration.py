from langgraph.graph import END, START, StateGraph

from open_deep_research.state import AgentState


def test_parallel_researcher_reference_updates_merge_deterministically():
    builder = StateGraph(AgentState)
    builder.add_node(
        "researcher_a",
        lambda state: {
            "source_ids": ["src_b", "src_a"],
            "evidence_ids": ["evd_shared", "evd_a"],
            "requirement_ids": ["req_shared"],
        },
    )
    builder.add_node(
        "researcher_b",
        lambda state: {
            "source_ids": ["src_a", "src_c"],
            "evidence_ids": ["evd_b", "evd_shared"],
            "requirement_ids": ["req_shared"],
        },
    )
    builder.add_edge(START, "researcher_a")
    builder.add_edge(START, "researcher_b")
    builder.add_edge("researcher_a", END)
    builder.add_edge("researcher_b", END)

    result = builder.compile().invoke({"messages": []})

    assert result["source_ids"] == ["src_a", "src_b", "src_c"]
    assert result["evidence_ids"] == ["evd_a", "evd_b", "evd_shared"]
    assert result["requirement_ids"] == ["req_shared"]
