"""Smoke tests for server construction and HTTP auth middleware."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from local_terminal_mcp.auth import HEALTH_PATH, BearerAuthMiddleware, health_endpoint
from local_terminal_mcp.config import ServerConfig
from local_terminal_mcp.policy import Policy
from local_terminal_mcp.server import build_server


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return tmp_path


async def test_readonly_server_tools(repo: Path) -> None:
    mcp = build_server(ServerConfig(policy=Policy(root=repo, allow_write=False)))
    names = {tool.name for tool in await mcp.list_tools()}
    assert {"run_command", "read_file", "list_directory"} <= names
    assert "write_file" not in names


async def test_write_server_exposes_write_tool(repo: Path) -> None:
    mcp = build_server(ServerConfig(policy=Policy(root=repo, allow_write=True)))
    names = {tool.name for tool in await mcp.list_tools()}
    assert "write_file" in names


def _auth_app(token: str) -> Starlette:
    app = Starlette(
        routes=[
            Route("/", lambda request: PlainTextResponse("secret")),
            Route(HEALTH_PATH, health_endpoint),
        ]
    )
    app.add_middleware(BearerAuthMiddleware, token=token)
    return app


def test_auth_rejects_missing_token() -> None:
    client = TestClient(_auth_app("x" * 32))
    assert client.get("/").status_code == 401


def test_auth_rejects_wrong_token() -> None:
    client = TestClient(_auth_app("x" * 32))
    resp = client.get("/", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_auth_accepts_correct_token() -> None:
    token = "x" * 32
    client = TestClient(_auth_app(token))
    resp = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.text == "secret"


def test_health_endpoint_needs_no_auth() -> None:
    client = TestClient(_auth_app("x" * 32))
    resp = client.get(HEALTH_PATH)
    assert resp.status_code == 200
    assert resp.text == "ok"
