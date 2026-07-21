import pytest

from open_deep_research.memory.models import MemoryType, MemoryWriteProposal
from open_deep_research.memory.sqlite_repository import SQLiteMemoryRepository
from open_deep_research.memory.write_gate import MemoryWriteGate
from open_deep_research.runtime.identity import RuntimeIdentity


@pytest.mark.asyncio
async def test_sqlite_rehydrates_and_scope_unique(tmp_path):
    db = tmp_path / "memory.sqlite"
    who = RuntimeIdentity(tenant_id="t", user_id="u", project_id="p", thread_id="x", auth_source="test")
    proposal = MemoryWriteProposal(namespace=who.namespace("preference"), memory_type=MemoryType.PREFERENCE, content="Use concise reports", explicit_statement_ref="msg-1", importance=.9)
    repo = SQLiteMemoryRepository(str(db))
    _, stored = await MemoryWriteGate(repo).propose_evaluate_apply(proposal, who, actor="test")
    reopened = SQLiteMemoryRepository(str(db))
    assert (await reopened.get(proposal.namespace, stored.memory_id)).content == "Use concise reports"
    assert await reopened.list_audit(proposal.namespace, proposal.proposal_id)
