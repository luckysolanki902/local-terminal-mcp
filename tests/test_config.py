"""Tests for configuration loading and fail-closed validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_terminal_mcp.config import ConfigError, ServerConfig, load_config
from local_terminal_mcp.policy import DEFAULT_ALLOWED_COMMANDS, Policy


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("LTMCP_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTMCP_ROOT", str(tmp_path))
    cfg = load_config()
    assert cfg.transport == "stdio"
    assert cfg.policy.allow_write is False
    assert cfg.policy.allowed_commands == DEFAULT_ALLOWED_COMMANDS


def test_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTMCP_ROOT", str(tmp_path))
    monkeypatch.setenv("LTMCP_ALLOW_COMMANDS", "ls, cat ,git")
    monkeypatch.setenv("LTMCP_ALLOW_WRITE", "true")
    monkeypatch.setenv("LTMCP_TIMEOUT", "30")
    cfg = load_config()
    assert cfg.policy.allowed_commands == frozenset({"ls", "cat", "git"})
    assert cfg.policy.allow_write is True
    assert cfg.policy.timeout_seconds == 30


def test_int_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTMCP_ROOT", str(tmp_path))
    monkeypatch.setenv("LTMCP_TIMEOUT", "notanumber")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config()


def test_http_without_token_is_refused(tmp_path: Path) -> None:
    cfg = ServerConfig(policy=Policy(root=tmp_path), transport="http")
    with pytest.raises(ConfigError, match="without an auth token"):
        cfg.validate()


def test_http_with_short_token_is_refused(tmp_path: Path) -> None:
    cfg = ServerConfig(
        policy=Policy(root=tmp_path), transport="http", auth_token="short"
    )
    with pytest.raises(ConfigError, match="at least 16 characters"):
        cfg.validate()


def test_http_with_good_token_validates(tmp_path: Path) -> None:
    cfg = ServerConfig(
        policy=Policy(root=tmp_path),
        transport="http",
        auth_token="x" * 32,
    )
    cfg.validate()  # no raise


def test_unknown_transport_is_refused(tmp_path: Path) -> None:
    cfg = ServerConfig(policy=Policy(root=tmp_path), transport="carrier-pigeon")
    with pytest.raises(ConfigError, match="transport must be"):
        cfg.validate()


def test_missing_root_is_refused(tmp_path: Path) -> None:
    cfg = ServerConfig(policy=Policy(root=tmp_path / "does-not-exist"))
    with pytest.raises(ConfigError, match="not a directory"):
        cfg.validate()


def test_path_auth_requires_long_secret(tmp_path: Path) -> None:
    cfg = ServerConfig(
        policy=Policy(root=tmp_path),
        transport="http",
        auth_mode="path",
        mcp_path="/mcp/short",
    )
    with pytest.raises(ConfigError, match="at least 24 characters"):
        cfg.validate()


def test_path_auth_with_long_secret_validates(tmp_path: Path) -> None:
    cfg = ServerConfig(
        policy=Policy(root=tmp_path),
        transport="http",
        auth_mode="path",
        mcp_path="/mcp/" + "a" * 32,
    )
    cfg.validate()  # no raise
    assert cfg.path_secret == "a" * 32


def test_unknown_auth_mode_is_refused(tmp_path: Path) -> None:
    cfg = ServerConfig(policy=Policy(root=tmp_path), auth_mode="magic")
    with pytest.raises(ConfigError, match="auth mode must be"):
        cfg.validate()


def test_path_auth_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTMCP_ROOT", str(tmp_path))
    monkeypatch.setenv("LTMCP_AUTH_MODE", "path")
    monkeypatch.setenv("LTMCP_MCP_PATH", "/mcp/" + "b" * 32)
    cfg = load_config()
    assert cfg.auth_mode == "path"
    assert cfg.mcp_path == "/mcp/" + "b" * 32
