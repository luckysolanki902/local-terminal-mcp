# Install & setup checklist

A 2-minute path from zero to: ChatGPT/Claude running commands on your machine,
plus (optionally) auto-saving ChatGPT's own generated images into your repo.

> Full reference: [README.md](README.md). Security model: [SECURITY.md](SECURITY.md).

---

## 1. Install the server

Requires Python 3.10+.

```bash
git clone https://github.com/luckysolanki902/local-terminal-mcp.git
cd local-terminal-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[images]"      # [images] adds Pillow for read_image downscaling
```

---

## 2A. Use it locally (Claude Desktop / Claude Code) — simplest, most secure

No network, no tunnel. stdio transport.

- **Claude Code:**
  ```bash
  claude mcp add local-terminal -- \
    "$(pwd)/.venv/bin/local-terminal-mcp" --root /path/to/your/repo
  ```
- **Claude Desktop:** add to `claude_desktop_config.json`:
  ```jsonc
  {
    "mcpServers": {
      "local-terminal": {
        "command": "/abs/path/local-terminal-mcp/.venv/bin/local-terminal-mcp",
        "args": ["--root", "/path/to/your/repo"]
      }
    }
  }
  ```

Add `--allow-write` to let it write files. Done — skip to [Verify](#4-verify).

---

## 2B. Use it from ChatGPT — needs a public HTTPS tunnel

ChatGPT can only reach public HTTPS, so run in HTTP mode behind ngrok/cloudflared.

```bash
SECRET="$(openssl rand -hex 16)"
echo "secret path: /mcp/$SECRET"      # you'll paste this into ChatGPT + the extension

local-terminal-mcp --transport http --host 127.0.0.1 --port 3003 \
  --root /path/to/your/repo \
  --auth path --mcp-path "/mcp/$SECRET" \
  --allow-write \
  --inbox /path/to/your/repo/incoming --inbox-ttl-days 7
```

Expose port 3003 with a tunnel (pick one):

```bash
ngrok http 3003                       # free; URL changes each restart
# or a cloudflared named tunnel mapping a hostname -> http://localhost:3003
```

Then in ChatGPT: **Plugins → Add ▾ → Create custom MCP server**
- **Server URL:** `https://<your-tunnel-host>/mcp/$SECRET`
- **Authentication:** **No authentication** (the secret path is the credential)
- Tick **I understand** → **Create** → **Connect**.

> Changed which tools the server exposes (e.g. flipped `--allow-write`)? **Delete
> and recreate** the ChatGPT connector so it re-fetches the tool list — a plain
> reconnect does not refresh it.

Full walkthrough (ngrok + cloudflared): [docs/CHATGPT_SETUP.md](docs/CHATGPT_SETUP.md).

---

## 3. (Optional) Save ChatGPT's *own* generated images into your repo

ChatGPT's native "Create image" output lives only in the browser — the model and
server can't reach it. The browser extension bridges it. **One-time load:**

1. Open **`brave://extensions`** (or `chrome://extensions`).
2. Toggle **Developer mode** (top-right).
3. **Load unpacked** → select this repo's **`browser-extension/`** folder.
4. Click the extension's icon and set:
   - **Server base URL:** `http://localhost:3003` (talks to the server directly; no tunnel needed here)
   - **MCP secret path:** `/mcp/<your SECRET>`
   - **Target folder:** `incoming`
   - ✅ **Auto-save new generated images**
   - **Save settings**

From now on, every image ChatGPT generates auto-uploads to `incoming/` at full
quality — no clicks. File the keepers with the connector:

> *"Local Terminal: import the latest images from my inbox into references/art."*

The `incoming/` staging folder self-cleans after `--inbox-ttl-days` days. Details:
[browser-extension/README.md](browser-extension/README.md).

---

## 4. Verify

Ask the assistant (ChatGPT via `@local`, or Claude):

> *"Using Local Terminal, run `git log --oneline -5` and list this directory."*

You should get your real repo's output. For images, drop one in the repo and ask
it to `read_image` and describe it.

---

## Security quick notes

- Default is **read-only** and **allowlisted** (only safe inspection commands).
- HTTP refuses to start without a credential (secret path or bearer token).
- Writes are confined to the `--root`; command path-arguments are too.
- Turn on `--approval tty|file` to be asked before running non-allowlisted
  commands (Claude Code–style once/always). See [README.md](README.md).
