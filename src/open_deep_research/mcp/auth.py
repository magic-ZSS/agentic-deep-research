"""Authentication helpers shared by legacy and named HTTP MCP servers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


TokenFetcher = Callable[[str], Awaitable[dict | None]]


async def authorization_headers(
    *,
    auth_required: bool,
    server_url: str,
    fetcher: TokenFetcher | None,
) -> dict[str, str] | None:
    """Fail closed when a configured server requires unavailable credentials."""
    if not auth_required:
        return None
    if fetcher is None:
        raise PermissionError("MCP authentication is required")
    tokens = await fetcher(server_url)
    access_token = tokens.get("access_token") if tokens else None
    if not isinstance(access_token, str) or not access_token:
        raise PermissionError("MCP authentication token is unavailable")
    return {"Authorization": f"Bearer {access_token}"}

