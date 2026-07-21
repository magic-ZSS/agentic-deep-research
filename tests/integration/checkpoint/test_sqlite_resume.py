from __future__ import annotations

import asyncio
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt

from open_deep_research.runtime.persistence import persistence_lifespan


class State(TypedDict):
    value: str
    effects: Annotated[list[str], lambda a, b: list(dict.fromkeys(a + b))]


def builder():
    def node(state: State):
        answer = interrupt("approval")
        return {"value": answer, "effects": ["effect:" + answer]}
    graph = StateGraph(State).add_node("node", node).add_edge(START, "node")
    return graph


@pytest.mark.asyncio
async def test_cross_lifespan_sqlite_resume_and_thread_isolation(tmp_path):
    checkpoint = tmp_path / "checkpoints.sqlite"
    store = tmp_path / "store.sqlite"
    one = {"configurable": {"thread_id": "one"}}
    two = {"configurable": {"thread_id": "two"}}
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(checkpoint), store_db_path=str(store)) as resources:
        graph = builder().compile(checkpointer=resources.checkpointer, store=resources.store)
        assert "__interrupt__" in await graph.ainvoke({"value": "", "effects": []}, one)
        assert "__interrupt__" in await graph.ainvoke({"value": "", "effects": []}, two)
        leaked_saver = resources.checkpointer
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(checkpoint), store_db_path=str(store)) as resources:
        graph = builder().compile(checkpointer=resources.checkpointer, store=resources.store)
        assert (await graph.ainvoke(Command(resume="A"), one))["effects"] == ["effect:A"]
        assert (await graph.ainvoke(Command(resume="B"), two))["effects"] == ["effect:B"]
    with pytest.raises(Exception):
        await leaked_saver.aget_tuple(one)


@pytest.mark.asyncio
async def test_lifespan_closes_on_exception_and_cancel(tmp_path):
    saver = None
    with pytest.raises(RuntimeError):
        async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(tmp_path/"x.db"), store_db_path=str(tmp_path/"y.db")) as resources:
            saver = resources.checkpointer
            raise RuntimeError("boom")
    with pytest.raises(Exception):
        await saver.aget_tuple({"configurable": {"thread_id": "closed"}})

    entered = asyncio.Event()
    holder = {}

    async def cancelled_owner():
        async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(tmp_path/"cancel-c.db"), store_db_path=str(tmp_path/"cancel-s.db")) as resources:
            holder["saver"] = resources.checkpointer
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(Exception):
        await holder["saver"].aget_tuple({"configurable": {"thread_id": "cancelled"}})


@pytest.mark.asyncio
async def test_off_backend_has_no_persistence(tmp_path):
    async with persistence_lifespan(backend="off", checkpoint_db_path=str(tmp_path/"unused")) as resources:
        assert resources.checkpointer is None and resources.store is None
