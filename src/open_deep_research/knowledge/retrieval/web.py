"""Structured Web-search boundary used only by governed retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_deep_research.knowledge.ids import canonicalize_text, canonicalize_uri, stable_id
from open_deep_research.knowledge.models import AuthorityClass


class StructuredWebResult(BaseModel):
    """Normalized candidate returned by a governed Web provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: str = ""
    query: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    score: float = Field(default=0.0, ge=0, le=1)
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    authority_class: AuthorityClass = AuthorityClass.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query", "title", "content")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        normalized = canonicalize_text(value).strip()
        if not normalized:
            raise ValueError("structured Web text cannot be blank")
        return normalized

    def model_post_init(self, _context: object) -> None:
        normalized_url = canonicalize_uri(self.url)
        for name in ("retrieved_at", "published_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("Web result timestamps must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        expected = stable_id(
            "web_result",
            normalized_url,
            self.query,
            self.content,
            self.retrieved_at.isoformat(),
        )
        if self.result_id and self.result_id != expected:
            raise ValueError("result_id does not match normalized Web result")
        object.__setattr__(self, "url", normalized_url)
        object.__setattr__(self, "result_id", expected)


class WebSearchRuntime(BaseModel):
    """Trusted provider-call context not supplied by the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    researcher_id: str = Field(min_length=1)
    max_results_per_query: int = Field(default=3, ge=1, le=20)
    topic: Literal["general", "news", "finance"] = "general"

    @field_validator("scope_id", "run_id", "researcher_id")
    @classmethod
    def normalize_runtime_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Web runtime identity cannot be blank")
        return normalized


@runtime_checkable
class WebSearchProvider(Protocol):
    """Only Web boundary accepted by the Phase 3 orchestrator."""

    name: str

    async def search(
        self,
        queries: tuple[str, ...],
        *,
        runtime: WebSearchRuntime,
        config: RunnableConfig | None = None,
    ) -> tuple[StructuredWebResult, ...]:
        """Return structured, source-resolvable candidates."""
        ...


def infer_authority(result: dict[str, Any]) -> AuthorityClass:
    """Conservatively classify explicit provider hints and common primary domains."""
    hint = str(result.get("authority_class") or result.get("source_type") or "").casefold()
    mapping = {
        "official": AuthorityClass.OFFICIAL,
        "primary": AuthorityClass.PRIMARY,
        "secondary": AuthorityClass.SECONDARY,
        "self_reported": AuthorityClass.SELF_REPORTED,
        "corporate": AuthorityClass.SELF_REPORTED,
    }
    if hint in mapping:
        return mapping[hint]
    url = str(result.get("url") or "").casefold()
    if ".gov/" in f"{url}/" or ".gov." in url:
        return AuthorityClass.OFFICIAL
    if any(marker in url for marker in ("doi.org/", "arxiv.org/", ".edu/")):
        return AuthorityClass.PRIMARY
    return AuthorityClass.SECONDARY


def _safe_provider_metadata(item: dict[str, Any], provider: str) -> dict[str, Any]:
    """Retain only bounded JSON-safe provenance, never raw provider payloads."""
    metadata: dict[str, Any] = {"provider": provider}
    for key in ("result_id", "published_date", "source_type"):
        value = item.get(key)
        if isinstance(value, (str, int, float, bool)):
            metadata[key] = value
    return metadata


class TavilyStructuredWebSearchProvider:
    """Tavily raw-result adapter; it never invokes the legacy summary model."""

    name = "tavily-structured"

    async def search(
        self,
        queries: tuple[str, ...],
        *,
        runtime: WebSearchRuntime,
        config: RunnableConfig | None = None,
    ) -> tuple[StructuredWebResult, ...]:
        if not queries:
            return ()
        # Lazy import avoids changing the default-off production import surface.
        from open_deep_research.utils import tavily_search_async

        raw = await tavily_search_async(
            list(queries),
            max_results=runtime.max_results_per_query,
            topic=runtime.topic,
            include_raw_content=True,
            config=config,
        )
        deduplicated: dict[str, StructuredWebResult] = {}
        now = datetime.now(UTC)
        for response in raw:
            query = str(response.get("query") or "").strip()
            for item in response.get("results", ()):  # provider data is untrusted
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                title = item.get("title")
                content = item.get("raw_content") or item.get("content")
                if not all(isinstance(value, str) and value.strip() for value in (query, url, title, content)):
                    continue
                try:
                    score = float(item.get("score") or 0)
                    normalized = StructuredWebResult(
                        query=query,
                        url=url,
                        title=title,
                        content=content,
                        score=max(0.0, min(1.0, score)),
                        retrieved_at=now,
                        authority_class=infer_authority(item),
                        metadata=_safe_provider_metadata(item, self.name),
                    )
                except (TypeError, ValueError):
                    continue
                existing = deduplicated.get(normalized.url)
                if existing is None or (-normalized.score, normalized.result_id) < (
                    -existing.score,
                    existing.result_id,
                ):
                    deduplicated[normalized.url] = normalized
        return tuple(
            sorted(deduplicated.values(), key=lambda item: (item.url, item.result_id))
        )


def require_web_provider(provider: object) -> WebSearchProvider:
    """Fail closed before binding an object that lacks the provider contract."""
    if not isinstance(provider, WebSearchProvider):
        raise TypeError("agentic Web provider must implement WebSearchProvider")
    return provider
