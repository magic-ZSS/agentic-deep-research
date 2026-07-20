from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from open_deep_research.mcp.config import FILESYSTEM_PACKAGE, FILESYSTEM_PACKAGE_VERSION


@pytest.mark.windows_stdio
@pytest.mark.skipif(
    os.environ.get("ODR_RUN_WINDOWS_MCP_STDIO") != "1",
    reason="requires explicit local subprocess authorization",
)
def test_fixed_filesystem_package_handshake_list_and_read(tmp_path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-native acceptance")
    (tmp_path / "fixture.md").write_text("phase4 stdio fixture", encoding="utf-8")

    async def run() -> None:
        parameters = StdioServerParameters(
            command="cmd",
            args=[
                "/c", "npx", "--offline",
                f"{FILESYSTEM_PACKAGE}@{FILESYSTEM_PACKAGE_VERSION}",
                str(tmp_path),
            ],
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {item.name for item in tools.tools}
                assert {"read_text_file", "list_directory"} <= names
                result = await session.call_tool(
                    "read_text_file", {"path": str(tmp_path / "fixture.md")}
                )
                assert not result.isError
                assert "phase4 stdio fixture" in str(result.content)

    asyncio.run(run())
