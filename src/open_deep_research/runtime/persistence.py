"""Managed async lifespans for LangGraph local persistence."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver


@dataclass(frozen=True)
class PersistenceResources:
    """Resources valid only while the owning async context is open."""

    checkpointer: Any | None
    store: Any | None
    backend: Literal["off", "memory", "sqlite"]


def _ensure_parent(path: str) -> str:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


@asynccontextmanager
async def persistence_lifespan(
    *,
    backend: Literal["off", "memory", "sqlite"],
    checkpoint_db_path: str,
    store_db_path: str | None = None,
) -> AsyncIterator[PersistenceResources]:
    """Open, set up, and close checkpoint/store resources as one lifespan."""
    if backend == "off":
        yield PersistenceResources(None, None, "off")
        return
    if backend == "memory":
        yield PersistenceResources(InMemorySaver(), None, "memory")
        return
    if backend != "sqlite":
        raise ValueError(f"unsupported checkpointer backend: {backend}")

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.store.sqlite.aio import AsyncSqliteStore

    checkpoint_path = _ensure_parent(checkpoint_db_path)
    store_path = _ensure_parent(store_db_path or f"{checkpoint_db_path}.store")
    if Path(checkpoint_path) == Path(store_path):
        raise ValueError("checkpoint and store SQLite files must be separate")

    async with AsyncExitStack() as stack:
        saver = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(checkpoint_path)
        )
        strict_serde = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_json_modules=None,
            allowed_msgpack_modules=None,
        )
        saver.serde = strict_serde
        saver.jsonplus_serde = strict_serde
        store = await stack.enter_async_context(
            AsyncSqliteStore.from_conn_string(store_path, index=None)
        )
        await saver.setup()
        await store.setup()
        yield PersistenceResources(saver, store, "sqlite")
