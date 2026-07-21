import pytest

from open_deep_research.memory.in_memory_repository import InMemoryMemoryRepository
from open_deep_research.memory.models import MemoryType, MemoryWriteProposal
from open_deep_research.memory.write_gate import MemoryWriteGate
from open_deep_research.runtime.identity import RuntimeIdentity


@pytest.mark.asyncio
async def test_gate_rejects_forged_namespace():
    alice = RuntimeIdentity(tenant_id="t", user_id="alice", project_id="p", thread_id="1", auth_source="test")
    bob = RuntimeIdentity(tenant_id="t", user_id="bob", project_id="p", thread_id="2", auth_source="test")
    item = MemoryWriteProposal(namespace=bob.namespace("preference"), memory_type=MemoryType.PREFERENCE, content="forged", explicit_statement_ref="m", importance=1)
    with pytest.raises(PermissionError):
        await MemoryWriteGate(InMemoryMemoryRepository()).evaluate(item, alice)
