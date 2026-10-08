#!/usr/bin/env bash
# Loads .env into the environment and starts the server, so LTMCP_* vars
# always take effect (the server itself never reads .env directly).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
source .env
set +a
exec .venv/bin/local-terminal-mcp "$@"
