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

> 🚀 **Just want to get going?** Follow the [install & setup checklist](INSTALL.md).

---

## Table of contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Security model](#security-model)
- [Install](#install)
- [Quick start (local / stdio)](#quick-start-local--stdio)
- [Connect to Claude Desktop](#connect-to-claude-desktop)
- [Connect to Claude Code](#connect-to-claude-code)
- [Connect to ChatGPT (over the internet)](#connect-to-chatgpt-over-the-internet) · [exact step-by-step guide](docs/CHATGPT_SETUP.md)
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

## What you can do with it

- **Work on your codebase from ChatGPT without spending Codex/API tokens.**
  Let ChatGPT run `git`, `rg`, `cat`, `find`, etc. to read, search and reason
  about a local repo — billed to your flat ChatGPT subscription instead of
  metered agent/API usage.
- **Run local generators from ChatGPT — images, sprites, assets — unmetered.**
  The server runs *any program you allowlist*. Allowlist your own generation
  CLI or script and ChatGPT can trigger it locally, as many times as you like,
  without using ChatGPT's built-in image quota. For example, to let ChatGPT
  drive a local sprite/image script:

  ```bash
  local-terminal-mcp --transport http --port 3003 \
    --root /path/to/assets-project \
    --auth path --mcp-path "/mcp/$SECRET" \
    --allow-commands "python,node,convert,aseprite" \
    --allow-write
  ```

  Then ask ChatGPT to call `run_command` with e.g.
  `python gen_sprite.py --seed 42 --out sprites/hero.png`, and `read_file` /
  `list_directory` to inspect the results. (`--allow-write` is only needed if
  the generator writes into the root; keep it off for read-only analysis.)
- **Save images ChatGPT makes with its *own* image skill into your repo.** A
  small [browser extension](browser-extension/) auto-saves every image ChatGPT
  generates straight into your repo (full quality, hands-free) — the one thing a
  plain connector can't do. See
  [Saving ChatGPT's own generated images](#saving-chatgpts-own-generated-images).
- **Let ChatGPT *see* images in your repo.** `read_image` returns a picture as
  an image (auto-downscaled if large), so ChatGPT can critique art, read text
  from a screenshot/resume, or compare assets.

> You decide exactly which programs are reachable. The default allowlist is
> read-only; everything beyond it is opt-in.

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

> 📄 **For the exact, step-by-step tested procedure, see
> [docs/CHATGPT_SETUP.md](docs/CHATGPT_SETUP.md).** The summary below covers the
> same flow.

ChatGPT can only reach servers over public HTTPS, so run in HTTP mode behind a
tunnel. The flow is: **(1)** start the server, **(2)** expose it with a tunnel
(ngrok *or* cloudflared), **(3)** add the connector in ChatGPT.

> **Why `path` auth for ChatGPT?** ChatGPT's *Create custom MCP server* dialog
> only offers **OAuth** or **No authentication** — there is no field for a
> static bearer token. So instead of a header, we put an unguessable secret in
> the URL path (a *capability URL*) and select **No authentication** in ChatGPT.
> The MCP route exists only at that secret path; probes of the bare `/mcp`
> return 404. This is appropriate for personal use; note the secret appears in
> the URL (and thus in edge/proxy logs). For a shared or higher-value
> deployment, implement OAuth instead.

The server also validates the incoming `Host` header for DNS-rebinding
protection. Because tunnels forward an arbitrary public hostname, the server
accepts any host by default; pin it to your tunnel hostname with
`--allowed-hosts` for defense in depth.

### Step 1 — start the server in HTTP mode (path auth)

```bash
SECRET="$(openssl rand -hex 16)"
echo "MCP URL path: /mcp/$SECRET"
local-terminal-mcp --transport http --host 127.0.0.1 --port 8000 \
  --root /path/to/your/repo \
  --auth path --mcp-path "/mcp/$SECRET"
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
MCP endpoint is that URL + your secret path, e.g.
`https://a1b2-34-56.ngrok-free.app/mcp/<SECRET>`.

Notes for the **free tier**:

- The URL is **random and changes every restart** — you'll re-paste it into
  ChatGPT each session. (A paid plan gives a stable `--domain`.)
- ngrok shows a browser interstitial on the free tier for *browser* traffic;
  ChatGPT's MCP client sends API requests, so it is not affected.
- Optionally lock the Host header to the tunnel domain:

  ```bash
  local-terminal-mcp --transport http --port 8000 --root /path/to/repo \
    --auth path --mcp-path "/mcp/$SECRET" \
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

Your MCP endpoint is `https://mcp.yourdomain.com/mcp/<SECRET>`. (If you also
front it with Cloudflare Access, note ChatGPT cannot complete an Access login,
so use a reserved port/hostname routed directly to the server, as here, rather
than an Access-gated one.)

</details>

### Step 3 — add the connector in ChatGPT

Requires a plan that has the custom MCP feature (Plus/Pro/Team/Enterprise/Edu):

- **Plugins** → **Add ▾** → **Create custom MCP server**.
- **Name**: e.g. `Local Terminal`.
- **Server URL**: your tunnel URL + secret path
  (e.g. `https://a1b2-34-56.ngrok-free.app/mcp/<SECRET>`).
- **Authentication**: **No authentication** (the secret path is the credential).
- Tick **I understand and want to continue**, then **Create as a plugin** and
  **Connect**.

ChatGPT discovers the tools automatically. A `GET /healthz` endpoint (no auth)
is available for tunnel/uptime checks. To use the tools in a chat, ask ChatGPT
to use the connector by name.

## Configuration reference

Every option has a CLI flag and an `LTMCP_`-prefixed environment variable. CLI
flags win over environment variables.

| CLI flag | Env var | Default | Meaning |
|---|---|---|---|
| `--root` | `LTMCP_ROOT` | cwd | Directory the server is confined to |
| `--transport` | `LTMCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `--host` | `LTMCP_HOST` | `127.0.0.1` | HTTP bind host |
| `--port` | `LTMCP_PORT` | `8000` | HTTP bind port |
| `--auth` | `LTMCP_AUTH_MODE` | `bearer` | HTTP auth mode: `bearer` (header) or `path` (secret in URL) |
| `--auth-token` | `LTMCP_AUTH_TOKEN` | — | Bearer token (required for `bearer` mode) |
| `--mcp-path` | `LTMCP_MCP_PATH` | `/mcp` | Path the MCP endpoint is served at; for `path` auth, end it with a long random segment |
| `--allowed-hosts` | `LTMCP_ALLOWED_HOSTS` | any | Comma-separated `Host` header allowlist (e.g. your tunnel hostname) |
| `--allow-commands` | `LTMCP_ALLOW_COMMANDS` | read-only set | Comma-separated allowlist patterns (e.g. `git *,rg *`) |
| `--no-contain-path-args` | `LTMCP_CONTAIN_PATH_ARGS=false` | on | Stop confining command path-args to the root |
| `--approval` | `LTMCP_APPROVAL_MODE` | `none` | Ask before running non-allowlisted commands: `none`/`tty`/`file` |
| `--approvals-dir` | `LTMCP_APPROVALS_DIR` | — | Directory to coordinate approvals in `file` mode |
| `--approval-timeout` | `LTMCP_APPROVAL_TIMEOUT` | `60` | Seconds to wait for a decision |
| `--allowlist-file` | `LTMCP_ALLOWLIST_FILE` | — | JSON file that "always allow" appends to |
| `--image-gen-cmd` | `LTMCP_IMAGE_GEN_CMD` | — | Local image generator template (`{prompt}`/`{output}`); enables `generate_image` |
| `--image-timeout` | `LTMCP_IMAGE_TIMEOUT` | `300` | Image generation timeout (seconds) |
| `--inbox` | `LTMCP_INBOX` | — | Staging folder (ideally a repo subdir like `incoming`) to import files FROM; enables the import tools |
| `--inbox-ttl-days` | `LTMCP_INBOX_TTL_DAYS` | `0` | Auto-delete inbox files older than N days (`0` = never); use only with a dedicated staging folder |
| `--allow-write` | `LTMCP_ALLOW_WRITE` | `false` | Enable `write_file`, `write_file_base64`, `generate_image` |
| `--max-output-bytes` | `LTMCP_MAX_OUTPUT_BYTES` | `100000` | Output truncation limit |
| `--timeout` | `LTMCP_TIMEOUT` | `120` | Per-command timeout (seconds) |

## Tools exposed

| Tool | Available when | Description |
|---|---|---|
| `run_command` | always | Run one allowlisted command (no shell). `cd` persists per session; takes an optional `session`. |
| `read_file` | always | Read a text file inside the root. |
| `read_image` | always | Read an image (png/jpg/gif/webp/bmp) and return it **as an image** the model can see (large images are auto-downscaled to fit). |
| `list_directory` | always | List a directory inside the root. |
| `open_terminal` | always | Open a session with its own persistent working directory. |
| `list_terminals` | always | List open sessions and their directories. |
| `close_terminal` | always | Close a session. |
| `write_file` | `--allow-write` | Write a UTF-8 text file inside the root. |
| `write_file_base64` | `--allow-write` | Write a **binary** file (e.g. a PNG) from base64 — for saving generated images and other assets. |
| `move_file` | `--allow-write` | Move/rename a file or directory within the root. |
| `copy_file` | `--allow-write` | Copy a file or directory within the root. |
| `generate_image` | `--image-gen-cmd` | Generate an image from a prompt using a **local** generator, saved into the root. |
| `list_inbox` | `--inbox` | List recent files (images) in a trusted inbox folder (e.g. Downloads). |
| `import_recent_images` | `--inbox` + `--allow-write` | Import the most recent image(s) from the inbox into the repo. |
| `import_file` | `--inbox` + `--allow-write` | Import a named file from the inbox into the repo. |

### Images

`write_file` only carries UTF-8 text, so binary assets (PNGs) use
`write_file_base64` (plain base64 or a `data:` URL). `read_image` returns the
file as an MCP image block, so the assistant actually *sees* it rather than
getting base64 text. To generate images locally (free, unlimited, on your
machine), point `--image-gen-cmd` at a local generator with `{prompt}` and
`{output}` placeholders — e.g. `sd -p {prompt} -o {output} --steps 8` — and the
`generate_image` tool runs it server-side (no shell), writing straight into the
repo. A ready-to-use example generator is included at
[`examples/pixel_gen.py`](examples/pixel_gen.py) (procedural pixel-art sprites,
no model download — `--image-gen-cmd "python examples/pixel_gen.py {prompt} {output}"`);
swap in mflux / stable-diffusion.cpp / the OpenAI image API for photoreal output.
Note: ChatGPT caps each tool call at ~45s, so a slow generator can time out there
(local MCP clients have no such limit).

> **Note on ChatGPT's *own* image skill.** The above generates images via a
> *local* tool. ChatGPT's built-in "Create image" produces pictures that live
> only in the browser and can't be handed to a connector — to save those, see
> [Saving ChatGPT's own generated images](#saving-chatgpts-own-generated-images).

### Sessions (persistent working directory)

You don't need to prefix every call with `cd …`. Run `cd <dir>` once and it
**persists** for later calls — there's a shared default session, and you can
`open_terminal` for additional named sessions (pass the returned id as
`session`). This is *not* a real shell: commands still run one-at-a-time with
`shell=False` and the allowlist, so `cd src && rm -rf .` is still rejected —
you just `cd src` once, then run commands.

### Allowlist patterns

`--allow-commands` entries are glob patterns over the command's argv:

- `git` or `git *` — `git` with any arguments.
- `git log *` — only `git log …`.
- `rg *`, `cat *`, etc.

A program-position wildcard (`*`) and absolute-path programs (`/bin/rm`) are
**rejected**. With path containment on (default), arguments that are absolute,
`~`, or use `..` are refused — so `cat /etc/passwd` stays blocked even under
`cat *`. Disable with `--no-contain-path-args`.

### Saving ChatGPT's own generated images

ChatGPT's native image skill renders images **only in the browser** — the model
and the MCP server never receive the bytes (no URL either), so they can't be
saved through a normal connector tool. There are two working routes:

**Route 1 — browser extension (recommended; full quality, hands-free).** The
[browser extension](browser-extension/) reads the rendered image from the page
and POSTs it to the server's `/upload` endpoint (secret-gated, write-mode,
path-contained, CORS-restricted to ChatGPT), writing it straight into your repo
(e.g. a staging folder like `incoming/`). Loading it is a **one-time** step;
after that, turn on **auto-save** and every new image ChatGPT generates is
uploaded automatically as it appears — no clicks, and no MCP→browser channel is
needed (the extension watches the page itself; the server just receives the
uploads). Setup: [browser-extension/README.md](browser-extension/README.md).

> Why an extension at all? The model can't pass image bytes to a connector tool
> (it won't, even for a tiny payload), the code-interpreter sandbox has no
> network, and the page's CSP blocks direct posts to localhost — so the browser
> is the only place that can both *see* the generated image and *reach* your
> local server. An extension is the clean form of that bridge.

**Route 2 — download + import (no install).** Use ChatGPT's own download button
(saves the full-res image to your Downloads folder), then import it with the
inbox tools. Run with a staging inbox and a TTL so it self-cleans:

```bash
local-terminal-mcp --transport http --port 3003 --root /path/to/repo \
  --auth path --mcp-path "/mcp/$SECRET" --allow-write \
  --inbox /path/to/repo/incoming --inbox-ttl-days 7
```

Then, after downloading images into that folder (or configuring the extension to
upload there):

> *"Local Terminal: import the 3 most recent images from my inbox into assets/."*

`list_inbox` lists the staging folder, `import_recent_images` / `import_file`
copy or move them into a permanent folder, and files left in the inbox longer
than `--inbox-ttl-days` are auto-deleted. The inbox is a source only — writes
still land only inside the root.

> **Reading images back.** `read_image` returns an image file as an MCP image
> block, so the assistant can actually *see* it — describe art, read text from a
> screenshot/resume, etc. Large images are auto-downscaled to fit the transport
> limit (Pillow required — `pip install -e ".[images]"`); the file on disk is
> unchanged.

## Approvals & the dynamic allowlist (Claude Code–style)

By default, a command whose program isn't on the static allowlist is simply
refused. Enable `--approval` to be *asked* instead — the same model as Claude
Code:

- **deny** → the command is refused.
- **once** → it runs this time only; nothing is saved.
- **always** → it runs **and** the program is appended to a JSON allowlist
  (`--allowlist-file`), so it's permitted without asking next time.

The decision is always made **locally**, on the machine the server runs on — a
remote caller (ChatGPT) can only *request* a command.

**`tty` mode** (server running in a terminal): you're prompted right there.

```bash
local-terminal-mcp --root /path/to/repo \
  --approval tty --allowlist-file ~/.ltmcp-allow.json
```

**`file` mode** (server backgrounded, e.g. behind a tunnel): the request is
queued and you decide with the `approve`/`deny` CLI.

```bash
# server
local-terminal-mcp --transport http --port 3003 --root /path/to/repo \
  --auth path --mcp-path "/mcp/$SECRET" \
  --approval file --approvals-dir ~/.ltmcp/approvals \
  --allowlist-file ~/.ltmcp/allow.json

# in another terminal, when ChatGPT tries something new:
local-terminal-mcp approvals --approvals-dir ~/.ltmcp/approvals   # list pending
local-terminal-mcp approve <id> --always --approvals-dir ~/.ltmcp/approvals
local-terminal-mcp deny <id> --approvals-dir ~/.ltmcp/approvals
```

> ⚠️ Approving a program like `python3` grants it broad power (it's an
> interpreter). Approve deliberately — "always allow" is persistent.

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
with no shell. Expand the static allowlist with `--allow-commands`, or turn on
`--approval` to be asked (once / always) when a new program is requested, Claude
Code–style — see [Approvals](#approvals--the-dynamic-allowlist-claude-codestyle).

**How is the HTTP endpoint protected?** With a credential, never obscurity of
the tunnel hostname alone. Use `bearer` auth (header token) for clients that
support custom headers, or `path` auth (a long random secret in the URL, a
*capability URL*) for ChatGPT, whose UI only offers OAuth or no-auth. For a
shared or higher-value deployment, implement OAuth and/or front it with
Cloudflare Access.

**Why no pipes or `&&`?** Because allowing shell composition is the easiest way
to smuggle a dangerous command past an allowlist. Run multiple tool calls
instead.

## License

MIT — see [LICENSE](LICENSE).
