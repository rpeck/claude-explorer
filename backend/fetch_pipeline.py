"""Fetch pipeline helpers — SSE encoding and retry-event draining.

Extracted from ``backend/routers/fetch.py`` (Council A2, 2026-05-21).

This module hosts the small, pure helpers used by the fetch router's
SSE generators. The router itself (and the bigger ``_fetch_phase_stream``,
``_capture_phase_stream``, ``refresh_pipeline_stream`` generators that
make heavy use of test-patched names like ``ClaudeFetcher``,
``load_credentials``, ``capture_credentials``, ``DEFAULT_*``) intentionally
stays in ``backend.routers.fetch`` so ``monkeypatch.setattr`` semantics
keep working — see the 60+ patches at ``backend.routers.fetch.<name>``
under ``backend/tests/``.

Only patch-safe helpers (no module-level test-patched name references
inside their bodies) live here:

  * ``_send_event``        — pure ``json.dumps`` wrapper
  * ``_is_session_expired_error`` — pure string check
  * ``_drain_retry_events``       — takes the fetcher as a parameter

If you find yourself wanting to add a helper that references
``ClaudeFetcher``, ``load_credentials``, ``capture_credentials``, or any
of the ``DEFAULT_*`` paths at the module level, KEEP IT in the router —
or first refactor the test suite away from the router-path patch idiom.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import: ClaudeFetcher is referenced only as a parameter
    # annotation on _drain_retry_events. Importing it under TYPE_CHECKING
    # avoids a runtime dependency from this leaf module on fetcher/.
    from fetcher.bulk_fetch import ClaudeFetcher


def _send_event(data: dict) -> str:
    """Encode a dict as a single Server-Sent Events ``data:`` frame."""
    return f"data: {json.dumps(data)}\n\n"


def _is_session_expired_error(error_msg: str) -> bool:
    """True if the upstream error indicates expired creds or Cloudflare block."""
    if not error_msg:
        return False
    lowered = error_msg.lower()
    return "401" in error_msg or "403" in error_msg or "cf-mitigated" in lowered


def _drain_retry_events(fetcher: ClaudeFetcher) -> list[str]:
    """Drain ``fetcher.retry_events``, returning SSE-encoded ``progress`` events.

    The list is emptied in place. The caller forwards each frame to the
    client. We use this post-call rather than real-time streaming via
    asyncio.Queue + call_soon_threadsafe because the maximum delay between
    a retry happening and the user seeing the message is bounded by the
    eventual call's completion (sub-2s for the configured backoff).

    See WWCMM in ClaudeFetcher: if user UX feedback indicates the silence
    during retry is jarring, upgrade to a real-time queue.
    """
    frames: list[str] = []
    if not fetcher.retry_events:
        return frames
    for event in fetcher.retry_events:
        frames.append(
            _send_event(
                {
                    "type": "progress",
                    "message": event.get("message", "Network hiccup; retrying..."),
                    "current": 0,
                    "total": 0,
                }
            )
        )
    fetcher.retry_events.clear()
    return frames


__all__ = [
    "_drain_retry_events",
    "_is_session_expired_error",
    "_send_event",
]
