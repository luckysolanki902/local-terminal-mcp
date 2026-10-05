# Contributing

Thanks for your interest in improving local-terminal-mcp.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Guidelines

- **The policy is the security boundary.** Any change to `policy.py` or
  `executor.py` must come with tests, including tests for what should be
  *rejected*. See `tests/test_policy.py` for the style.
- Keep `policy.py` free of third-party and network dependencies so it stays
  easy to audit and test in isolation.
- Default to the safe choice: read-only, allowlisted, path-contained. New
  capabilities should be opt-in via a flag, not on by default.
- Run `ruff check .` and `pytest` before opening a PR.

## Reporting bugs

Open an issue with reproduction steps. For security issues, see
[SECURITY.md](SECURITY.md).
