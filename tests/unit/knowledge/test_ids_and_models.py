from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.ids import (
    canonicalize_local_ref,
    canonicalize_text,
    canonicalize_uri,
    sha256_bytes,
    stable_id,
)
from open_deep_research.knowledge.models import (
    Chunk,
    ChunkInput,
    ChunkLocatorType,
    ContentBlob,
    KnowledgeAccessContext,
    KnowledgeScope,
    Source,
    SourceKind,
    Visibility,
)
from open_deep_research.knowledge.repositories import (
    RepositoryAccessError,
    authorize_scope,
)


def test_exact_bytes_hash_is_distinct_from_logical_text_normalization():
    assert canonicalize_text("Cafe\u0301\r\nline") == "Caf\u00e9\nline"
    assert sha256_bytes(b"a\r\n") != sha256_bytes(b"a\n")
    assert stable_id("item", "Cafe\u0301\r\n") == stable_id(
        "item", "Caf\u00e9\n"
    )


def test_source_uri_and_local_reference_canonicalization():
    assert (
        canonicalize_uri("HTTPS://Example.COM:443/a?z=2&a=1#fragment")
        == "https://example.com/a?a=1&z=2"
    )
    assert canonicalize_local_ref(r"C:\\Docs\\..\\Docs\\Paper.PDF") == (
        "c:/docs/paper.pdf"
    )


def test_internal_windows_path_never_enters_public_view():
    source = Source(
        scope_id="scope_test",
        kind=SourceKind.LOCAL_FILE,
        internal_storage_ref=r"C:\\private\\paper.pdf",
        display_name="Paper",
    )
    public = source.public_view().model_dump(mode="json")
    assert "internal_storage_ref" not in public
    assert "C:" not in str(public)


def test_models_are_strict_versioned_and_frozen():
    scope = KnowledgeScope(tenant_id="tenant", project_id="project")
    assert scope.schema_version == "1.0"
    with pytest.raises(ValidationError):
        KnowledgeScope.model_validate(
            {
                "schema_version": "2.0",
                "tenant_id": "tenant",
                "project_id": "project",
            }
        )
    with pytest.raises(ValidationError):
        KnowledgeScope(tenant_id="tenant", project_id="project", unknown=True)
    with pytest.raises(ValidationError):
        scope.project_id = "changed"


def test_scope_authorization_is_fail_closed_for_private_and_project_scope():
    private = KnowledgeScope(
        tenant_id="tenant",
        project_id="project",
        owner_user_id="alice",
        visibility=Visibility.PRIVATE,
    )
    allowed = KnowledgeAccessContext(
        trusted_tenant_id="tenant",
        trusted_project_id="project",
        trusted_user_id="alice",
        auth_source="test",
        request_id="request",
    )
    authorize_scope(allowed, private)
    denied = allowed.model_copy(update={"trusted_user_id": "bob"})
    with pytest.raises(RepositoryAccessError):
        authorize_scope(denied, private)


@pytest.mark.parametrize(
    ("chunk_input", "expected"),
    [
        (
            ChunkInput(
                ordinal=0,
                text="PDF evidence",
                locator_type=ChunkLocatorType.PAGE,
                page_start=2,
                page_end=3,
            ),
            {"page_start": 2, "page_end": 3, "heading_path": []},
        ),
        (
            ChunkInput(
                ordinal=1,
                text="Markdown evidence",
                locator_type=ChunkLocatorType.HEADING,
                heading_path=("Architecture", "Storage"),
            ),
            {
                "page_start": None,
                "page_end": None,
                "heading_path": ["Architecture", "Storage"],
            },
        ),
    ],
)
def test_chunk_locators_round_trip_structurally(chunk_input, expected):
    chunk = Chunk(
        **chunk_input.model_dump(), scope_id="scope_test", version_id="ver_test"
    )
    restored = Chunk.model_validate_json(chunk.model_dump_json())
    assert restored == chunk
    dumped = restored.model_dump(mode="json")
    for key, value in expected.items():
        assert dumped[key] == value


def test_heading_locator_encoding_cannot_collide_on_separator_characters():
    first = Chunk(
        scope_id="scope_test",
        version_id="ver_test",
        ordinal=0,
        text="same text",
        locator_type=ChunkLocatorType.HEADING,
        heading_path=("A/B",),
    )
    second = Chunk(
        scope_id="scope_test",
        version_id="ver_test",
        ordinal=0,
        text="same text",
        locator_type=ChunkLocatorType.HEADING,
        heading_path=("A", "B"),
    )
    assert first.locator_key() != second.locator_key()
    assert first.chunk_id != second.chunk_id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"locator_type": "page"},
        {"locator_type": "page", "page_start": 2, "page_end": 1},
        {"locator_type": "heading", "heading_path": ()},
        {"locator_type": "anchor"},
        {"locator_type": "text", "page_start": 1},
    ],
)
def test_invalid_chunk_locator_combinations_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        ChunkInput(ordinal=0, text="content", **kwargs)


def test_blob_storage_reference_is_root_relative_and_traversal_free():
    digest = sha256_bytes(b"bytes")
    with pytest.raises(ValidationError):
        ContentBlob(
            scope_id="scope_test",
            content_sha256=digest,
            byte_size=5,
            media_type="text/plain",
            storage_ref=r"C:\\private\\blob",
        )
    with pytest.raises(ValidationError):
        ContentBlob(
            scope_id="scope_test",
            content_sha256=digest,
            byte_size=5,
            media_type="text/plain",
            storage_ref="scope/../outside.blob",
        )


def test_naive_domain_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        KnowledgeScope(
            tenant_id="tenant",
            project_id="project",
            created_at=datetime(2026, 1, 1),
        )
    aware = KnowledgeScope(
        tenant_id="tenant",
        project_id="project",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert aware.created_at.tzinfo is UTC
