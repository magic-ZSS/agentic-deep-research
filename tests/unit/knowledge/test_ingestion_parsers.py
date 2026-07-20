"""Offline contract tests for the four local candidate-ingestion parsers."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.ingestion.parsers import (
    ChunkingConfig,
    DocumentInput,
    DocumentParseError,
    DocumentParser,
    HtmlSnapshotParser,
    MarkdownParser,
    ParseErrorCode,
    ParserLocatorType,
    PastQueryParser,
    PdfParser,
)
from open_deep_research.knowledge.models import ChunkLocatorType, SourceKind


FIXTURES = Path(__file__).parents[2] / "fixtures" / "knowledge"


def _input(
    raw_bytes: bytes,
    *,
    media_type: str,
    input_ref: str,
    source_kind: SourceKind = SourceKind.LOCAL_FILE,
    canonical_uri: str | None = None,
) -> DocumentInput:
    return DocumentInput(
        source_kind=source_kind,
        media_type=media_type,
        input_ref=input_ref,
        display_name=Path(input_ref.replace("\\", "/")).name or "snapshot",
        canonical_uri=canonical_uri,
        raw_bytes=raw_bytes,
        metadata={"fixture": True},
    )


def _pdf_bytes(*page_texts: str) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_textbox(page.rect + (36, 36, -36, -36), text, fontsize=11)
    raw = document.tobytes()
    document.close()
    return raw


def test_document_input_is_bytes_only_and_input_ref_is_not_opened():
    raw = (FIXTURES / "sample.md").read_bytes()
    document = _input(
        raw,
        media_type="TEXT/MARKDOWN; charset=UTF-8",
        input_ref=r"C:\does-not-exist\sample.md",
    )
    parsed = MarkdownParser().parse(document)
    assert parsed.chunks
    assert document.normalized_media_type == "text/markdown"
    assert document.suffix == ".md"
    with pytest.raises(ValidationError):
        DocumentInput(
            source_kind=SourceKind.LOCAL_FILE,
            media_type="text/plain",
            input_ref=" ",
            display_name="invalid",
            raw_bytes=b"bytes",
        )
    with pytest.raises(ValidationError):
        DocumentInput(
            source_kind=SourceKind.LOCAL_FILE,
            media_type="text/plain",
            input_ref="valid.txt",
            display_name="invalid bytes",
            raw_bytes="not bytes",
        )


def test_all_parsers_satisfy_the_local_parser_protocol():
    parsers = (PdfParser(), MarkdownParser(), HtmlSnapshotParser(), PastQueryParser())
    assert all(isinstance(parser, DocumentParser) for parser in parsers)
    assert PdfParser().supports("application/pdf", "")
    assert MarkdownParser().supports("application/octet-stream", ".markdown")
    assert HtmlSnapshotParser().supports("text/html; charset=utf-8", "")
    assert PastQueryParser().supports("application/x-ndjson", "")


def test_pdf_parser_preserves_one_indexed_and_cross_page_locators():
    raw = _pdf_bytes("First page evidence " * 3, "Second page evidence " * 3)
    document = _input(raw, media_type="application/pdf", input_ref=r"D:\safe\paper.pdf")
    parsed = PdfParser().parse(document, ChunkingConfig(max_chars=100, overlap=10))

    assert parsed.metadata["parse_media"] is False
    assert parsed.metadata["ocr"] is False
    assert parsed.metadata["enrichment"] is False
    assert any(
        chunk.locator.page_start == 1 and chunk.locator.page_end == 2
        for chunk in parsed.chunks
    )
    assert all(chunk.locator.type is ParserLocatorType.PAGE for chunk in parsed.chunks)
    assert all(chunk.to_chunk_input().locator_type is ChunkLocatorType.PAGE for chunk in parsed.chunks)


def test_pdf_parser_rejects_invalid_and_image_only_documents():
    invalid = _input(b"not a pdf", media_type="application/pdf", input_ref="bad.pdf")
    with pytest.raises(DocumentParseError) as invalid_error:
        PdfParser().parse(invalid)
    assert invalid_error.value.code is ParseErrorCode.INVALID_DOCUMENT

    blank = _input(_pdf_bytes(""), media_type="application/pdf", input_ref="blank.pdf")
    with pytest.raises(DocumentParseError) as blank_error:
        PdfParser().parse(blank)
    assert blank_error.value.code is ParseErrorCode.NO_TEXT
    assert "OCR is not enabled" in blank_error.value.message


def test_markdown_parser_preserves_hierarchy_repeats_fences_and_crlf():
    raw = (FIXTURES / "sample.md").read_text(encoding="utf-8").replace("\n", "\r\n").encode()
    parsed = MarkdownParser().parse(
        _input(raw, media_type="text/markdown", input_ref="sample.md"),
        ChunkingConfig(max_chars=1_000, overlap=0),
    )
    paths = [chunk.locator.heading_path for chunk in parsed.chunks]
    assert ("Architecture",) in paths
    assert paths.count(("Architecture", "Storage")) == 2
    assert ("Architecture", "Storage", "Deep Detail") in paths
    assert not any("This is code" in part for path in paths for part in path)
    storage = [chunk for chunk in parsed.chunks if chunk.locator.heading_path[-1] == "Storage"]
    assert [chunk.metadata["heading_occurrence"] for chunk in storage] == [1, 2]
    assert all("\r" not in chunk.text for chunk in parsed.chunks)
    assert "# This is code, not a heading" in storage[0].text


def test_markdown_chunking_is_deterministic_and_phase1_compatible():
    document = _input(
        (FIXTURES / "sample.md").read_bytes(),
        media_type="text/markdown",
        input_ref="sample.md",
    )
    config = ChunkingConfig(max_chars=48, overlap=8)
    first = MarkdownParser().parse(document, config)
    second = MarkdownParser().parse(document, config)
    assert first == second
    assert first.chunk_inputs() == second.chunk_inputs()
    assert all(chunk.locator_type is ChunkLocatorType.HEADING for chunk in first.chunk_inputs())


def test_html_parser_removes_executable_content_and_preserves_snapshot_identity():
    document = _input(
        (FIXTURES / "sample.html").read_bytes(),
        media_type="text/html",
        input_ref=r"C:\safe\snapshot.html",
    )
    parsed = HtmlSnapshotParser().parse(document, ChunkingConfig(max_chars=1_000, overlap=0))
    combined = "\n".join(chunk.text for chunk in parsed.chunks)
    assert parsed.title == "Offline Snapshot"
    assert parsed.canonical_uri == "https://example.com/wiki/Agent?a=1&z=2"
    assert "secretScript" not in combined
    assert "secret-style" not in combined
    assert parsed.metadata["removed_non_content_tags"] == 2
    assert any(chunk.locator.anchor == "overview" for chunk in parsed.chunks)
    detail = next(chunk for chunk in parsed.chunks if chunk.locator.heading_path[-1] == "Details")
    assert detail.locator.anchor == "details"
    assert detail.metadata["anchor_generated"] is True
    persisted = detail.to_chunk_input()
    assert persisted.locator_type is ChunkLocatorType.ANCHOR
    assert persisted.metadata["canonical_uri"] == parsed.canonical_uri


def test_html_input_canonical_uri_overrides_snapshot_hint_without_fetching():
    document = _input(
        (FIXTURES / "sample.html").read_bytes(),
        media_type="text/html",
        input_ref="never-open-this.html",
        canonical_uri="HTTPS://Authority.EXAMPLE:443/page?b=2&a=1#fragment",
    )
    parsed = HtmlSnapshotParser().parse(document)
    assert parsed.canonical_uri == "https://authority.example/page?a=1&b=2"


def test_verified_past_query_preserves_record_source_evidence_and_scope():
    parsed = PastQueryParser().parse(
        _input(
            (FIXTURES / "past_queries.json").read_bytes(),
            media_type="application/json",
            input_ref="past_queries.json",
            source_kind=SourceKind.PAST_QUERY,
        )
    )
    assert len(parsed.chunks) == 2
    assert all(chunk.locator.type is ParserLocatorType.QUERY_RECORD for chunk in parsed.chunks)
    assert all(chunk.locator.record_id == "query-record-001" for chunk in parsed.chunks)
    assert all(chunk.metadata["verified"] is True for chunk in parsed.chunks)
    persisted = parsed.chunk_inputs()
    assert all(chunk.locator_type is ChunkLocatorType.TEXT for chunk in persisted)
    assert all(chunk.metadata["record_id"] == "query-record-001" for chunk in persisted)
    assert persisted[0].metadata["source_id"] == "src_fixture_001"
    assert persisted[0].metadata["evidence_id"] == "evd_fixture_001"


def test_past_query_jsonl_is_supported_without_external_io():
    record = {
        "record_id": "private-1",
        "verified": True,
        "query": "Q",
        "scope": {
            "tenant_id": "t",
            "project_id": "p",
            "visibility": "private",
            "owner_user_id": "alice",
        },
        "facts": [{"text": "Fact", "source_id": "src", "evidence_id": "evd"}],
    }
    raw = (json.dumps(record) + "\n").encode()
    parsed = PastQueryParser().parse(
        _input(
            raw,
            media_type="application/x-ndjson",
            input_ref="records.jsonl",
            source_kind=SourceKind.PAST_QUERY,
        )
    )
    assert parsed.chunks[0].metadata["scope"]["owner_user_id"] == "alice"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda record: record.update(verified=False), ParseErrorCode.UNVERIFIED_RECORD),
        (lambda record: record.pop("scope"), ParseErrorCode.MISSING_SCOPE),
        (lambda record: record["facts"][0].pop("evidence_id"), ParseErrorCode.MISSING_EVIDENCE),
        (
            lambda record: record.update(
                scope={"tenant_id": "t", "project_id": "p", "visibility": "private"}
            ),
            ParseErrorCode.MISSING_SCOPE,
        ),
    ],
)
def test_past_query_rejects_unverified_unscoped_or_unbound_facts(mutate, expected_code):
    payload = json.loads((FIXTURES / "past_queries.json").read_text(encoding="utf-8"))
    mutate(payload["records"][0])
    document = _input(
        json.dumps(payload).encode(),
        media_type="application/json",
        input_ref="invalid.json",
        source_kind=SourceKind.PAST_QUERY,
    )
    with pytest.raises(DocumentParseError) as error:
        PastQueryParser().parse(document)
    assert error.value.code is expected_code
    assert error.value.as_dict()["parser_name"] == PastQueryParser.name


def test_empty_text_snapshots_fail_instead_of_generating_content():
    for parser, media_type, suffix in (
        (MarkdownParser(), "text/markdown", "empty.md"),
        (HtmlSnapshotParser(), "text/html", "empty.html"),
        (PastQueryParser(), "application/json", "empty.json"),
    ):
        with pytest.raises(DocumentParseError) as error:
            parser.parse(_input(b"", media_type=media_type, input_ref=suffix))
        assert error.value.code is ParseErrorCode.EMPTY_INPUT
