# Security

This project runs commands on your machine on behalf of an AI assistant. Treat
it accordingly.

## Threat model

The server assumes the **assistant may be adversarial or manipulated** (e.g. via
prompt injection in files it reads). Safety therefore does not depend on the
model behaving well. A deterministic policy layer (`policy.py`) gates every
request:

- **Allowlist, deny by default** — only listed programs run; unknown is denied.
- **No shell** — one program per call, executed with `shell=False`; pipes,
  chaining, redirects and command substitution are rejected.
- **Path containment** — paths are fully resolved (symlinks, `..`) and must stay
  inside the configured root.
- **Writes are opt-in** — disabled unless started with `--allow-write`.
- **Fail closed** — HTTP transport refuses to start without a bearer token of at
  least 16 characters.

## Deploying safely over the internet

- Always use a bearer token. An obscure tunnel URL is **not** a security control.
- Put **Cloudflare Access** (or an equivalent identity proxy) in front of the
  tunnel so unauthenticated requests never reach the server.
- Pin `--allowed-hosts` to your exact tunnel hostname.
- Run as a **low-privilege user**, ideally inside a container or VM.
- Keep write access off unless you need it; scope the root to one project.

## Reporting a vulnerability

Please open a private security advisory on the repository, or contact the
maintainer directly, rather than filing a public issue. Include reproduction
steps and the impact you observed.
