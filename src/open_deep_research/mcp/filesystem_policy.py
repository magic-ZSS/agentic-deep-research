"""Fail-closed Allowed Roots policy for native Windows and portable tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from open_deep_research.mcp.audit import InMemoryMCPAuditSink, MCPAuditSink
from open_deep_research.mcp.config import AllowedRoot, RootMode
from open_deep_research.mcp.errors import MCPAccessDeniedError, MCPConfigurationError


class FilesystemOperation(StrEnum):
    """Complete operation vocabulary enforced below model/tool annotations."""

    READ = "read"
    LIST = "list"
    SEARCH = "search"
    INFO = "info"
    EXCLUSIVE_CREATE = "exclusive_create"
    WRITE = "write"
    EDIT = "edit"
    MOVE = "move"
    DELETE = "delete"


_READ_OPERATIONS = frozenset(
    {
        FilesystemOperation.READ,
        FilesystemOperation.LIST,
        FilesystemOperation.SEARCH,
        FilesystemOperation.INFO,
    }
)


@dataclass(frozen=True, slots=True)
class ResolvedAllowedRoot:
    """Canonical runtime root; configured and resolved paths must stay stable."""

    config: AllowedRoot
    configured_path: Path
    canonical_path: Path
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class FilesystemAccessDecision:
    """Internal decision; only public_locator may cross the model boundary."""

    root_id: str
    operation: FilesystemOperation
    allowed: bool
    reason: str
    public_locator: str | None = None
    resolved_path: Path | None = None
    destructive: bool = False


def _contains_path(root: Path, candidate: Path) -> bool:
    """Compare using component boundaries and Windows case folding."""
    try:
        return os.path.commonpath(
            (os.path.normcase(str(root)), os.path.normcase(str(candidate)))
        ) == os.path.normcase(str(root))
    except (ValueError, OSError):
        return False


def _relative_parts(value: str) -> tuple[str, ...]:
    """Reject absolute, drive, UNC, WSL, null, and traversal inputs."""
    if not isinstance(value, str) or "\x00" in value:
        raise MCPAccessDeniedError("filesystem request contains an invalid locator")
    stripped = value.strip().replace("\\", "/")
    if stripped in ("", "."):
        return ()
    windows = PureWindowsPath(value)
    posix = PurePosixPath(stripped)
    if windows.is_absolute() or windows.drive or windows.root or posix.is_absolute():
        raise MCPAccessDeniedError("filesystem locator must be root-relative")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise MCPAccessDeniedError("filesystem traversal is forbidden")
    return parts


def _has_symlink_component(root: Path, candidate: Path) -> bool:
    """Reject a stable symlink/junction path before opening it."""
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
            # On Windows a junction/reparse point is not always reported by
            # is_symlink(). Comparing the lexical and final paths catches it.
            if current.exists() and current.resolve(strict=True) != current.absolute():
                return True
        except OSError:
            return True
    return False


class AllowedRootsPolicy:
    """Runtime root registry with replacement semantics and sanitized audit."""

    def __init__(
        self,
        roots: tuple[AllowedRoot, ...] = (),
        *,
        audit_sink: MCPAuditSink | None = None,
    ) -> None:
        self.audit_sink = audit_sink or InMemoryMCPAuditSink()
        self._roots: dict[str, ResolvedAllowedRoot] = {}
        self.replace_roots(roots)

    @property
    def root_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._roots))

    def replace_roots(self, roots: tuple[AllowedRoot, ...]) -> tuple[str, ...]:
        """Completely replace roots; all invalid/empty input means no access."""
        resolved: dict[str, ResolvedAllowedRoot] = {}
        errors: list[str] = []
        for root in roots:
            if root.root_id in resolved:
                errors.append(f"duplicate_root:{root.root_id}")
                continue
            try:
                configured = Path(root.path).absolute()
                canonical = configured.resolve(strict=True)
                if not canonical.is_dir():
                    raise MCPConfigurationError("allowed root is not a directory")
                resolved[root.root_id] = ResolvedAllowedRoot(
                    config=root,
                    configured_path=configured,
                    canonical_path=canonical,
                    identity=(canonical.stat().st_dev, canonical.stat().st_ino),
                )
            except (OSError, ValueError, MCPConfigurationError):
                errors.append(f"invalid_root:{root.root_id}")
        self._roots = resolved
        return tuple(errors)

    def _deny(
        self,
        *,
        root_id: str,
        operation: FilesystemOperation,
        reason: str,
        request_id: str,
        actor: str,
    ) -> FilesystemAccessDecision:
        self.audit_sink.record(
            request_id=request_id,
            actor=actor,
            action=operation.value,
            allowed=False,
            reason_code=reason,
            root_id=root_id or None,
        )
        raise MCPAccessDeniedError(f"filesystem access denied: {reason}")

    def resolve(
        self,
        root_id: str,
        relative_locator: str,
        *,
        operation: FilesystemOperation,
        request_id: str,
        actor: str,
        must_exist: bool = True,
    ) -> FilesystemAccessDecision:
        """Resolve at operation time; no empty-root or cwd fallback exists."""
        root = self._roots.get(root_id)
        if root is None:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="unknown_or_empty_root",
                request_id=request_id,
                actor=actor,
            )
        allowed = (
            operation in _READ_OPERATIONS
            if root.config.mode is RootMode.READ_ONLY
            else operation is FilesystemOperation.EXCLUSIVE_CREATE
        )
        if not allowed:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="operation_not_allowed_for_root_mode",
                request_id=request_id,
                actor=actor,
            )
        try:
            parts = _relative_parts(relative_locator)
        except MCPAccessDeniedError:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="invalid_relative_locator",
                request_id=request_id,
                actor=actor,
            )
        try:
            current_root = root.configured_path.resolve(strict=True)
        except OSError:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="root_no_longer_resolvable",
                request_id=request_id,
                actor=actor,
            )
        try:
            current_identity = (current_root.stat().st_dev, current_root.stat().st_ino)
        except OSError:
            current_identity = (-1, -1)
        if current_root != root.canonical_path or current_identity != root.identity:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="root_identity_changed",
                request_id=request_id,
                actor=actor,
            )
        lexical = root.canonical_path.joinpath(*parts)
        try:
            if must_exist:
                candidate = lexical.resolve(strict=True)
                symlink_check_target = lexical
            else:
                if lexical.exists() or lexical.is_symlink():
                    return self._deny(
                        root_id=root_id,
                        operation=operation,
                        reason="exclusive_target_exists",
                        request_id=request_id,
                        actor=actor,
                    )
                candidate = lexical.parent.resolve(strict=True) / lexical.name
                symlink_check_target = lexical.parent
        except OSError:
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="path_or_parent_not_resolvable",
                request_id=request_id,
                actor=actor,
            )
        if not _contains_path(root.canonical_path, candidate):
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="resolved_path_outside_root",
                request_id=request_id,
                actor=actor,
            )
        if not root.config.follow_symlinks and _has_symlink_component(
            root.canonical_path, symlink_check_target
        ):
            return self._deny(
                root_id=root_id,
                operation=operation,
                reason="symlink_or_junction_forbidden",
                request_id=request_id,
                actor=actor,
            )
        public_relative = "/".join(parts)
        locator = f"root://{root.config.public_alias}"
        if public_relative:
            locator += f"/{public_relative}"
        self.audit_sink.record(
            request_id=request_id,
            actor=actor,
            action=operation.value,
            allowed=True,
            reason_code="policy_allowed",
            root_id=root_id,
        )
        return FilesystemAccessDecision(
            root_id=root_id,
            operation=operation,
            allowed=True,
            reason="policy_allowed",
            public_locator=locator,
            resolved_path=candidate,
            destructive=False,
        )

    def root(self, root_id: str) -> ResolvedAllowedRoot:
        root = self._roots.get(root_id)
        if root is None:
            raise MCPAccessDeniedError("filesystem root is unavailable")
        return root
