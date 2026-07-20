"""Model-safe filesystem service returning only public locators."""

from __future__ import annotations

import fnmatch
from typing import Never

from pydantic import BaseModel, ConfigDict

from open_deep_research.mcp.errors import MCPAccessDeniedError
from open_deep_research.mcp.filesystem_policy import (
    AllowedRootsPolicy,
    FilesystemOperation,
)


class PublicFileEntry(BaseModel):
    """Safe list/search/info result without an absolute path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str
    locator: str
    name: str
    is_directory: bool
    byte_size: int | None = None


class PublicFileContent(BaseModel):
    """Bounded UTF-8 content plus public provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str
    locator: str
    content: str
    byte_size: int


class GovernedFilesystemService:
    """Read-only adapter; no write/edit/move/delete methods are defined."""

    def __init__(self, policy: AllowedRootsPolicy) -> None:
        self.policy = policy

    def _deny(self, *, root_id: str, request_id: str, actor: str, reason: str) -> Never:
        self.policy.audit_sink.record(
            request_id=request_id,
            actor=actor,
            action=FilesystemOperation.READ.value,
            allowed=False,
            reason_code=reason,
            root_id=root_id,
        )
        raise MCPAccessDeniedError(f"filesystem access denied: {reason}")

    def read_text(
        self,
        *,
        root_id: str,
        relative_locator: str,
        request_id: str,
        actor: str,
    ) -> PublicFileContent:
        decision = self.policy.resolve(
            root_id,
            relative_locator,
            operation=FilesystemOperation.READ,
            request_id=request_id,
            actor=actor,
        )
        assert decision.resolved_path is not None and decision.public_locator is not None
        root = self.policy.root(root_id).config
        path = decision.resolved_path
        if not path.is_file():
            self._deny(root_id=root_id, request_id=request_id, actor=actor, reason="not_a_file")
        if root.allowed_suffixes and path.suffix.lower() not in root.allowed_suffixes:
            self._deny(root_id=root_id, request_id=request_id, actor=actor, reason="suffix_not_allowed")
        size = path.stat().st_size
        if size > root.max_file_bytes:
            self._deny(root_id=root_id, request_id=request_id, actor=actor, reason="read_limit_exceeded")
        content = path.read_bytes().decode("utf-8", errors="strict")
        return PublicFileContent(
            root_id=root_id,
            locator=decision.public_locator,
            content=content,
            byte_size=size,
        )

    def list_directory(
        self,
        *,
        root_id: str,
        relative_locator: str = ".",
        request_id: str,
        actor: str,
    ) -> tuple[PublicFileEntry, ...]:
        decision = self.policy.resolve(
            root_id,
            relative_locator,
            operation=FilesystemOperation.LIST,
            request_id=request_id,
            actor=actor,
        )
        assert decision.resolved_path is not None
        if not decision.resolved_path.is_dir():
            raise MCPAccessDeniedError("requested filesystem object is not a directory")
        entries: list[PublicFileEntry] = []
        for child in sorted(decision.resolved_path.iterdir(), key=lambda item: item.name.casefold()):
            child_relative = child.relative_to(self.policy.root(root_id).canonical_path).as_posix()
            try:
                child_decision = self.policy.resolve(
                    root_id,
                    child_relative,
                    operation=FilesystemOperation.INFO,
                    request_id=request_id,
                    actor=actor,
                )
            except MCPAccessDeniedError:
                continue
            assert child_decision.resolved_path is not None and child_decision.public_locator
            is_directory = child_decision.resolved_path.is_dir()
            entries.append(
                PublicFileEntry(
                    root_id=root_id,
                    locator=child_decision.public_locator,
                    name=child.name,
                    is_directory=is_directory,
                    byte_size=None if is_directory else child_decision.resolved_path.stat().st_size,
                )
            )
        return tuple(entries)

    def search(
        self,
        *,
        root_id: str,
        relative_locator: str,
        pattern: str,
        request_id: str,
        actor: str,
        limit: int = 100,
    ) -> tuple[PublicFileEntry, ...]:
        if not pattern or len(pattern) > 256 or limit < 1 or limit > 1000:
            raise MCPAccessDeniedError("invalid filesystem search request")
        decision = self.policy.resolve(
            root_id,
            relative_locator,
            operation=FilesystemOperation.SEARCH,
            request_id=request_id,
            actor=actor,
        )
        assert decision.resolved_path is not None
        root = self.policy.root(root_id).canonical_path
        matches: list[PublicFileEntry] = []
        stack = [decision.resolved_path]
        while stack and len(matches) < limit:
            current = stack.pop()
            for child in sorted(current.iterdir(), key=lambda item: item.name.casefold(), reverse=True):
                relative = child.relative_to(root).as_posix()
                try:
                    child_decision = self.policy.resolve(
                        root_id,
                        relative,
                        operation=FilesystemOperation.INFO,
                        request_id=request_id,
                        actor=actor,
                    )
                except MCPAccessDeniedError:
                    continue
                assert child_decision.resolved_path is not None and child_decision.public_locator
                if child_decision.resolved_path.is_dir():
                    stack.append(child_decision.resolved_path)
                if fnmatch.fnmatch(relative, pattern):
                    is_directory = child_decision.resolved_path.is_dir()
                    matches.append(
                        PublicFileEntry(
                            root_id=root_id,
                            locator=child_decision.public_locator,
                            name=child.name,
                            is_directory=is_directory,
                            byte_size=None if is_directory else child_decision.resolved_path.stat().st_size,
                        )
                    )
                    if len(matches) >= limit:
                        break
        return tuple(sorted(matches, key=lambda item: item.locator))

    def get_info(
        self,
        *,
        root_id: str,
        relative_locator: str,
        request_id: str,
        actor: str,
    ) -> PublicFileEntry:
        decision = self.policy.resolve(
            root_id,
            relative_locator,
            operation=FilesystemOperation.INFO,
            request_id=request_id,
            actor=actor,
        )
        assert decision.resolved_path is not None and decision.public_locator
        is_directory = decision.resolved_path.is_dir()
        return PublicFileEntry(
            root_id=root_id,
            locator=decision.public_locator,
            name=decision.resolved_path.name,
            is_directory=is_directory,
            byte_size=None if is_directory else decision.resolved_path.stat().st_size,
        )
