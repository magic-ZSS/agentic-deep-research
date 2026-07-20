from pathlib import Path

from open_deep_research.configuration import Configuration
from open_deep_research.state import ResearcherOutputState


def test_structured_evidence_defaults_off_without_creating_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configuration = Configuration()
    assert configuration.enable_structured_evidence is False
    assert configuration.knowledge_repository_backend == "sqlite"
    assert configuration.knowledge_db_path == "data/knowledge/knowledge.db"
    assert configuration.knowledge_blob_dir == "data/knowledge/blobs"
    assert not Path("data").exists()


def test_legacy_researcher_output_remains_compatible_and_refs_are_additive():
    output = ResearcherOutputState(
        compressed_research="legacy compressed research",
        raw_notes=["legacy raw note"],
    )
    assert output.compressed_research == "legacy compressed research"
    assert output.raw_notes == ["legacy raw note"]
    assert output.source_ids == []
    assert output.evidence_ids == []
    assert output.requirement_ids == []
