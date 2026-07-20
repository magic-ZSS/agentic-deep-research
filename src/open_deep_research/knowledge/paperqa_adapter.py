"""Lazy, repository-governed PaperQA retrieval adapter.

PaperQA objects are always disposable derived state. This module never imports the
optional dependency at module import time and never lets it assign canonical IDs.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from open_deep_research.knowledge.models import (
    KnowledgeAccessContext,
    KnowledgeScope,
)
from open_deep_research.knowledge.retrieval.models import (
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    RetrievalRecord,
)
from open_deep_research.knowledge.retrieval.protocols import RetrievalCatalog
from open_deep_research.knowledge.retrieval.repository_retriever import (
    RepositoryKnowledgeRetriever,
    eligible_records,
    lexical_score,
    record_to_hit,
)


class PaperQAAdapterError(RuntimeError):
    """PaperQA derived retrieval could not be used safely."""


class PaperQABackendMatch(BaseModel):
    """Narrow backend result carrying a project-owned chunk identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    score: float = Field(ge=0, allow_inf_nan=False)
    contextual_summary: str | None = None


class PaperQARetrievalBackend(Protocol):
    """Backend seam that can be replaced with a deterministic offline fake."""

    name: str

    async def retrieve(
        self,
        query: str,
        records: Sequence[RetrievalRecord],
        *,
        limit: int,
        contextualize: bool,
    ) -> Sequence[PaperQABackendMatch]: ...


class ContextualEvidenceProvider(Protocol):
    """Injected, bounded summarizer; implementations may be fake or model-backed."""

    async def summarize(
        self,
        query: str,
        record: RetrievalRecord,
        *,
        token_limit: int,
    ) -> str | None: ...


class FakePaperQABackend:
    """Deterministic no-network contract backend for tests and safe fallback."""

    name = "paperqa-fake"

    async def retrieve(
        self,
        query: str,
        records: Sequence[RetrievalRecord],
        *,
        limit: int,
        contextualize: bool,
    ) -> Sequence[PaperQABackendMatch]:
        scored = [
            (lexical_score(query, record), record.chunk.chunk_id, record)
            for record in records
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            PaperQABackendMatch(
                chunk_id=chunk_id,
                score=score,
                contextual_summary=(
                    record.chunk.text[:500] if contextualize else None
                ),
            )
            for score, chunk_id, record in scored[:limit]
        )


class BoundedContextualizingBackend:
    """Add opt-in contextual summaries around raw retrieval with hard limits."""

    def __init__(
        self,
        raw_backend: PaperQARetrievalBackend,
        *,
        contextualizer: ContextualEvidenceProvider,
        evidence_k: int = 8,
        max_concurrency: int = 2,
        timeout_seconds: float = 30.0,
        token_limit: int = 4_000,
    ) -> None:
        if evidence_k < 1:
            raise ValueError("evidence_k must be positive")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if token_limit < 1:
            raise ValueError("token_limit must be positive")
        self._raw_backend = raw_backend
        self._contextualizer = contextualizer
        self._evidence_k = evidence_k
        self._max_concurrency = max_concurrency
        self._timeout_seconds = timeout_seconds
        self._token_limit = token_limit
        self.name = f"{raw_backend.name}+contextual"

    async def retrieve(
        self,
        query: str,
        records: Sequence[RetrievalRecord],
        *,
        limit: int,
        contextualize: bool,
    ) -> Sequence[PaperQABackendMatch]:
        raw_limit = min(limit, self._evidence_k) if contextualize else limit
        matches = tuple(
            await self._raw_backend.retrieve(
                query,
                records,
                limit=raw_limit,
                contextualize=False,
            )
        )[:raw_limit]
        if not contextualize or not matches:
            return matches

        records_by_chunk = {record.chunk.chunk_id: record for record in records}
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def enrich(match: PaperQABackendMatch) -> PaperQABackendMatch:
            record = records_by_chunk.get(match.chunk_id)
            if record is None:
                return match
            try:
                async with semaphore:
                    summary = await asyncio.wait_for(
                        self._contextualizer.summarize(
                            query,
                            record,
                            token_limit=self._token_limit,
                        ),
                        timeout=self._timeout_seconds,
                    )
            except TimeoutError as exc:
                raise PaperQAAdapterError(
                    f"contextual summarization timeout for {match.chunk_id}"
                ) from exc
            except PaperQAAdapterError:
                raise
            except Exception as exc:
                raise PaperQAAdapterError(
                    f"contextual summarization exception for {match.chunk_id}"
                ) from exc
            normalized = summary.strip() if summary else None
            return match.model_copy(update={"contextual_summary": normalized})

        tasks = [asyncio.create_task(enrich(match)) for match in matches]
        try:
            return tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise


class DeterministicHashEmbedding:
    """Small local-only embedding used by inspection smoke and CLI workflows."""

    name = "odr-deterministic-hash-v1"

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32 or dimensions > 4096:
            raise ValueError("dimensions must be between 32 and 4096")
        self.dimensions = dimensions
        self.mode: Any = None

    def set_mode(self, mode: Any) -> None:
        """Match the narrow LMI embedding interface without importing LMI."""
        self.mode = mode

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return normalized signed hashing vectors without model/network calls."""
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(math.fsum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


def create_offline_paperqa_settings(
    index_directory: str | Path,
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
) -> Any:
    """Construct the only supported PaperQA Settings shape for Phase 2."""
    raw_path = str(index_directory).strip()
    if not raw_path:
        raise ValueError("paperqa index_directory cannot be blank")
    paperqa = module_loader("paperqa")
    return paperqa.Settings(
        parsing={
            "use_doc_details": False,
            "multimodal": False,
            "defer_embedding": True,
        },
        answer={"evidence_skip_summary": True},
        agent={
            "index": {
                "index_directory": raw_path,
                "sync_with_paper_directory": False,
            },
            "rebuild_index": False,
        },
    )


class NativePaperQABackend:
    """Small native seam limited to manual text loading and raw text retrieval."""

    name = "paperqa-native"

    def __init__(
        self,
        *,
        settings: Any,
        embedding_model: Any,
        minimum_similarity: float = 0.0,
        module_loader: Callable[[str], Any] = importlib.import_module,
    ) -> None:
        if not 0.0 <= minimum_similarity <= 1.0:
            raise ValueError("minimum_similarity must be between 0 and 1")
        self._settings = settings
        self._embedding_model = embedding_model
        self._minimum_similarity = minimum_similarity
        self._module_loader = module_loader
        self._validate_offline_settings()

    def _validate_offline_settings(self) -> None:
        parsing = getattr(self._settings, "parsing", None)
        if parsing is None:
            raise PaperQAAdapterError("PaperQA settings must expose parsing controls")
        if getattr(parsing, "use_doc_details", None) is not False:
            raise PaperQAAdapterError("PaperQA metadata enrichment must be disabled")
        if getattr(parsing, "defer_embedding", None) is not True:
            raise PaperQAAdapterError("PaperQA eager embedding must be disabled")
        media_flags = getattr(parsing, "should_parse_and_enrich_media", None)
        if media_flags != (False, False):
            raise PaperQAAdapterError("PaperQA multimodal parsing/enrichment must be off")
        if self._embedding_model is None:
            raise PaperQAAdapterError("an explicit embedding backend is required")

    async def retrieve(
        self,
        query: str,
        records: Sequence[RetrievalRecord],
        *,
        limit: int,
        contextualize: bool,
    ) -> Sequence[PaperQABackendMatch]:
        if contextualize:
            raise PaperQAAdapterError(
                "native contextualization requires a separately injected bounded service"
            )
        if not records:
            return ()

        paperqa = self._module_loader("paperqa")
        docs = paperqa.Docs()
        records_by_version: dict[str, list[RetrievalRecord]] = {}
        records_by_chunk: dict[str, RetrievalRecord] = {}
        for record in sorted(records, key=lambda item: item.chunk.chunk_id):
            records_by_chunk.setdefault(record.chunk.chunk_id, record)
            records_by_version.setdefault(record.version.version_id, []).append(record)

        for version_id in sorted(records_by_version):
            version_records = records_by_version[version_id]
            first = version_records[0]
            doc = paperqa.Doc(
                docname=f"odr_{version_id}",
                dockey=version_id,
                citation=first.source.display_name,
                content_hash=first.version.content_sha256,
            )
            texts = [
                paperqa.Text(
                    text=record.chunk.text,
                    name=f"odr_{record.chunk.chunk_id}",
                    doc=doc,
                    scope_id=record.chunk.scope_id,
                    source_id=record.source.source_id,
                    version_id=record.version.version_id,
                    chunk_id=record.chunk.chunk_id,
                )
                for record in sorted(
                    version_records, key=lambda item: item.chunk.chunk_id
                )
            ]
            await docs.aadd_texts(
                texts=texts,
                doc=doc,
                settings=self._settings,
                # Keep ``defer_embedding=True`` effective. Passing the model here
                # makes upstream PaperQA embed eagerly even with that setting.
                embedding_model=None,
            )

        lmi = self._module_loader("lmi")
        modes = getattr(lmi, "EmbeddingModes", None)
        if modes is not None and hasattr(self._embedding_model, "set_mode"):
            self._embedding_model.set_mode(modes.DOCUMENT)
        try:
            matches = await docs.retrieve_texts(
                query=query,
                k=min(limit, len(records_by_chunk)),
                settings=self._settings,
                embedding_model=self._embedding_model,
            )
        except BaseException:
            if modes is not None and hasattr(self._embedding_model, "set_mode"):
                self._embedding_model.set_mode(modes.DOCUMENT)
            raise
        if modes is not None and hasattr(self._embedding_model, "set_mode"):
            self._embedding_model.set_mode(modes.QUERY)
        try:
            query_vectors = await self._embedding_model.embed_documents([query])
        finally:
            if modes is not None and hasattr(self._embedding_model, "set_mode"):
                self._embedding_model.set_mode(modes.DOCUMENT)
        if len(query_vectors) != 1:
            raise PaperQAAdapterError("embedding backend returned an invalid query shape")
        query_vector = query_vectors[0]
        results: list[PaperQABackendMatch] = []
        seen: set[str] = set()
        for text in matches:
            chunk_id = getattr(text, "chunk_id", None)
            if not isinstance(chunk_id, str) or chunk_id not in records_by_chunk:
                continue
            if chunk_id in seen:
                continue
            similarity = self._cosine_similarity(
                query_vector, getattr(text, "embedding", None)
            )
            if similarity is None or similarity <= self._minimum_similarity:
                continue
            seen.add(chunk_id)
            results.append(
                PaperQABackendMatch(chunk_id=chunk_id, score=similarity)
            )
        return tuple(results)

    @staticmethod
    def _cosine_similarity(
        left: Sequence[float], right: Sequence[float] | None
    ) -> float | None:
        if right is None or len(left) != len(right) or not left:
            return None
        if any(not math.isfinite(value) for value in (*left, *right)):
            return None
        numerator = math.fsum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(math.fsum(value * value for value in left))
        right_norm = math.sqrt(math.fsum(value * value for value in right))
        if not all(math.isfinite(value) for value in (numerator, left_norm, right_norm)):
            return None
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


class PaperQAKnowledgeRetriever:
    """Apply governance before and after optional PaperQA-derived retrieval."""

    def __init__(
        self,
        catalog: RetrievalCatalog,
        *,
        backend: PaperQARetrievalBackend,
        enabled: bool = False,
        fallback_on_error: bool = True,
    ) -> None:
        self._catalog = catalog
        self._backend = backend
        self._enabled = enabled
        self._fallback_on_error = fallback_on_error
        self._fallback = RepositoryKnowledgeRetriever(catalog)

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeSearchResult:
        if not self._enabled:
            return await self._fallback.search(request, access=access, scope=scope)

        records = eligible_records(
            await self._catalog.list_records(access, scope),
            request,
            scope_id=scope.scope_id,
        )
        if not records:
            return KnowledgeSearchResult(
                query=request.query,
                backend=self._backend.name,
                empty_reason="no_eligible_knowledge",
            )

        records_by_chunk = {record.chunk.chunk_id: record for record in records}
        try:
            matches = await self._backend.retrieve(
                request.query,
                tuple(records_by_chunk.values()),
                limit=request.limit,
                contextualize=request.contextualize,
            )
        except Exception as exc:
            if not self._fallback_on_error:
                raise PaperQAAdapterError("PaperQA retrieval failed") from exc
            fallback = await self._fallback.search(
                request, access=access, scope=scope
            )
            return fallback.model_copy(
                update={"warnings": (f"paperqa_fallback:{type(exc).__name__}",)}
            )

        best_by_chunk: dict[str, PaperQABackendMatch] = {}
        for match in matches:
            if match.chunk_id not in records_by_chunk:
                continue
            prior = best_by_chunk.get(match.chunk_id)
            if prior is None or match.score > prior.score:
                best_by_chunk[match.chunk_id] = match
        ordered = sorted(
            best_by_chunk.values(), key=lambda item: (-item.score, item.chunk_id)
        )[: request.limit]
        hits = tuple(
            record_to_hit(
                records_by_chunk[match.chunk_id],
                score=match.score,
                rank=rank,
                retrieval_method=self._backend.name,
                contextual_summary=match.contextual_summary,
                at=request.as_of,
            )
            for rank, match in enumerate(ordered, start=1)
        )
        return KnowledgeSearchResult(
            query=request.query,
            hits=hits,
            backend=self._backend.name,
            empty_reason=None if hits else "no_matching_knowledge",
        )

    async def read(
        self,
        request: KnowledgeReadRequest,
        *,
        access: KnowledgeAccessContext,
        scope: KnowledgeScope,
    ) -> KnowledgeReadResult:
        return await self._fallback.read(request, access=access, scope=scope)


__all__ = [
    "BoundedContextualizingBackend",
    "ContextualEvidenceProvider",
    "DeterministicHashEmbedding",
    "FakePaperQABackend",
    "NativePaperQABackend",
    "PaperQAAdapterError",
    "PaperQABackendMatch",
    "PaperQAKnowledgeRetriever",
    "PaperQARetrievalBackend",
    "create_offline_paperqa_settings",
]
