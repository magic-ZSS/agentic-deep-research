from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from open_deep_research.knowledge.ingestion.models import (
    ImportIndexStatus,
    ImportInputKind,
    ImportJob,
    ImportJobError,
    ImportJobStatus,
)
from open_deep_research.knowledge.ids import sha256_bytes


def _job(**updates):
    created = datetime(2026, 7, 21, tzinfo=UTC)
    values = {
        "scope_id": "scope_test",
        "input_kind": ImportInputKind.PDF,
        "input_ref": r"C:\Fixtures\Paper.pdf",
        "content_sha256": sha256_bytes(b"fixture"),
        "parser_name": "fixture-parser",
        "parser_version": "1.0",
        "chunk_config": {"size": 500, "overlap": 50},
        "created_at": created,
        "updated_at": created,
    }
    values.update(updates)
    return ImportJob(**values)


def test_import_job_identity_is_stable_strict_and_frozen():
    first = _job()
    second = _job(
        input_ref=r"c:\fixtures\paper.pdf",
        chunk_config={"overlap": 50, "size": 500},
    )
    assert first.job_id == second.job_id
    assert first.chunk_config_sha256 == second.chunk_config_sha256
    with pytest.raises(ValidationError):
        _job(unexpected=True)
    with pytest.raises(ValidationError):
        first.status = ImportJobStatus.RUNNING


def test_failed_job_retries_under_same_identity_and_clears_error():
    start = _job()
    running = start.transition(
        status=ImportJobStatus.RUNNING,
        at=start.created_at + timedelta(seconds=1),
    )
    assert running.job_id == start.job_id
    assert running.attempt_count == 1
    failure = ImportJobError(
        code="parse_failed",
        stage="parse",
        message="fixture parse error",
        retryable=True,
    )
    failed = running.transition(
        status=ImportJobStatus.FAILED,
        error=failure,
        at=start.created_at + timedelta(seconds=2),
    )
    assert failed.error == failure
    retried = failed.transition(
        status=ImportJobStatus.RUNNING,
        at=start.created_at + timedelta(seconds=3),
    )
    assert retried.job_id == start.job_id
    assert retried.attempt_count == 2
    assert retried.error is None
    assert retried.finished_at is None


def test_success_and_ready_index_require_complete_chain_and_valid_order():
    running = _job().transition(status=ImportJobStatus.RUNNING)
    with pytest.raises(ValidationError):
        running.transition(status=ImportJobStatus.SUCCEEDED)
    succeeded = running.transition(
        status=ImportJobStatus.SUCCEEDED,
        blob_id="blob_test",
        source_id="src_test",
        document_id="doc_test",
        version_id="ver_test",
    )
    indexed = succeeded.transition(
        status=ImportJobStatus.SUCCEEDED,
        index_status=ImportIndexStatus.PENDING,
    ).transition(
        status=ImportJobStatus.SUCCEEDED,
        index_status=ImportIndexStatus.READY,
    )
    assert indexed.index_status is ImportIndexStatus.READY
    assert indexed.finished_at == succeeded.finished_at
    index_failed = succeeded.transition(
        status=ImportJobStatus.SUCCEEDED,
        index_status=ImportIndexStatus.FAILED,
        error=ImportJobError(
            code="index_failed",
            stage="index",
            message="deterministic index failure",
            retryable=True,
        ),
    )
    assert index_failed.error is not None
    retrying_index = index_failed.transition(
        status=ImportJobStatus.SUCCEEDED,
        index_status=ImportIndexStatus.PENDING,
    )
    assert retrying_index.error is None
    with pytest.raises(ValueError, match="invalid import job transition"):
        _job().transition(status=ImportJobStatus.SUCCEEDED)


def test_failed_status_requires_structured_error():
    with pytest.raises(ValidationError):
        _job(status=ImportJobStatus.FAILED)
    with pytest.raises(ValidationError):
        _job(
            status=ImportJobStatus.PENDING,
            error=ImportJobError(
                code="unexpected",
                stage="test",
                message="not allowed on pending",
                retryable=False,
            ),
        )
