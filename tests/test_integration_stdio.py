"""End-to-end test: drive the server as a real MCP client over stdio."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from local_terminal_mcp.allowlist import DynamicAllowlist
from local_terminal_mcp.approvals import Decision, FileApprovalQueue


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


async def test_stdio_apply_patch_roundtrip(tmp_path: Path) -> None:
    """Read a file, patch it through the real tool surface, re-read to confirm."""
    (tmp_path / "hello.py").write_text("def hi():\n    return 1\n")

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "local_terminal_mcp",
            "--root",
            str(tmp_path),
            "--allow-write",
        ],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert "apply_patch" in tools

            patch = (
                "*** Begin Patch\n"
                "*** Update File: hello.py\n"
                "@@ def hi():\n"
                "-    return 1\n"
                "+    return 2\n"
                "*** End Patch\n"
            )
            res = await session.call_tool("apply_patch", {"input": patch})
            assert "1 modified" in _text(res)

            reread = await session.call_tool("read_file", {"path": "hello.py"})
            assert "return 2" in _text(reread)


async def test_stdio_session_cd_persists(tmp_path: Path) -> None:
    """Open a terminal, cd into a subdir, and have it persist across calls."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "marker.txt").write_text("x")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "local_terminal_mcp", "--root", str(tmp_path)],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"open_terminal", "list_terminals", "close_terminal"} <= tools

            opened = _text(await session.call_tool("open_terminal", {}))
            sid = opened.split("'")[1]  # opened session '<id>' ...

            cd = await session.call_tool(
                "run_command", {"command": "cd sub", "session": sid}
            )
            assert "cwd: sub" in _text(cd)

            # A later command runs in the session's directory without re-cd'ing.
            ls = await session.call_tool(
                "run_command", {"command": "ls", "session": sid}
            )
            assert "marker.txt" in _text(ls)

            pwd = await session.call_tool(
                "run_command", {"command": "pwd", "session": sid}
            )
            assert "sub" in _text(pwd)


async def test_stdio_write_build_workflow(tmp_path: Path) -> None:
    """The sprites-style workflow: write files into nested dirs, list, read, git."""
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "local_terminal_mcp",
            "--root", str(tmp_path),
            "--allow-write",
            "--allow-commands", "git *,ls *,cat *,tree,find *",
        ],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert "write_file" in tools  # write mode exposes it

            # Create an HTML preview and a sprite frame in a nested directory.
            html = "<!doctype html><title>preview</title><svg></svg>"
            w1 = await session.call_tool(
                "write_file", {"path": "index.html", "content": html}
            )
            assert "refused" not in _text(w1)
            w2 = await session.call_tool(
                "write_file",
                {"path": "sprites/frame1.svg", "content": "<svg/>"},
            )
            assert "refused" not in _text(w2)

            listing = _text(
                await session.call_tool("list_directory", {"path": "."})
            )
            assert "index.html" in listing and "sprites" in listing

            back = _text(
                await session.call_tool("read_file", {"path": "index.html"})
            )
            assert "preview" in back

            # git init works under the 'git *' pattern.
            gi = _text(
                await session.call_tool("run_command", {"command": "git init"})
            )
            assert "refused" not in gi

            # A write escaping the root is refused.
            esc = _text(
                await session.call_tool(
                    "write_file", {"path": "../escape.txt", "content": "x"}
                )
            )
            assert "refused" in esc

    assert (tmp_path / "index.html").read_text() == html
    assert (tmp_path / "sprites" / "frame1.svg").exists()


async def test_stdio_binary_write_and_image_read(tmp_path: Path) -> None:
    """Save a PNG via base64 and read it back as an actual image block."""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "local_terminal_mcp", "--root", str(tmp_path), "--allow-write"],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"write_file_base64", "read_image"} <= tools

            b64 = base64.b64encode(png).decode()
            w = await session.call_tool(
                "write_file_base64",
                {"path": "sprites/hero.png", "data_base64": b64},
            )
            assert "refused" not in _text(w)

            res = await session.call_tool(
                "read_image", {"path": "sprites/hero.png"}
            )
            # The result carries an image content block, not text.
            kinds = [getattr(b, "type", None) for b in res.content]
            assert "image" in kinds

    assert (tmp_path / "sprites" / "hero.png").read_bytes() == png


async def test_stdio_approval_always_persists(tmp_path: Path) -> None:
    """A non-allowlisted command is approved 'always' and persisted to JSON."""
    root = tmp_path / "root"
    root.mkdir()
    approvals = tmp_path / "approvals"
    allowlist = tmp_path / "allow.json"

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "local_terminal_mcp",
            "--root", str(root),
            "--allow-commands", "echo",  # 'date' is NOT allowed
            "--approval", "file",
            "--approvals-dir", str(approvals),
            "--allowlist-file", str(allowlist),
            "--approval-timeout", "15",
        ],
    )

    queue = FileApprovalQueue(approvals)

    async def approve_when_asked() -> None:
        for _ in range(300):
            pending = await asyncio.to_thread(queue.list_pending)
            if pending:
                await asyncio.to_thread(
                    queue.decide, pending[0].id, Decision.ALWAYS
                )
                return
            await asyncio.sleep(0.05)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            approver = asyncio.create_task(approve_when_asked())
            res = await session.call_tool("run_command", {"command": "date"})
            await approver

            # It ran (not refused).
            assert "refused" not in _text(res)
            assert "[exit 0]" in _text(res)

    # 'always' persisted the program to the JSON allowlist.
    assert DynamicAllowlist(allowlist).contains("date")
