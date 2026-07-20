from __future__ import annotations

import json

import pytest

from open_deep_research.mcp.config import AllowedRoot, RootMode
from open_deep_research.mcp.errors import MCPAccessDeniedError
from open_deep_research.mcp.filesystem_adapter import GovernedFilesystemService
from open_deep_research.mcp.filesystem_policy import AllowedRootsPolicy, FilesystemOperation


def test_temporary_read_root_returns_only_public_locators(tmp_path) -> None:
    root = tmp_path / "fixture-root"; outside = tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / "doc.md").write_text("phase four", encoding="utf-8")
    policy = AllowedRootsPolicy((AllowedRoot(root_id="docs", path=str(root), mode=RootMode.READ_ONLY, public_alias="docs"),))
    service = GovernedFilesystemService(policy)
    content = service.read_text(root_id="docs", relative_locator="doc.md", request_id="r", actor="test")
    listed = service.list_directory(root_id="docs", request_id="r", actor="test")
    assert content.locator == "root://docs/doc.md"
    assert listed[0].locator == content.locator
    serialized = json.dumps([content.model_dump(mode="json"), listed[0].model_dump(mode="json")])
    assert str(root) not in serialized
    assert str(outside) not in serialized
    with pytest.raises(MCPAccessDeniedError):
        service.read_text(root_id="docs", relative_locator="../outside/secret.md", request_id="r", actor="test")


def test_audit_denials_are_sanitized(tmp_path) -> None:
    root = tmp_path / "docs"; root.mkdir()
    policy = AllowedRootsPolicy((AllowedRoot(root_id="docs", path=str(root), mode=RootMode.READ_ONLY, public_alias="docs"),))
    with pytest.raises(MCPAccessDeniedError):
        policy.resolve("docs", str(tmp_path / "secret"), operation=FilesystemOperation.READ, request_id="request", actor="actor")
    records = policy.audit_sink.list_records()
    serialized = json.dumps([record.model_dump(mode="json") for record in records])
    assert str(tmp_path) not in serialized
    assert records[-1].request_id == "request"
    assert records[-1].actor == "actor"
