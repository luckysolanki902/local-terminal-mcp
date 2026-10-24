# Social posts — local-terminal-mcp

**Repo:** https://github.com/luckysolanki902/local-terminal-mcp
**Demo screenshot:** `./post-demo.jpg` (ChatGPT running real commands on my Mac + finding a security gap)
**Note:** This file is intentionally NOT committed.

---

## X / Twitter (main post)

I connected ChatGPT to my Mac's terminal with a ~700-line MCP server.

Asked it to "analyze this repo like a senior engineer."

It ran git, rg, cat, wc on my machine, counted 1,174 LOC — then found a real security hole in my own code.

All on ChatGPT Plus. Zero Codex/API tokens. 🧵

---

## X / Twitter (thread continuation)

2/ The setup is dead simple:
• A tiny MCP server exposing safe, allowlisted commands (git, rg, cat…)
• cloudflared/ngrok to expose it over HTTPS
• Add it as a custom connector in ChatGPT → done

Now ChatGPT works on my local codebase conversationally. No copy-pasting files.

3/ The kicker: I asked for "the single best improvement" and it caught a genuine gap in its own security model —

the policy checks the *executable name* but not its arguments, so an allowed binary like `cat /outside/path` could read outside the sandbox. Correct, and sharp.

4/ Why it matters: if you already pay for ChatGPT, you can point it at your repo (or any local tool — image/sprite generators, scripts, build steps) and run things locally without burning metered agent tokens.

Read-only by default. You allowlist exactly what it can touch.

5/ It's open source (MIT). Works with ChatGPT, Claude Desktop, and Claude Code.

⭐ https://github.com/luckysolanki902/local-terminal-mcp

---

## LinkedIn (longer form)

I spent an afternoon connecting ChatGPT directly to my Mac's terminal — safely — and the result surprised me.

I built a small, security-first MCP server (~700 lines) that exposes a handful of **allowlisted** commands to any MCP client. Then I added it to ChatGPT as a custom connector over a Cloudflare tunnel.

Then I asked ChatGPT one thing: *"Using only the Local Terminal connector, analyze this repo like a senior engineer."*

It did — on my machine:
→ Ran git, ripgrep, cat, find, wc through the connector
→ Counted 1,174 lines of Python (702 production / 472 tests)
→ Summarized the architecture and security model
→ And found a **real gap in the code**: the policy validates the *name* of the executable but not its arguments, so an allowed binary could still reach outside the intended sandbox. A legitimately good catch.

Two things make this worth sharing:

1. **Cost.** This runs on a flat ChatGPT subscription — not metered API/agent tokens. If you already pay for ChatGPT, you can analyze and operate on local codebases (or drive local tools — asset/image/sprite generators, scripts, builds) without per-token billing.

2. **Safety.** It's deny-by-default: only allowlisted programs, no shell, one command per call, file access confined to a single root, writes opt-in, and HTTP always behind a credential. The AI is never trusted to "decide" what's safe — a deterministic policy layer enforces it.

It's open source under MIT and works with ChatGPT, Claude Desktop, and Claude Code.

👉 https://github.com/luckysolanki902/local-terminal-mcp

What would you point it at first — your codebase, or a local asset pipeline?

---

## The ChatGPT response captured in the screenshot

**Prompt:** "Using ONLY the Local Terminal connector (running on my Mac), analyze this repo like a senior engineer. Run whatever you need (git, rg, cat, find, wc). Then give me: (1) one line on what it is, (2) its security model in 3 bullets, (3) total Python lines of code, (4) the single best improvement. Keep it tight."

**ChatGPT (Worked for 53s):**

**1. What it is**
A small security-first MCP server that lets ChatGPT/Claude inspect and operate on a local repo through tightly constrained terminal/file tools.

**2. Security model**
- Deny by default: only allowlisted executables run, with `shell=False`; pipes, chaining, redirects, substitution, etc. are rejected.
- Filesystem boundary: `read_file`, `write_file`, cwd, and directory listing resolve symlinks/`..` and must remain under the configured root; writes are opt-in.
- Network boundary: HTTP requires either a bearer token or long secret-path capability; output/time are bounded, with optional Host pinning.

**3. Python size**
1,174 lines total across `src/` + `tests/`. Production: 702 · Tests: 472

**4. Best improvement**
Add command-specific argument validation, especially path containment. Right now the policy validates the executable name, not what that executable can do. So an "allowed" binary can bypass the intended read-only/root boundary through its own flags/arguments, e.g. `cat /outside/path`, `find ... -delete`, or mutating `git` subcommands. The strongest architecture would be: allowlisted executable + allowlisted subcommands/options + resolved path arguments constrained to root.

---

## Posting tips

- Attach `post-demo.jpg` to the X post and the LinkedIn post — the screenshot is the hook.
- Best first line on X is the "found a security hole in my own code" angle; lead with it.
- Consider pinning the tweet and adding the repo link in the first reply (links in replies sometimes get better reach than in the main tweet).
- Suggested hashtags (LinkedIn): #ChatGPT #MCP #AI #DeveloperTools #OpenSource
