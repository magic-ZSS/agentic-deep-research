from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from open_deep_research.mcp.config import AllowedRoot, RootMode
from open_deep_research.mcp.errors import MCPAccessDeniedError, MCPQuotaExceededError
from open_deep_research.mcp.filesystem_policy import AllowedRootsPolicy
from open_deep_research.mcp.staging import ExclusiveCreateStaging


def _staging(tmp_path, **updates):
    values = dict(
        root_id="staging", path=str(tmp_path), mode=RootMode.IMPORT_STAGING,
        public_alias="imports", allowed_suffixes=(".md",),
        allowed_media_types=("text/markdown",), max_file_bytes=8,
        max_files_per_run=2, max_total_bytes_per_run=12,
    )
    values.update(updates)
    return ExclusiveCreateStaging(AllowedRootsPolicy((AllowedRoot(**values),)))


def _create(staging, name="a.md", content=b"hello", run="run"):
    return staging.exclusive_create(root_id="staging", relative_locator=name, content=content, media_type="text/markdown", run_id=run, request_id="request", actor="test")


def test_exclusive_create_never_overwrites(tmp_path) -> None:
    staging = _staging(tmp_path)
    _create(staging)
    with pytest.raises(MCPAccessDeniedError):
        _create(staging, content=b"changed")
    assert (tmp_path / "a.md").read_bytes() == b"hello"


@pytest.mark.parametrize("name,media,error", [("a.exe", "text/markdown", MCPAccessDeniedError), ("a.md", "application/octet-stream", MCPAccessDeniedError), ("a.md", "text/markdown", MCPQuotaExceededError)])
def test_suffix_media_and_single_file_quota(tmp_path, name, media, error) -> None:
    staging = _staging(tmp_path)
    content = b"012345678" if error is MCPQuotaExceededError else b"ok"
    with pytest.raises(error):
        staging.exclusive_create(root_id="staging", relative_locator=name, content=content, media_type=media, run_id="run", request_id="r", actor="a")
    assert list(tmp_path.iterdir()) == []


def test_per_run_count_and_bytes_quota_leave_no_partial(tmp_path) -> None:
    staging = _staging(tmp_path)
    _create(staging, "a.md", b"123456")
    _create(staging, "b.md", b"123456")
    with pytest.raises(MCPQuotaExceededError):
        _create(staging, "c.md", b"1")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.md", "b.md"]


def test_concurrent_same_name_has_one_winner_and_no_overwrite(tmp_path) -> None:
    staging = _staging(tmp_path, max_files_per_run=10, max_total_bytes_per_run=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_create, staging, "same.md", value) for value in (b"one", b"two")]
    successes = [future.result() for future in futures if future.exception() is None]
    assert len(successes) == 1
    assert (tmp_path / "same.md").read_bytes() in (b"one", b"two")
