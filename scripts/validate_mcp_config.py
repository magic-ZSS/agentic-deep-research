"""Validate a Phase-4 MCP template without starting subprocesses."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.mcp.config import (  # noqa: E402
    FILESYSTEM_PACKAGE,
    FILESYSTEM_PACKAGE_VERSION,
    MCPServerConfig,
)


PLACEHOLDER = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")


def validate(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = f"{FILESYSTEM_PACKAGE}@{FILESYSTEM_PACKAGE_VERSION}"
    if raw.get("filesystem_package") != expected:
        raise ValueError("filesystem package must be pinned to the reviewed version")
    if raw.get("enable_filesystem_mcp") is not False or raw.get("enable_knowledge_mcp") is not False:
        raise ValueError("example feature flags must default off")
    servers = raw.get("mcp_servers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError("mcp_servers must be a non-empty object")
    validated = {}
    for name, value in servers.items():
        roots = value.get("allowed_roots", [])
        for root in roots:
            configured_path = root.get("path", "")
            if not PLACEHOLDER.fullmatch(configured_path):
                raise ValueError("example roots must use environment placeholders")
            root["path"] = "C:/placeholder/allowed-root"
        server = MCPServerConfig.model_validate(value)
        if any("latest" in arg or arg == "-y" for arg in server.args):
            raise ValueError("unpinned or auto-install npx usage is forbidden")
        validated[name] = server
    return {"status": "valid", "servers": sorted(validated), "filesystem_package": expected, "started": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--no-start", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.config), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

