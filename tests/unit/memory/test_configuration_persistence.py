import pytest

from open_deep_research.configuration import Configuration
from open_deep_research.runtime.graph_factory import open_deep_research_graph
from open_deep_research.runtime.persistence import persistence_lifespan


def test_phase5_defaults_preserve_legacy_graph_behavior():
    config = Configuration()
    assert config.enable_memory is False
    assert config.enable_memory_writes is False
    assert config.checkpointer_backend == "off"


def test_database_files_must_be_separate():
    with pytest.raises(ValueError, match="must be separate"):
        Configuration(checkpoint_db_path="same.db", memory_db_path="same.db")
    with pytest.raises(ValueError, match="requires enable_memory"):
        Configuration(enable_memory_writes=True)


@pytest.mark.asyncio
async def test_sqlite_uses_strict_no_pickle_serializer(tmp_path):
    async with persistence_lifespan(backend="sqlite", checkpoint_db_path=str(tmp_path/"c.db"), store_db_path=str(tmp_path/"s.db")) as resources:
        serde = resources.checkpointer.serde
        assert serde.pickle_fallback is False
        assert serde._allowed_msgpack_modules is None


@pytest.mark.asyncio
async def test_root_graph_factory_preserves_off_path():
    async with open_deep_research_graph(Configuration()) as managed:
        assert managed.resources.backend == "off"
        assert managed.graph is not None
