"""Compile graphs only while managed persistence resources are alive."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from open_deep_research.configuration import Configuration
from open_deep_research.runtime.persistence import (
    PersistenceResources,
    persistence_lifespan,
)


@dataclass(frozen=True)
class ManagedGraph:
    """A compiled graph paired with resources owned by the active context."""
    graph: Any
    resources: PersistenceResources


@asynccontextmanager
async def open_deep_research_graph(
    configuration: Configuration,
) -> AsyncIterator[ManagedGraph]:
    """Yield a compiled root graph without leaking saver/store lifetimes."""
    from open_deep_research.deep_researcher import deep_researcher_builder

    async with persistence_lifespan(
        backend=configuration.checkpointer_backend,
        checkpoint_db_path=configuration.checkpoint_db_path,
        store_db_path=configuration.checkpoint_store_db_path,
    ) as resources:
        graph = deep_researcher_builder.compile(
            checkpointer=resources.checkpointer,
            store=resources.store,
        )
        yield ManagedGraph(graph=graph, resources=resources)
