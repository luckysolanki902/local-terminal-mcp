# ChatGPT → Local MCP Image Saver (browser extension)

Saves **ChatGPT-generated images** straight into your local repo via
`local-terminal-mcp` — including images made by ChatGPT's own image skill,
which otherwise can't be saved through a connector.

## Why this is needed

A ChatGPT-generated image is only rendered **in the browser** (as a `blob:`
URL). The model and the MCP server can't reach it — but the browser can. This
extension reads those rendered image bytes on the page and POSTs them to the
server's `/upload` endpoint, which writes them into your repo at full quality.

```
ChatGPT renders image (blob)  →  extension fetches the bytes
     →  POST to  http://localhost:PORT/mcp/<secret>/upload?path=assets/x.png
     →  local-terminal-mcp writes the file into your repo  ✅
```

## Setup

1. **Run the server in HTTP + write mode** (the `/upload` endpoint needs write):

   ```bash
   SECRET="$(openssl rand -hex 16)"
   local-terminal-mcp --transport http --port 3003 --root /path/to/your/repo \
     --auth path --mcp-path "/mcp/$SECRET" --allow-write
   ```

   The extension talks to the server directly on `localhost`, so **no tunnel is
   required** for image saving (the tunnel is only for the ChatGPT connector).

2. **Load the extension** in Chrome/Brave:
   - Open `chrome://extensions` (or `brave://extensions`).
   - Enable **Developer mode**.
   - **Load unpacked** → select this `browser-extension/` folder.

3. **Configure it**: click the extension icon and set:
   - **Server base URL**: `http://localhost:3003`
   - **MCP secret path**: `/mcp/<secret>` (same secret as above)
   - **Target folder**: e.g. `assets`

## Use

On any `chatgpt.com` page with generated images, click the floating
**"⬇ Save images → repo"** button (bottom-right). Every generated image on the
page is saved into `<target folder>/gpt-<timestamp>-N.png` in your repo, at full
resolution. A toast reports how many were saved.

## Security

- The `/upload` endpoint is gated by the **secret path** (same capability as the
  MCP endpoint), requires **write mode**, and is **path-contained** to the root.
- CORS is restricted to `https://chatgpt.com` / `https://chat.openai.com`.
- `localhost` HTTP is exempt from mixed-content blocking, so the https ChatGPT
  page is allowed to POST to your local server.
