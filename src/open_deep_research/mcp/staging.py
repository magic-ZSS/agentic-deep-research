"""Atomic exclusive-create staging; overwrite/edit/move/delete do not exist."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from open_deep_research.mcp.audit import MCPAuditSink
from open_deep_research.mcp.config import RootMode
from open_deep_research.mcp.errors import (
    MCPAccessDeniedError,
    MCPConflictError,
    MCPQuotaExceededError,
)
from open_deep_research.mcp.filesystem_policy import (
    AllowedRootsPolicy,
    FilesystemOperation,
)


class StagingArtifact(BaseModel):
    """Safe immutable handle returned instead of an internal path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    root_id: str
    public_locator: str
    relative_locator: str
    content_sha256: str
    byte_size: int
    media_type: str
    run_id: str


@dataclass(slots=True)
class _QuotaUsage:
    files: int = 0
    bytes: int = 0


class ExclusiveCreateStaging:
    """Run-scoped quota accounting around OS-level exclusive file creation."""

    def __init__(self, policy: AllowedRootsPolicy) -> None:
        self.policy = policy
        self.audit_sink: MCPAuditSink = policy.audit_sink
        self._usage: dict[tuple[str, str], _QuotaUsage] = {}
        self._artifacts: dict[tuple[str, str], StagingArtifact] = {}
        self._lock = threading.RLock()

    def _reserve(self, root_id: str, run_id: str, byte_size: int) -> None:
        root = self.policy.root(root_id).config
        if root.mode is not RootMode.IMPORT_STAGING:
            raise MCPAccessDeniedError("root is not an import staging capability")
        key = (root_id, run_id)
        with self._lock:
            current = self._usage.setdefault(key, _QuotaUsage())
            if current.files + 1 > root.max_files_per_run:
                raise MCPQuotaExceededError("staging file-count quota exceeded")
            if current.bytes + byte_size > root.max_total_bytes_per_run:
                raise MCPQuotaExceededError("staging total-byte quota exceeded")
            current.files += 1
            current.bytes += byte_size

    def _release(self, root_id: str, run_id: str, byte_size: int) -> None:
        with self._lock:
            current = self._usage[(root_id, run_id)]
            current.files -= 1
            current.bytes -= byte_size

    def exclusive_create(
        self,
        *,
        root_id: str,
        relative_locator: str,
        content: bytes,
        media_type: str,
        run_id: str,
        request_id: str,
        actor: str,
    ) -> StagingArtifact:
        """Create exactly once; any failure removes a self-created partial target."""
        decision = self.policy.resolve(
            root_id,
            relative_locator,
            operation=FilesystemOperation.EXCLUSIVE_CREATE,
            request_id=request_id,
            actor=actor,
            must_exist=False,
        )
        root = self.policy.root(root_id).config
        suffix = Path(relative_locator.replace("\\", "/")).suffix.lower()
        normalized_media_type = media_type.strip().lower()
        if suffix not in root.allowed_suffixes:
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="suffix_not_allowed",
                root_id=root_id,
            )
            raise MCPAccessDeniedError("staging suffix is not allowed")
        if normalized_media_type not in root.allowed_media_types:
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="media_type_not_allowed",
                root_id=root_id,
            )
            raise MCPAccessDeniedError("staging media type is not allowed")
        if len(content) > root.max_file_bytes:
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="single_file_quota_exceeded",
                root_id=root_id,
            )
            raise MCPQuotaExceededError("staging single-file quota exceeded")
        assert decision.resolved_path is not None and decision.public_locator is not None
        try:
            self._reserve(root_id, run_id, len(content))
        except MCPQuotaExceededError as exc:
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="run_quota_exceeded",
                root_id=root_id,
            )
            raise exc
        target = decision.resolved_path
        created = False
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(target, flags, 0o600)
            created = True
            try:
                view = memoryview(content)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("staging write made no progress")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except FileExistsError as exc:
            self._release(root_id, run_id, len(content))
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="exclusive_target_conflict",
                root_id=root_id,
            )
            raise MCPConflictError("staging target already exists") from exc
        except BaseException:
            self._release(root_id, run_id, len(content))
            if created:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            self.audit_sink.record(
                request_id=request_id,
                actor=actor,
                action=FilesystemOperation.EXCLUSIVE_CREATE.value,
                allowed=False,
                reason_code="exclusive_create_failed",
                root_id=root_id,
            )
            raise
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = "artifact_" + hashlib.sha256(
            f"{root_id}\0{run_id}\0{relative_locator}\0{digest}".encode()
        ).hexdigest()
        artifact = StagingArtifact(
            artifact_id=artifact_id,
            root_id=root_id,
            public_locator=decision.public_locator,
            relative_locator=relative_locator.replace("\\", "/"),
            content_sha256=digest,
            byte_size=len(content),
            media_type=normalized_media_type,
            run_id=run_id,
        )
        with self._lock:
            self._artifacts[(run_id, artifact_id)] = artifact
        return artifact

    def resolve_artifact(self, *, run_id: str, artifact_id: str) -> StagingArtifact:
        """Resolve only an artifact created in the same run."""
        with self._lock:
            artifact = self._artifacts.get((run_id, artifact_id))
        if artifact is None:
            raise MCPAccessDeniedError("staging artifact is unavailable")
        return artifact

    def usage(self, *, root_id: str, run_id: str) -> tuple[int, int]:
        with self._lock:
            current = self._usage.get((root_id, run_id), _QuotaUsage())
            return current.files, current.bytes
