import pytest

from open_deep_research.memory.langmem_adapter import LangMemProposalAdapter
from open_deep_research.memory.models import MemoryType
from open_deep_research.runtime.identity import RuntimeIdentity


@pytest.mark.asyncio
async def test_langmem_adapter_only_returns_proposals():
    async def extractor(payload): return ["candidate"]
    who = RuntimeIdentity(tenant_id="t", user_id="u", project_id="p", thread_id="x", auth_source="test")
    result = await LangMemProposalAdapter(extractor).extract({}, identity=who, memory_type=MemoryType.EPISODIC, origin_run_id="r")
    assert result[0].provenance == {"adapter": "langmem", "proposal_only": True}
    assert not hasattr(LangMemProposalAdapter, "put") and not hasattr(LangMemProposalAdapter, "delete")
