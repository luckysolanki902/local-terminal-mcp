"""End-to-end test: drive the server as a real MCP client over stdio."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


async def test_stdio_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi there")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "local_terminal_mcp", "--root", str(tmp_path)],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"run_command", "read_file", "list_directory"} <= tools
            assert "write_file" not in tools  # read-only by default

            read_res = await session.call_tool("read_file", {"path": "hello.txt"})
            assert "hi there" in _text(read_res)

            run_res = await session.call_tool(
                "run_command", {"command": "echo integration"}
            )
            assert "integration" in _text(run_res)

            refused = await session.call_tool(
                "run_command", {"command": "rm -rf /"}
            )
            assert "refused" in _text(refused)
