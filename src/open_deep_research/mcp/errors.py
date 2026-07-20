"""Stable, non-sensitive MCP failure semantics."""


class MCPIntegrationError(RuntimeError):
    """Base error for governed MCP integration."""


class MCPConfigurationError(MCPIntegrationError, ValueError):
    """Configuration cannot be converted to a safe runtime."""


class MCPAccessDeniedError(MCPIntegrationError, PermissionError):
    """A trusted policy denied an operation without revealing existence."""


class MCPConflictError(MCPIntegrationError):
    """An exclusive or stable identity already exists with other contents."""


class MCPQuotaExceededError(MCPIntegrationError):
    """A run-scoped staging quota would be exceeded."""


class MCPToolLoadError(MCPIntegrationError):
    """One configured MCP server could not load its tools."""


class MCPUnknownToolError(MCPIntegrationError, LookupError):
    """The model requested a tool absent from the trusted registry."""
