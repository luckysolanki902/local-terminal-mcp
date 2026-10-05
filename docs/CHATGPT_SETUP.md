# Connecting to ChatGPT — exact setup

This is the precise, tested procedure for using `local-terminal-mcp` as a custom
connector in ChatGPT, so ChatGPT can analyse a local codebase on your Mac.

ChatGPT's *Create custom MCP server* dialog only offers **OAuth** or **No
authentication** — there is no static bearer-token field. So we authenticate
with a **capability URL**: a long random secret in the URL path. The MCP route
exists only at that secret path, and probes of the bare `/mcp` return `404`.

> Security note: the secret lives in the URL, so it can appear in edge/proxy
> logs. This is fine for personal use. Keep the default **read-only** policy,
> and for a shared/higher-value deployment implement OAuth instead.

---

## Prerequisites

- A ChatGPT plan with the custom-MCP feature (Plus/Pro/Team/Enterprise/Edu).
- `local-terminal-mcp` installed (see the main [README](../README.md#install)).
- A tunnel that gives a public HTTPS URL: **cloudflared** or **ngrok**.

---

## Step 1 — start the server with a secret path

```bash
cd /path/to/local-terminal-mcp
source .venv/bin/activate

SECRET="$(openssl rand -hex 16)"              # the capability secret
PORT=3003                                     # a port your tunnel routes
echo "MCP path: /mcp/$SECRET"

local-terminal-mcp \
  --transport http --host 127.0.0.1 --port "$PORT" \
  --root /path/to/the/repo/you/want/to/analyse \
  --auth path --mcp-path "/mcp/$SECRET" \
  --allowed-hosts <your-public-hostname>
```

The server is read-only by default (safe programs only, no shell, no writes).

---

## Step 2 — expose it with a tunnel

### Option A — cloudflared (named tunnel on your own domain)

If your `~/.cloudflared/config.yml` already routes a hostname to the port, just
run the tunnel. Example ingress entry:

```yaml
ingress:
  - hostname: 3003.yourdomain.com
    service: http://localhost:3003
  - service: http_status:404
```

```bash
cloudflared tunnel run <your-tunnel-name>
```

Public endpoint: `https://3003.yourdomain.com/mcp/<SECRET>`

### Option B — ngrok (works on the free tier)

```bash
ngrok config add-authtoken <YOUR_NGROK_AUTHTOKEN>   # one-time
ngrok http 3003
```

ngrok prints `https://<random>.ngrok-free.app`. Public endpoint:
`https://<random>.ngrok-free.app/mcp/<SECRET>`
(the free-tier URL changes on every restart; set `--allowed-hosts` to it).

---

## Step 3 — verify the public URL before touching ChatGPT

```bash
BASE="https://<your-public-hostname>"
SECRET_PATH="/mcp/<SECRET>"

# health (no auth) → 200
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/healthz"

# bare /mcp without the secret → 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/mcp"

# the secret path, MCP initialize → returns serverInfo
curl -s -X POST "$BASE$SECRET_PATH" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
```

The third call should return a line containing
`"serverInfo":{"name":"local-terminal-mcp",...}`.

---

## Step 4 — add the connector in ChatGPT

1. Open **ChatGPT → Plugins** (left sidebar).
2. Top-right **Add ▾** → **Create custom MCP server**.
3. Fill the dialog:
   - **Name**: `Local Terminal`
   - **Connection**: leave on **Server URL**.
   - **Server URL**: `https://<your-public-hostname>/mcp/<SECRET>`
   - **Authentication**: select **No authentication**.
4. Tick **I understand and want to continue**.
5. Click **Create as a plugin**, then **Connect Local Terminal** on the consent
   screen.

You should see **"Local Terminal is now connected"** and a new icon in the
*Installed* row.

---

## Step 5 — test it in a chat

Start a **New chat** and send:

```
Use the Local Terminal connector. Call run_command with "git log --oneline -n 5",
then call list_directory on ".". Show me the raw tool output from both calls.
```

Expected: ChatGPT returns the real `git log` output and directory listing from
your machine. You can confirm the calls server-side — each one is a
`POST /mcp/<SECRET> ... 200 OK` in the server log, while any `POST /mcp` is
`404`.

To see the security boundary, ask it to run something off the allowlist (e.g.
`rm -rf .`): the tool returns `refused: command 'rm' is not on the allowlist`.

---

## Stopping / restarting

```bash
# stop
pkill -f local-terminal-mcp
pkill -f "cloudflared tunnel run"        # or stop your ngrok process

# restart from the saved config (.env)
set -a; source .env; set +a
local-terminal-mcp        # reads LTMCP_* from the environment
```

Rotate the secret any time by generating a new `SECRET`, restarting the server
with the new `--mcp-path`, and updating the Server URL in the ChatGPT connector.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| ChatGPT "couldn't connect" | Tunnel down, wrong port, or wrong secret path. Re-run the Step 3 curls. |
| `404` on the secret path | Server not started with `--auth path --mcp-path /mcp/<SECRET>`, or URL typo. |
| `421`/host error | Add your public hostname to `--allowed-hosts`. |
| Tools not called | Name the connector in your message, or check it's enabled for the chat. |
| Only OAuth/No-auth offered | Expected — use **No authentication** with the capability URL. |
