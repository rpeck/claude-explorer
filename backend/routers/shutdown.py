"""``POST /api/shutdown`` — the sidebar's power button stops the server.

When the macOS app, the Windows shortcut, or the Linux menu entry starts
the server, no terminal is open to press Ctrl+C in. See UX.md → "Quit
(power button)" and ``backend/shutdown.py``.

The server listens on localhost, but any web page the user visits can send
requests to localhost. Three checks keep other sites from stopping it:

* The Host guard (``backend/host_guard.py``) refuses every request whose
  Host is not a loopback name. That stops DNS rebinding, where a site makes
  its own domain resolve to 127.0.0.1 and so becomes same-origin.
* The request must carry ``X-Claude-Explorer-Action: shutdown``. A custom
  header makes a cross-origin request need a CORS preflight, and the server
  answers no CORS at all, so the browser never sends the POST.
* If the browser sends an ``Origin``, its host name must be on the guard's
  allowlist (the Vite dev server proxies from ``localhost:5173``). It is
  never compared with the Host header: a rebinding page sends an Origin
  equal to its own Host.

Not gated by corrupt-config safe mode: stopping writes nothing.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request

from .. import shutdown as shutdown_mod
from ..host_guard import allowed_hosts
from ..models import ShutdownResponse

router = APIRouter()

ACTION_HEADER_VALUE = "shutdown"


def _origin_allowed(origin: str | None, host: str | None) -> bool:  # noqa: ARG001
    """True if the browser's Origin names an allowed host.

    ``host`` is accepted only to keep the call site obvious; it is not used,
    because matching Origin against Host is what DNS rebinding defeats.
    """
    if not origin:
        return True  # non-browser clients send none
    allowed = allowed_hosts()
    if "*" in allowed:
        return True  # serve --host <non-loopback>: the user exposed the server
    try:
        name = urlsplit(origin).hostname
    except ValueError:
        return False
    return name is not None and name in allowed


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
    refusal = shutdown_mod.request_shutdown()
    if refusal is not None:
        raise HTTPException(status_code=503, detail=refusal)
    return ShutdownResponse()
