from pathlib import Path

import pytest
from pydantic import ValidationError

from open_deep_research.configuration import Configuration
from open_deep_research.state import ResearcherOutputState


def test_structured_evidence_defaults_off_without_creating_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configuration = Configuration()
    assert configuration.enable_structured_evidence is False
    assert configuration.knowledge_repository_backend == "sqlite"
    assert configuration.knowledge_db_path == "data/knowledge/knowledge.db"
    assert configuration.knowledge_blob_dir == "data/knowledge/blobs"
    assert configuration.enable_knowledge_base is False
    assert configuration.enable_paperqa_retrieval is False
    assert configuration.knowledge_import_roots == ()
    assert configuration.knowledge_import_staging == "data/knowledge/import"
    assert configuration.paperqa_index_dir == "data/knowledge/paperqa-index"
    assert configuration.knowledge_search_visibility == "active_only"
    assert configuration.paperqa_contextual_summarization is False
    assert configuration.enable_knowledge_tools is False
    assert configuration.enable_agentic_rag is False
    assert configuration.enable_knowledge_writeback is False
    assert configuration.run_evidence_store_backend == "memory"
    assert configuration.run_evidence_db_path == (
        "data/run-evidence/run-evidence.db"
    )
    assert not Path("data").exists()


def test_phase2_configuration_rejects_overlapping_or_unbounded_chunking():
    with pytest.raises(ValidationError, match="overlap"):
        Configuration(
            knowledge_chunk_size_chars=256,
            knowledge_chunk_overlap_chars=256,
        )
    with pytest.raises(ValidationError):
        Configuration(knowledge_search_limit=51)
    with pytest.raises(ValidationError):
        Configuration(paperqa_contextual_max_concurrency=9)


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
    assert output.run_evidence_ids == []
    assert output.coverage_assessment_ids == []
    assert output.retrieval_decision_ids == []
    assert output.completion_decision_ids == []
    assert output.research_gaps == []
