import json

import pytest

from open_deep_research.mcp_servers.knowledge_server import create_knowledge_server
from open_deep_research.memory.models import MemoryType, MemoryWriteProposal
from open_deep_research.memory.recall import MemoryRecall
from open_deep_research.memory.sqlite_repository import SQLiteMemoryRepository
from open_deep_research.memory.write_gate import MemoryWriteGate
from open_deep_research.runtime.identity import RuntimeIdentity
from open_deep_research.tools.memory import memory_search


def who(user, thread):
    return RuntimeIdentity(tenant_id="tenant", user_id=user, project_id="project", thread_id=thread, auth_source="test")


@pytest.mark.asyncio
async def test_explicit_preference_cross_thread_same_user_only(tmp_path):
    repo = SQLiteMemoryRepository(str(tmp_path / "memory.db"))
    alice1, alice2, bob = who("alice", "one"), who("alice", "two"), who("bob", "three")
    item = MemoryWriteProposal(namespace=alice1.namespace("preference"), memory_type=MemoryType.PREFERENCE, content="Prefer concise tables", explicit_statement_ref="message-1", importance=.9)
    await MemoryWriteGate(repo).propose_evaluate_apply(item, alice1, actor="test")
    recall = MemoryRecall(repo)
    assert (await recall.search("concise", alice2, memory_types=(MemoryType.PREFERENCE,))).records
    assert not (await recall.search("concise", bob, memory_types=(MemoryType.PREFERENCE,))).records


@pytest.mark.asyncio
async def test_read_only_memory_tool_matches_internal_recall_and_no_namespace_arg(tmp_path):
    repo = SQLiteMemoryRepository(str(tmp_path / "memory.db"))
    identity = who("alice", "one")
    item = MemoryWriteProposal(namespace=identity.namespace("preference"), memory_type=MemoryType.PREFERENCE, content="Prefer concise tables", explicit_statement_ref="message-1", importance=.9)
    await MemoryWriteGate(repo).propose_evaluate_apply(item, identity, actor="test")
    recall = MemoryRecall(repo)
    internal = await recall.search("concise", identity, memory_types=(MemoryType.PREFERENCE,))
    raw = await memory_search.ainvoke({"query": "concise"}, {"configurable": {"_memory_context": {"recall": recall, "identity": identity}}})
    external = json.loads(raw)
    assert external["records"][0]["memory_id"] == internal.records[0].memory_id
    assert "namespace" not in memory_search.args_schema.model_fields
    assert not hasattr(memory_search, "delete")


@pytest.mark.asyncio
async def test_fastmcp_registers_memory_search_read_only_when_ready(tmp_path):
    identity = who("alice", "one")
    recall = MemoryRecall(SQLiteMemoryRepository(str(tmp_path / "memory.db")))
    class Context:
        runtime_identity = identity
    class Service:
        memory_recall = recall
        context = Context()
        async def memory_search(self, query, *, limit=5): return {"records": []}
    tools = {item.name: item for item in await create_knowledge_server(Service()).list_tools()}
    assert tools["memory_search"].annotations.readOnlyHint is True
    assert tools["memory_search"].annotations.destructiveHint is False
