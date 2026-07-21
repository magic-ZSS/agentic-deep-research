"""Offline Phase 5 checkpoint/resume self-test and future CLI hook."""
# ruff: noqa: D101,D103

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt

from open_deep_research.runtime.persistence import persistence_lifespan


class ResumeState(TypedDict):
    value: str
    committed: Annotated[list[str], lambda left, right: list(dict.fromkeys(left + right))]


def build_graph():
    def gated(state: ResumeState):
        answer = interrupt("resume-value")
        return {"value": answer, "committed": [f"commit:{answer}"]}

    builder = StateGraph(ResumeState)
    builder.add_node("gated", gated)
    builder.add_edge(START, "gated")
    return builder


async def self_test(path: Path) -> None:
    config = {"configurable": {"thread_id": "phase5-self-test"}}
    store_path = path.with_name(path.stem + "-store.sqlite")
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(path), store_db_path=str(store_path)) as resources:
        graph = build_graph().compile(checkpointer=resources.checkpointer, store=resources.store)
        first = await graph.ainvoke({"value": "", "committed": []}, config)
        assert "__interrupt__" in first
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(path), store_db_path=str(store_path)) as resources:
        graph = build_graph().compile(checkpointer=resources.checkpointer, store=resources.store)
        result = await graph.ainvoke(Command(resume="ok"), config)
        assert result["value"] == "ok"
        assert result["committed"] == ["commit:ok"]
    sys.stdout.write("phase5 resume self-test: PASS\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    if not args.self_test:
        raise SystemExit("only --self-test is available in Phase 5")
    asyncio.run(self_test(args.db.resolve()))


if __name__ == "__main__":
    main()
