"""Let the running server stop itself (the sidebar's power button).

The server stops the way Ctrl+C stops it: we raise SIGINT in our own
process. uvicorn catches it, finishes in-flight responses, runs the app's
lifespan shutdown, and ``uvicorn.run`` swallows the ``KeyboardInterrupt``
it re-raises afterwards, so ``claude-explorer serve`` exits with code 0.
The launchers notice the process is gone and quit too.

uvicorn only installs its signal handlers on the main thread, and runs the
event loop there. A request served anywhere else (``TestClient`` runs the
app on a worker thread) cannot stop the server this way, so
:func:`request_shutdown` refuses instead of interrupting the test runner.

If a long response (an open Refresh stream) keeps the graceful shutdown
waiting, a second SIGINT forces it after :data:`FORCE_AFTER_SEC`; uvicorn
treats a second Ctrl+C the same way. ``serve`` also caps the wait with
``timeout_graceful_shutdown``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading

log = logging.getLogger(__name__)

#: Seconds before the shutdown begins, so the HTTP response gets out first.
START_DELAY_SEC = 0.25
#: Seconds after the start before a stuck graceful shutdown is forced.
FORCE_AFTER_SEC = 10.0


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _send_signal(sig: int) -> None:
    """The process-signalling boundary (tests replace it)."""
    signal.raise_signal(sig)


def request_shutdown() -> bool:
    """Schedule a graceful exit of this server. Returns False if it can't.

    Must be called from a coroutine running on the server's event loop.
    """
    if not _on_main_thread():
        log.warning("shutdown requested, but this server cannot signal itself")
        return False
    loop = asyncio.get_running_loop()
    loop.call_later(START_DELAY_SEC, _send_signal, signal.SIGINT)
    loop.call_later(START_DELAY_SEC + FORCE_AFTER_SEC, _force_exit)
    log.info("shutdown requested from the web UI; stopping")
    return True


def _force_exit() -> None:
    log.warning("graceful shutdown still waiting after %.0fs; forcing", FORCE_AFTER_SEC)
    _send_signal(signal.SIGINT)
