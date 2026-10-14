"""``POST /api/shutdown`` — the sidebar's power button stops the server.

When the macOS app, the Windows shortcut, or the Linux menu entry starts
the server, no terminal is open to press Ctrl+C in. See UX.md → "Quit
(power button)" and ``backend/shutdown.py``.

The server listens on localhost, but any web page the user visits can send
requests to localhost. Two checks keep other sites from stopping it:

* The request must carry ``X-Claude-Explorer-Action: shutdown``. A custom
  header makes the browser send a CORS preflight first, and the CORS policy
  (``backend/main.py``) admits only the app's own dev origins.
* If the browser sends an ``Origin``, it must be this server or a loopback
  host (the Vite dev server proxies from ``localhost:5173``).

Not gated by corrupt-config safe mode: stopping writes nothing.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request

from .. import shutdown as shutdown_mod
from ..models import ShutdownResponse

router = APIRouter()

ACTION_HEADER_VALUE = "shutdown"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return True  # same-origin GETs and non-browser clients send none
    parts = urlsplit(origin)
    return parts.netloc == host or parts.hostname in _LOOPBACK_HOSTS


@router.post(
    "/shutdown",
    status_code=202,
    response_model=ShutdownResponse,
    summary="Stop this Claude Explorer server",
    responses={
        403: {"description": "Missing action header, or a cross-origin request"},
        503: {"description": "This server process cannot stop itself"},
    },
)
async def shutdown(
    request: Request,
    x_claude_explorer_action: str | None = Header(default=None),
) -> ShutdownResponse:
    if x_claude_explorer_action != ACTION_HEADER_VALUE:
        raise HTTPException(
            status_code=403,
            detail="Missing header X-Claude-Explorer-Action: shutdown.",
        )
    if not _origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Cross-origin shutdown refused.")
    if not shutdown_mod.request_shutdown():
        raise HTTPException(
            status_code=503,
            detail="This server was not started in a way that lets it stop itself.",
        )
    return ShutdownResponse()
