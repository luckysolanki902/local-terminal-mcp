"""local-terminal-mcp: a safe MCP server for running local commands.

Exposes a small set of policy-gated tools (run a command, read/write a file,
list a directory) over MCP so an assistant such as ChatGPT or Claude can
analyse a local codebase. The security model lives in :mod:`policy`.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import ServerConfig, load_config
from .policy import Policy, PolicyError

__all__ = ["ServerConfig", "load_config", "Policy", "PolicyError", "__version__"]
