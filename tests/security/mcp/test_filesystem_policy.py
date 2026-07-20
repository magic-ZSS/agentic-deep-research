from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from open_deep_research.mcp.config import AllowedRoot, RootMode
from open_deep_research.mcp.errors import MCPAccessDeniedError
from open_deep_research.mcp.filesystem_policy import (
    AllowedRootsPolicy,
    FilesystemOperation,
)


def _root(path: Path, *, mode: RootMode = RootMode.READ_ONLY) -> AllowedRoot:
    return AllowedRoot(root_id="docs", path=str(path), mode=mode, public_alias="knowledge")


@pytest.mark.parametrize(
    "locator",
    ("../secret.md", "C:/Windows/win.ini", "c:\\Windows\\win.ini", "//server/share/a", "/mnt/c/a", "bad\x00name"),
)
def test_traversal_absolute_drive_unc_wsl_and_null_fail_closed(tmp_path, locator) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    policy = AllowedRootsPolicy((_root(root),))
    with pytest.raises(MCPAccessDeniedError):
        policy.resolve(
            "docs", locator, operation=FilesystemOperation.READ,
            request_id="r1", actor="test",
        )


def test_empty_invalid_and_root_replacement_fail_closed(tmp_path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("ok", encoding="utf-8")
    policy = AllowedRootsPolicy((_root(root),))
    assert policy.root_ids == ("docs",)
    policy.replace_roots(())
    assert policy.root_ids == ()
    with pytest.raises(MCPAccessDeniedError):
        policy.resolve("docs", "a.md", operation=FilesystemOperation.READ, request_id="r", actor="a")


def test_prefix_sibling_and_write_operations_are_rejected(tmp_path) -> None:
    root = tmp_path / "allowed"
    sibling = tmp_path / "allowed-secret"
    root.mkdir(); sibling.mkdir()
    (root / "a.md").write_text("ok", encoding="utf-8")
    policy = AllowedRootsPolicy((_root(root),))
    with pytest.raises(MCPAccessDeniedError):
        policy.resolve("docs", "../allowed-secret/a.md", operation=FilesystemOperation.READ, request_id="r", actor="a")
    for operation in (FilesystemOperation.WRITE, FilesystemOperation.EDIT, FilesystemOperation.MOVE, FilesystemOperation.DELETE):
        with pytest.raises(MCPAccessDeniedError):
            policy.resolve("docs", "a.md", operation=operation, request_id="r", actor="a")


def test_symlink_or_junction_escape_is_rejected_when_supported(tmp_path) -> None:
    root = tmp_path / "docs"; outside = tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    link = root / "link"
    junction = False
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("host permits neither symlink nor junction creation")
        junction = True
    try:
        policy = AllowedRootsPolicy((_root(root),))
        with pytest.raises(MCPAccessDeniedError):
            policy.resolve("docs", "link/secret.md", operation=FilesystemOperation.READ, request_id="r", actor="a")
    finally:
        if junction:
            os.rmdir(link)
        else:
            link.unlink(missing_ok=True)


def test_root_identity_change_is_rechecked(tmp_path) -> None:
    root = tmp_path / "docs"; moved = tmp_path / "moved"
    root.mkdir(); (root / "a.md").write_text("ok", encoding="utf-8")
    policy = AllowedRootsPolicy((_root(root),))
    root.rename(moved); root.mkdir()
    with pytest.raises(MCPAccessDeniedError, match="root_identity_changed"):
        policy.resolve("docs", "a.md", operation=FilesystemOperation.READ, request_id="r", actor="a")
