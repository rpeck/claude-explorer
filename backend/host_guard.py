"""Answer only requests whose Host header is a loopback name.

Why: Claude Explorer listens on localhost, but a web page on any site can
make its own domain resolve to 127.0.0.1 (DNS rebinding). The browser then
treats the page's requests to this server as same-origin: it sends no CORS
preflight, and the page can read every response, from the conversation list
to the signed-in account, and can call ``POST /api/shutdown``. The page
cannot change the Host header, which still names the attacker's domain. So
this middleware refuses every request whose Host is not a loopback name.

Allowed host names:

* ``localhost``, ``127.0.0.1`` and ``::1``.
* The comma-separated names in ``CLAUDE_EXPLORER_ALLOWED_HOSTS``. The test
  suite adds its test-client names there (root ``conftest.py``).
* ``*`` in that variable turns the guard off. ``claude-explorer serve`` sets
  it when ``--host`` is not a loopback address, because the user then chose
  to expose the server on a network.

Starlette's ``TrustedHostMiddleware`` is not used: it splits the Host header
at the first colon, so it cannot match an IPv6 address such as ``[::1]:8765``.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ENV = "CLAUDE_EXPLORER_ALLOWED_HOSTS"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def host_name(host_header: str) -> str | None:
    """The lower-case host name from a Host header, without port or brackets."""
    try:
        return urlsplit("//" + host_header).hostname
    except ValueError:  # for example an unclosed IPv6 bracket
        return None


def allowed_hosts() -> frozenset[str]:
    """Loopback names plus the names from the environment.

    Read on every call, so a test (or ``serve``) can change the setting
    after this module is imported.
    """
    extra = {h.strip().lower() for h in os.environ.get(ENV, "").split(",") if h.strip()}
    return LOOPBACK_HOSTS | extra


def host_allowed(host_header: str | None) -> bool:
    allowed = allowed_hosts()
    if "*" in allowed:
        return True
    if not host_header:
        return False
    name = host_name(host_header)
    return name is not None and name in allowed


class HostGuardMiddleware:
    """Pure ASGI middleware: refuse a foreign Host before anything else runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"),
            None,
        )
        if host_allowed(host):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)


__all__ = ["ENV", "LOOPBACK_HOSTS", "HostGuardMiddleware", "allowed_hosts", "host_allowed", "host_name"]
