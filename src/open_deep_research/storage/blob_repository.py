"""Immutable in-memory and local content-addressed blob repositories."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from threading import RLock
from uuid import uuid4

from open_deep_research.knowledge.ids import blob_id_for, sha256_bytes, validate_sha256
from open_deep_research.knowledge.models import (
    ContentBlob,
    KnowledgeAccessContext,
    KnowledgeScope,
)
from open_deep_research.knowledge.repositories import (
    RepositoryAccessError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    authorize_scope,
)


_STABLE_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{64}$")


def _validate_entity_id(value: str, name: str) -> str:
    if not _STABLE_ENTITY_ID.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


class InMemoryBlobRepository:
    """Reference blob semantics for unit tests and in-process use."""

    def __init__(self) -> None:
        self._content: dict[tuple[str, str], bytes] = {}
        self._models: dict[tuple[str, str], ContentBlob] = {}
        self._lock = RLock()

    async def put(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content: bytes,
        media_type: str,
    ) -> ContentBlob:
        authorize_scope(access, scope)
        digest = sha256_bytes(content)
        blob_id = blob_id_for(scope.scope_id, digest)
        key = (scope.scope_id, blob_id)
        model = ContentBlob.from_bytes(
            scope_id=scope.scope_id,
            content=content,
            media_type=media_type,
            storage_ref=f"memory/{scope.scope_id}/{blob_id}.blob",
        )
        with self._lock:
            existing = self._content.get(key)
            if existing is not None and existing != content:
                raise RepositoryConflictError("blob identity has conflicting bytes")
            existing_model = self._models.get(key)
            if existing_model is not None and existing_model.media_type != media_type:
                raise RepositoryConflictError("blob identity has conflicting media_type")
            self._content.setdefault(key, bytes(content))
            self._models.setdefault(key, model)
            return self._models[key].model_copy(deep=True)

    async def get(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> bytes:
        authorize_scope(access, scope)
        _validate_entity_id(blob_id, "blob_id")
        key = (scope.scope_id, blob_id)
        with self._lock:
            if key in self._content:
                return bytes(self._content[key])
            if any(other_id == blob_id for _, other_id in self._content):
                raise RepositoryAccessError("blob belongs to another scope")
        raise RepositoryNotFoundError("blob not found")

    async def verify(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
        expected_sha256: str,
    ) -> bool:
        content = await self.get(access, scope, blob_id)
        digest = validate_sha256(expected_sha256)
        return sha256_bytes(content) == digest and blob_id == blob_id_for(
            scope.scope_id, digest
        )

    def count(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> int:
        """Return an authorized scope-local count for deterministic diagnostics."""
        authorize_scope(access, scope)
        with self._lock:
            return sum(
                1
                for current_scope, _ in self._content
                if current_scope == scope.scope_id
            )


class LocalBlobRepository:
    """Root-confined, atomic content-addressed storage for original bytes."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _relative_ref(self, scope_id: str, blob_id: str) -> str:
        _validate_entity_id(scope_id, "scope_id")
        _validate_entity_id(blob_id, "blob_id")
        return f"{scope_id}/{blob_id}.blob"

    def _path(self, scope_id: str, blob_id: str) -> Path:
        candidate = (self.root / self._relative_ref(scope_id, blob_id)).resolve()
        if self.root not in candidate.parents:
            raise RepositoryAccessError("blob path escaped configured root")
        return candidate

    def _find_other_scope(self, scope_id: str, blob_id: str) -> bool:
        for candidate in self.root.glob(f"*/{blob_id}.blob"):
            if candidate.parent.name != scope_id and candidate.is_file():
                return True
        return False

    @staticmethod
    def _write_atomic(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def put(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        content: bytes,
        media_type: str,
    ) -> ContentBlob:
        authorize_scope(access, scope)
        digest = sha256_bytes(content)
        blob_id = blob_id_for(scope.scope_id, digest)
        relative_ref = self._relative_ref(scope.scope_id, blob_id)
        target = self._path(scope.scope_id, blob_id)

        def write() -> None:
            if target.exists():
                if sha256_bytes(target.read_bytes()) != digest:
                    raise RepositoryConflictError("stored blob failed identity check")
                return
            self._write_atomic(target, content)
            if sha256_bytes(target.read_bytes()) != digest:
                raise RepositoryConflictError("atomic blob write failed verification")

        await asyncio.to_thread(write)
        return ContentBlob.from_bytes(
            scope_id=scope.scope_id,
            content=content,
            media_type=media_type,
            storage_ref=relative_ref,
        )

    async def get(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
    ) -> bytes:
        authorize_scope(access, scope)
        target = self._path(scope.scope_id, blob_id)
        if not target.is_file():
            if await asyncio.to_thread(self._find_other_scope, scope.scope_id, blob_id):
                raise RepositoryAccessError("blob belongs to another scope")
            raise RepositoryNotFoundError("blob not found")
        return await asyncio.to_thread(target.read_bytes)

    async def verify(
        self,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
        blob_id: str,
        expected_sha256: str,
    ) -> bool:
        content = await self.get(access, scope, blob_id)
        digest = validate_sha256(expected_sha256)
        return sha256_bytes(content) == digest and blob_id == blob_id_for(
            scope.scope_id, digest
        )

    def count(
        self, access: KnowledgeAccessContext, scope: KnowledgeScope
    ) -> int:
        """Return an authorized scope-local count without exposing other scopes."""
        authorize_scope(access, scope)
        _validate_entity_id(scope.scope_id, "scope_id")
        return sum(
            1 for path in (self.root / scope.scope_id).glob("*.blob") if path.is_file()
        )
