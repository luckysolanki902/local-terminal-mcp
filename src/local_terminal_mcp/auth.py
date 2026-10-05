"""Bearer-token authentication middleware for the HTTP transport.

Implemented as pure ASGI middleware (rather than Starlette's
``BaseHTTPMiddleware``) so it does not interfere with the streaming responses
the MCP Streamable-HTTP transport relies on.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Path that bypasses auth so tunnels/uptime checks can probe liveness.
HEALTH_PATH = "/healthz"


class BearerAuthMiddleware:
    """Reject HTTP requests lacking a valid ``Authorization: Bearer`` header."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        if scope.get("path") == HEALTH_PATH:
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")

        # Constant-time comparison avoids leaking the token via timing.
        if not hmac.compare_digest(provided, self._expected):
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


async def health_endpoint(request: object) -> PlainTextResponse:  # noqa: ARG001
    return PlainTextResponse("ok")


HealthHandler = Callable[[object], Awaitable[PlainTextResponse]]
