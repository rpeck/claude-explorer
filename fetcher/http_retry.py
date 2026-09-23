"""HTTP transport error vocabulary for the Claude fetcher.

Extracted from ``fetcher/bulk_fetch.py`` (Council A2-SPLIT, 2026-05-21).

This module owns two cohesive concerns:

  1. **Domain exceptions** the SSE router can switch on without importing
     ``curl_cffi`` types: ``FetchError``, ``FetchAuthError``,
     ``FetchTransientError``, ``FetchTerminalError``.

  2. **Persisted error vocabulary** (``PersistedErrorKind``,
     ``kind_from_http_status``, ``migrate_legacy_error_code``,
     ``extract_http_status_from_message``) — the closed set of strings
     written to ``_index.json`` per org, plus the heuristic that
     converts a raw 5xx error message back to an HTTP status code for
     diagnostics. Read-side: ``backend.routers.fetch`` switches on
     ``error_kind`` instead of string-matching exception types.

The retry layer (`_retry_sleep`, `_jittered_backoff`, `_classify_http_error`,
`with_retry`, `TransientHTTPError`) **deliberately stays in
``fetcher/bulk_fetch.py``** because the test suite patches
``fetcher.bulk_fetch._retry_sleep`` at the import site (see
``fetcher/tests/test_retry.py``) — Python's name-resolution rule is
that ``with_retry`` looks up ``_retry_sleep`` in its DEFINING module's
namespace, so moving ``with_retry`` here would silently turn the
patches into no-ops (jittered backoff still firing, real-time sleeps).
This was caught empirically during Council A2-SPLIT implementation
and the retry layer was kept in place. TESTING.md §5.12 applies.

``fetcher/bulk_fetch.py`` re-exports every public name in this module
verbatim so existing imports keep working (backend imports of
``PersistedErrorKind`` from ``fetcher.bulk_fetch``). DO NOT REMOVE the
re-exports without a coordinated update of all import sites.
"""

from __future__ import annotations

import re
from typing import Literal


# ---------------------------------------------------------------------------
# Transient classification constants.
#
# A first-of-process call to claude.ai through curl_cffi can fail with
# a TLS handshake error (libcurl code 35) because the TLS context has
# not been warmed yet. Cloudflare's edge also occasionally returns 5xx
# during deploys. Both classes of failure recover on the very next call.
# ---------------------------------------------------------------------------

# libcurl numeric codes we treat as transient.
#   7  CURLE_COULDNT_CONNECT
#   28 CURLE_OPERATION_TIMEDOUT
#   35 CURLE_SSL_CONNECT_ERROR  (the cold-start case the user hit)
#   52 CURLE_GOT_NOTHING
#   55 CURLE_SEND_ERROR
#   56 CURLE_RECV_ERROR
#   5  CURLE_COULDNT_RESOLVE_PROXY (DNS / proxy failure during transient outage)
#   6  CURLE_COULDNT_RESOLVE_HOST  (network down during fetch)
TRANSIENT_CURL_CODES: frozenset[int] = frozenset({5, 6, 7, 28, 35, 52, 55, 56})

# HTTP statuses we treat as transient (will retry).
TRANSIENT_HTTP_STATUSES: frozenset[int] = frozenset({502, 503, 504})

# HTTP statuses that mean the credentials are no longer valid. Never
# retry these — surface immediately so the SSE pipeline can launch
# capture once.
AUTH_HTTP_STATUSES: frozenset[int] = frozenset({401, 403})


# ---------------------------------------------------------------------------
# Domain exceptions.
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Base class for all domain errors raised by the fetcher."""


class FetchAuthError(FetchError):
    """Credentials are invalid / blocked. The router should run capture."""


class FetchTransientError(FetchError):
    """Transient transport-layer failure (TLS, connection, 5xx).

    Wraps the underlying exception in `__cause__`. Raised after retry
    attempts have been exhausted, OR raised directly when the caller
    asked us not to retry but the failure was still transient.
    """


class FetchTerminalError(FetchError):
    """Anything else — unrecoverable. The router emits a sticky toast."""


# ---------------------------------------------------------------------------
# Persisted error vocabulary.
#
# Replaces the ad-hoc "HTTP_401" / "HTTP_403" / "HTTP_404" / "TRANSIENT" /
# `type(e).__name__` strings that the router used to compute on the fly
# and persist to `_index.json` per org. The on-disk records now carry
# `(error_kind, http_status)` — a closed domain vocabulary plus the raw
# HTTP status for diagnostics — and the rollup at routers/fetch.py:918
# switches on `error_kind` instead of string-matching.
#
# Frontend `FetchToast.tsx` reads SSE `kind` (AUTH/TRANSIENT/TERMINAL) for
# in-flight error events; it never reads this on-disk field. So the
# persisted vocab can stay diagnostic without a UI migration.
#
# Naming note: we use `PersistedErrorKind` (Literal alias) — NOT an Enum —
# to match the existing precedent in `backend/routers/fetch.py:92`
# (`ErrorKind = Literal["AUTH","TRANSIENT","TERMINAL"]`) and avoid
# JSON-serialization churn (Literal stringly is what `save_index()` writes
# directly, no `.value` indirection needed).
# ---------------------------------------------------------------------------

PersistedErrorKind = Literal[
    "AUTH_EXPIRED",
    "ORG_FORBIDDEN",
    "ORG_NOT_FOUND",
    "TRANSIENT",
    "TERMINAL",
]

PERSISTED_ERROR_KINDS: frozenset[str] = frozenset({
    "AUTH_EXPIRED",
    "ORG_FORBIDDEN",
    "ORG_NOT_FOUND",
    "TRANSIENT",
    "TERMINAL",
})


def kind_from_http_status(status: int | None) -> PersistedErrorKind | None:
    """Map an HTTP status code to its persisted error kind.

    Returns None for statuses that don't have a 1:1 domain mapping
    (5xx, unknown). The caller picks TRANSIENT/TERMINAL for those.
    """
    if status == 401:
        return "AUTH_EXPIRED"
    if status == 403:
        return "ORG_FORBIDDEN"
    if status == 404:
        return "ORG_NOT_FOUND"
    return None


# Cheap heuristic: pull the first 3-digit HTTP code out of an error
# message. Same patterns the legacy `"401" in msg` checks relied on,
# but compiled once and shared.
_HTTP_STATUS_RE = re.compile(r"\b(\d{3})\b")


def extract_http_status_from_message(msg: str) -> int | None:
    """Pull a leading 3-digit HTTP status out of an error message, or None.

    Mirrors the legacy `"401" in str(exc)` / `"403" in str(exc)` checks
    in routers/fetch.py but normalized to an int we can store as
    `http_status` and pass to `kind_from_http_status()`.
    """
    if not msg:
        return None
    m = _HTTP_STATUS_RE.search(msg)
    if not m:
        return None
    try:
        status = int(m.group(1))
    except ValueError:
        return None
    # Only return plausible HTTP status codes (avoid matching "1234"
    # noise — `\b\d{3}\b` already constrains this, but be defensive).
    if 100 <= status <= 599:
        return status
    return None


# Read-time migration for legacy `_index.json` records that still carry
# the old `error_code` field. Used by the rollup helper in
# `backend/routers/fetch.py` so legacy in-memory records that flow
# through the SSE pipeline still bucket correctly.
_LEGACY_ERROR_CODE_MAP: dict[str, tuple[PersistedErrorKind, int | None]] = {
    "HTTP_401": ("AUTH_EXPIRED", 401),
    "HTTP_403": ("ORG_FORBIDDEN", 403),
    "HTTP_404": ("ORG_NOT_FOUND", 404),
    "TRANSIENT": ("TRANSIENT", None),
}


def migrate_legacy_error_code(
    legacy: str | None,
) -> tuple[PersistedErrorKind | None, int | None]:
    """Map a legacy on-disk `error_code` string to (kind, http_status).

    - None / "" -> (None, None).  Nothing to migrate.
    - Known legacy strings ("HTTP_401" etc.) -> the canonical mapping.
    - Anything else (e.g. raw `type(e).__name__` like "RuntimeError")
      defaults to ("TERMINAL", None) — preserves the "unknown =
      terminal" stance baked into `_classify_error()`.
    """
    if not legacy:
        return (None, None)
    return _LEGACY_ERROR_CODE_MAP.get(legacy, ("TERMINAL", None))


# ---------------------------------------------------------------------------
# Retry primitives.
# ---------------------------------------------------------------------------


__all__ = [
    # Domain exceptions
    "FetchError",
    "FetchAuthError",
    "FetchTransientError",
    "FetchTerminalError",
    # Persisted vocab
    "PersistedErrorKind",
    "PERSISTED_ERROR_KINDS",
    "kind_from_http_status",
    "extract_http_status_from_message",
    "migrate_legacy_error_code",
    # Constants
    "TRANSIENT_CURL_CODES",
    "TRANSIENT_HTTP_STATUSES",
    "AUTH_HTTP_STATUSES",
    # Retry-layer-related names live in fetcher.bulk_fetch (see module
    # docstring above for why) and are NOT re-exported here.
]
