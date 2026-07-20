"""Backward-compatible multi-server MCP configuration models."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_deep_research.mcp.errors import MCPConfigurationError


FILESYSTEM_PACKAGE = "@modelcontextprotocol/server-filesystem"
FILESYSTEM_PACKAGE_VERSION = "2026.1.14"
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "hard_delete",
        "force_promote",
        "force_memory_write",
        "memory_search",
        "write_file",
        "edit_file",
        "move_file",
        "delete_file",
        "create_directory",
    }
)
READONLY_FILESYSTEM_TOOLS = frozenset(
    {
        "read_text_file",
        "read_media_file",
        "read_multiple_files",
        "list_directory",
        "list_directory_with_sizes",
        "directory_tree",
        "search_files",
        "get_file_info",
    }
)
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class MCPTransport(StrEnum):
    """Transports supported by the installed LangChain MCP adapter."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerKind(StrEnum):
    """Security profile applied before tools reach the model."""

    EXTERNAL = "external"
    FILESYSTEM_READ_ONLY = "filesystem_read_only"
    KNOWLEDGE = "knowledge"


class RootMode(StrEnum):
    """An allowed root is never implicitly both readable and writable."""

    READ_ONLY = "read_only"
    IMPORT_STAGING = "import_staging"


class AllowedRoot(BaseModel):
    """Configured filesystem capability; the path is never model-visible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str
    path: str = Field(min_length=1, repr=False)
    mode: RootMode
    public_alias: str
    follow_symlinks: bool = False
    allowed_suffixes: tuple[str, ...] = (".pdf", ".md", ".html", ".htm")
    allowed_media_types: tuple[str, ...] = (
        "application/pdf",
        "text/markdown",
        "text/html",
    )
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=1024**3)
    max_files_per_run: int = Field(default=20, ge=1, le=10_000)
    max_total_bytes_per_run: int = Field(
        default=50 * 1024 * 1024, ge=1, le=10 * 1024**3
    )

    @field_validator("root_id", "public_alias")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not _NAME.fullmatch(value):
            raise ValueError("root identifiers must be stable safe names")
        return value

    @field_validator("path")
    @classmethod
    def validate_configured_path(cls, value: str) -> str:
        value = value.strip().strip('"').strip("'")
        if not value or "\x00" in value:
            raise ValueError("allowed root path is empty or contains a null byte")
        if not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()):
            raise ValueError("allowed root path must be absolute")
        return value

    @field_validator("allowed_suffixes")
    @classmethod
    def normalize_suffixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            sorted({item.strip().lower() for item in value if item.strip()})
        )
        if any(not item.startswith(".") or "/" in item or "\\" in item for item in normalized):
            raise ValueError("allowed suffixes must be simple extensions")
        return normalized

    @field_validator("allowed_media_types")
    @classmethod
    def normalize_media_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip().lower() for item in value if item.strip()}))
        if any("/" not in item for item in normalized):
            raise ValueError("allowed media types must be MIME types")
        return normalized

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.max_file_bytes > self.max_total_bytes_per_run:
            raise ValueError("max_file_bytes cannot exceed max_total_bytes_per_run")
        return self


class MCPServerConfig(BaseModel):
    """One independently isolated MCP server connection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport: MCPTransport
    kind: MCPServerKind = MCPServerKind.EXTERNAL
    enabled: bool = True
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    auth_required: bool = False
    allowed_tools: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=300)
    allowed_roots: tuple[AllowedRoot, ...] = ()

    @field_validator("allowed_tools")
    @classmethod
    def normalize_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        tools = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(not _NAME.fullmatch(item) for item in tools):
            raise ValueError("MCP tool names must be stable safe names")
        if FORBIDDEN_TOOL_NAMES.intersection(tools):
            raise ValueError("MCP configuration exposes a forbidden capability")
        return tools

    @model_validator(mode="after")
    def validate_transport_and_kind(self) -> Self:
        if self.transport is MCPTransport.STDIO:
            if not self.command or self.url:
                raise ValueError("stdio server requires command and forbids url")
        elif not self.url or self.command:
            raise ValueError("streamable_http server requires url and forbids command")
        if self.kind is MCPServerKind.FILESYSTEM_READ_ONLY:
            if not self.allowed_roots:
                raise ValueError("filesystem server requires at least one Allowed Root")
            if any(root.mode is not RootMode.READ_ONLY for root in self.allowed_roots):
                raise ValueError("filesystem MCP process may only receive read-only roots")
            if not set(self.allowed_tools).issubset(READONLY_FILESYSTEM_TOOLS):
                raise ValueError("filesystem MCP exposes a write-capable/raw tool")
        return self

    def adapter_connection(self, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Return the installed langchain-mcp-adapters connection shape."""
        if self.transport is MCPTransport.STDIO:
            return {
                "transport": "stdio",
                "command": self.command,
                "args": list(self.args),
            }
        return {
            "transport": "streamable_http",
            "url": self.url,
            "headers": headers,
            "timeout": self.timeout_seconds,
        }


class MCPConfig(BaseModel):
    """Legacy single-server fields plus the new named server map."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str | None = None
    tools: tuple[str, ...] | None = None
    auth_required: bool = False
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    @field_validator("tools")
    @classmethod
    def normalize_legacy_tools(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        tools = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(not _NAME.fullmatch(item) for item in tools):
            raise ValueError("MCP tool names must be stable safe names")
        if FORBIDDEN_TOOL_NAMES.intersection(tools):
            raise ValueError("MCP configuration exposes a forbidden capability")
        return tools

    @field_validator("servers")
    @classmethod
    def validate_server_names(
        cls, value: dict[str, MCPServerConfig]
    ) -> dict[str, MCPServerConfig]:
        if any(not _NAME.fullmatch(name) for name in value):
            raise ValueError("MCP server names must be stable safe names")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_legacy_shape(self) -> Self:
        supplied = any((self.url, self.tools is not None, self.auth_required))
        if supplied and not (self.url and self.tools):
            raise ValueError("legacy MCP config requires both url and tools")
        if self.url and self.servers:
            raise ValueError("legacy and named MCP server forms cannot be mixed")
        return self

    def normalized_servers(self) -> dict[str, MCPServerConfig]:
        """Map the historical URL shape to one streamable HTTP server."""
        if self.servers:
            return dict(self.servers)
        if not self.url or not self.tools:
            return {}
        return {
            "legacy_http": MCPServerConfig(
                transport=MCPTransport.STREAMABLE_HTTP,
                url=self.url.rstrip("/") + "/mcp",
                auth_required=self.auth_required,
                allowed_tools=self.tools,
            )
        }


def merge_mcp_servers(
    legacy: MCPConfig | None,
    named: dict[str, MCPServerConfig] | None,
) -> dict[str, MCPServerConfig]:
    """Resolve configuration without silently overwriting a server name."""
    servers = legacy.normalized_servers() if legacy else {}
    for name, server in sorted((named or {}).items()):
        if name in servers:
            raise MCPConfigurationError(f"duplicate MCP server name: {name}")
        servers[name] = server
    return servers
