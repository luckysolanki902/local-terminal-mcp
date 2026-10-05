# local-terminal-mcp

A small, security-first [MCP](https://modelcontextprotocol.io) server that lets
an AI assistant — **ChatGPT**, **Claude Desktop**, **Claude Code**, or any other
MCP client — run a set of **allowlisted** commands and read files from a local
directory on your machine.

The motivating use case: point ChatGPT (or Claude) at a local codebase so it can
run `git`, `rg`, `cat`, etc. and analyse the repo — without copying files around
by hand, and, for ChatGPT, using your flat-rate subscription instead of metered
API/agent tokens.

> ⚠️ **This runs commands on your computer.** It is built to be safe by default
> (read-only, allowlisted, no shell, path-contained, auth-required over the
> network), but you are responsible for how you configure and expose it. Read
> [Security model](#security-model) before exposing it to the internet.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Security model](#security-model)
- [Install](#install)
- [Quick start (local / stdio)](#quick-start-local--stdio)
- [Connect to Claude Desktop](#connect-to-claude-desktop)
- [Connect to Claude Code](#connect-to-claude-code)
- [Connect to ChatGPT (over the internet)](#connect-to-chatgpt-over-the-internet)
- [Configuration reference](#configuration-reference)
- [Tools exposed](#tools-exposed)
- [Development](#development)
- [FAQ](#faq)

---

## Why this exists

Running a coding agent against a large repo through a metered API burns tokens
fast. If you already pay for a ChatGPT or Claude subscription, you can instead
give the assistant a *tool* to read files and run read-only commands on your
machine, and let it analyse the code conversationally. That is what this server
provides, with a security model strong enough that you can leave write access
turned off and expose only read access.

## How it works

```
  MCP client (ChatGPT / Claude Desktop / Claude Code)
        │
        │   stdio  ── local, no network  ────────────┐
        │                                            │
        │   HTTP (Streamable) + bearer token         │
        ▼                                            ▼
  cloudflared / reverse proxy  ──────────►  local-terminal-mcp
        (only needed for ChatGPT)                    │
                                                     ▼
                                     policy engine  →  git / rg / cat / ...
                                     (allowlist, no shell, path containment)
```

- **Local clients (Claude Desktop, Claude Code)** talk to the server over
  **stdio** — the server is a child process, nothing is exposed to the network.
  This is the most secure mode and needs no tunnel.
- **ChatGPT** can only reach servers over public **HTTPS**, so for ChatGPT you
  run the server in **HTTP** mode behind a tunnel (e.g. `cloudflared`) and
  protect it with a bearer token (ideally plus Cloudflare Access).

## Security model

The server never trusts the model's judgement. A deterministic policy layer
(`src/local_terminal_mcp/policy.py`) gates every request:

1. **Allowlist, deny by default.** Only commands whose program is on the
   allowlist run. Anything else is rejected. The default allowlist is
   read-only (`git`, `rg`, `grep`, `ls`, `cat`, `head`, `tail`, `wc`, `find`,
   `tree`, `stat`, `file`, `pwd`, `echo`, `diff`).
2. **One command, no shell.** Commands are parsed with `shlex` and executed
   with `shell=False`. Pipes (`|`), chaining (`&&`, `;`), redirects (`>`),
   background (`&`) and command substitution (`$(...)`, backticks) are all
   **rejected** — so `git status && rm -rf /` never runs.
3. **Path containment.** Every file path is fully resolved (symlinks and `..`
   included) and must land inside the configured root directory.
4. **Writes are opt-in.** `write_file` is disabled unless you start with
   `--allow-write`. The default is read-only.
5. **Fail closed on exposure.** The server refuses to start in HTTP mode without
   an auth token of at least 16 characters.
6. **Bounded output and time.** Output is truncated to a byte limit and every
   command has a timeout.

**Layers you add around it:**

- Put **Cloudflare Access** (or equivalent) in front of the tunnel. A bearer
  token is the app-level check; Access is the network-level wall. Obscure
  tunnel URLs are **not** security.
- Run the server as a **low-privilege user**, ideally inside a container or VM,
  not as your main account.
- Keep write access off unless you truly need it, and when you do, keep the root
  scoped to a single project directory.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/luckysolanki/local-terminal-mcp.git
cd local-terminal-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Quick start (local / stdio)

Run it read-only against a project directory:

```bash
local-terminal-mcp --root /path/to/your/repo
```

That starts the server on stdio, waiting for an MCP client. See the sections
below to connect a specific client.

## Connect to Claude Desktop

Add the server to Claude Desktop's config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```jsonc
{
  "mcpServers": {
    "local-terminal": {
      "command": "/path/to/local-terminal-mcp/.venv/bin/local-terminal-mcp",
      "args": ["--root", "/path/to/your/repo"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the connectors / tools menu.

## Connect to Claude Code

```bash
claude mcp add local-terminal -- \
  /path/to/local-terminal-mcp/.venv/bin/local-terminal-mcp --root /path/to/your/repo
```

Then `/mcp` inside Claude Code will list the server.

## Connect to ChatGPT (over the internet)

ChatGPT can only reach servers over public HTTPS, so run in HTTP mode behind a
tunnel. The flow is: **(1)** start the server, **(2)** expose it with a tunnel
(ngrok *or* cloudflared), **(3)** add the connector in ChatGPT.

The server validates the incoming `Host` header for DNS-rebinding protection.
Because tunnels forward an arbitrary public hostname and every request already
requires a bearer token, the server accepts any host by default. For defense in
depth, pin it to your tunnel hostname with `--allowed-hosts`.

### Step 1 — start the server in HTTP mode

```bash
export LTMCP_AUTH_TOKEN="$(openssl rand -hex 24)"
echo "token: $LTMCP_AUTH_TOKEN"   # you'll paste this into ChatGPT
local-terminal-mcp --transport http --host 127.0.0.1 --port 8000 --root /path/to/your/repo
```

### Step 2 — expose it with a tunnel

<details open>
<summary><b>Option A — ngrok (works on the free tier)</b></summary>

Install ngrok and add your authtoken (one-time, from the ngrok dashboard):

```bash
brew install ngrok            # or: https://ngrok.com/download
ngrok config add-authtoken <YOUR_NGROK_AUTHTOKEN>
```

Start the tunnel pointing at the local port:

```bash
ngrok http 8000
```

ngrok prints a forwarding URL like `https://a1b2-34-56.ngrok-free.app`. Your
MCP endpoint is that URL + `/mcp`.

Notes for the **free tier**:

- The URL is **random and changes every restart** — you'll re-paste it into
  ChatGPT each session. (A paid plan gives a stable `--domain`.)
- ngrok shows a browser interstitial on the free tier for *browser* traffic;
  ChatGPT's MCP client sends API requests, so it is not affected.
- Optionally lock the Host header to the tunnel domain:

  ```bash
  local-terminal-mcp --transport http --port 8000 --root /path/to/repo \
    --allowed-hosts a1b2-34-56.ngrok-free.app
  ```

</details>

<details>
<summary><b>Option B — cloudflared (stable custom domain)</b></summary>

With a named tunnel on your own domain (see
[`examples/cloudflared-config.yml`](examples/cloudflared-config.yml)):

```yaml
tunnel: lucky-tunnel
credentials-file: /Users/you/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: mcp.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

```bash
cloudflared tunnel run lucky-tunnel
```

Strongly recommended: put **Cloudflare Access** in front of
`mcp.yourdomain.com`. Your MCP endpoint is `https://mcp.yourdomain.com/mcp`.

</details>

### Step 3 — add the connector in ChatGPT

Requires a plan with Developer Mode (Plus/Pro/Team/Enterprise/Edu as of late
2025):

- Settings → **Connectors** → **Advanced** → enable **Developer Mode**.
- **Add custom connector** → URL: your tunnel URL + `/mcp`
  (e.g. `https://a1b2-34-56.ngrok-free.app/mcp`).
- Authentication: **Bearer token** → paste the token from step 1.
- Confirm trust. ChatGPT discovers the tools automatically.

The MCP endpoint is served at the `/mcp` path. A `GET /healthz` endpoint (no
auth) is available for tunnel/uptime checks.

## Configuration reference

Every option has a CLI flag and an `LTMCP_`-prefixed environment variable. CLI
flags win over environment variables.

| CLI flag | Env var | Default | Meaning |
|---|---|---|---|
| `--root` | `LTMCP_ROOT` | cwd | Directory the server is confined to |
| `--transport` | `LTMCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `--host` | `LTMCP_HOST` | `127.0.0.1` | HTTP bind host |
| `--port` | `LTMCP_PORT` | `8000` | HTTP bind port |
| `--auth-token` | `LTMCP_AUTH_TOKEN` | — | Bearer token (required for HTTP) |
| `--allowed-hosts` | `LTMCP_ALLOWED_HOSTS` | any | Comma-separated `Host` header allowlist (e.g. your tunnel hostname) |
| `--allow-commands` | `LTMCP_ALLOW_COMMANDS` | read-only set | Comma-separated allowlist |
| `--allow-write` | `LTMCP_ALLOW_WRITE` | `false` | Enable `write_file` |
| `--max-output-bytes` | `LTMCP_MAX_OUTPUT_BYTES` | `100000` | Output truncation limit |
| `--timeout` | `LTMCP_TIMEOUT` | `120` | Per-command timeout (seconds) |

## Tools exposed

| Tool | Available when | Description |
|---|---|---|
| `run_command` | always | Run one allowlisted command (no shell). |
| `read_file` | always | Read a file inside the root. |
| `list_directory` | always | List a directory inside the root. |
| `write_file` | `--allow-write` | Write a file inside the root. |

## Development

```bash
pip install -e ".[dev]"
pytest            # run the test suite
ruff check .      # lint
```

The security-critical logic lives in `policy.py` and is covered by
`tests/test_policy.py`. If you change the policy, add a test for it.

## FAQ

**Can it run any command?** No — only programs on the allowlist, one at a time,
with no shell. Expand the allowlist with `--allow-commands` if you need more.

**Is the obscure tunnel URL enough protection?** No. Always use a bearer token,
and put Cloudflare Access (or similar) in front for anything beyond quick local
testing.

**Why no pipes or `&&`?** Because allowing shell composition is the easiest way
to smuggle a dangerous command past an allowlist. Run multiple tool calls
instead.

## License

MIT — see [LICENSE](LICENSE).
