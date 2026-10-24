"""Shared parsing helpers for Claude's on-disk JSON formats.

Houses ``parse_datetime`` (the canonical "always returns a tz-aware
datetime" wrapper) and ``_parse_iso_opt`` (the underlying "returns
None on failure" primitive). ``parse_datetime`` previously lived
(identically) in both ``backend.claude_code_reader`` and
``backend.store``. Those modules now re-export the function under
their original underscore-prefixed name (``_parse_datetime``) so
existing import paths — most notably ``from .store import
_parse_datetime`` used by ``backend.search`` at 8 call sites — keep
working unchanged.

Deliberate behavior preserved from the pre-refactor implementations
(do NOT "fix" without reading the call sites first):

* ``parse_datetime`` returns ``datetime.now(timezone.utc)`` for
  ``None``, empty string, or any parse failure. Downstream consumers
  (search ranking, conversation list rendering, summary cache) rely
  on always receiving a tz-aware ``datetime`` and have no codepath
  for ``None``. The return type is ``datetime``, not
  ``Optional[datetime]``.
* ``_parse_iso_opt`` is the "no-fallback" primitive: returns the
  parsed tz-aware ``datetime`` on success, ``None`` on failure. Use
  this when feeding ``min()`` / ``max()`` aggregations —
  substituting ``parse_datetime``'s ``now(utc)`` fallback into an
  aggregation inflates ``max()`` and bounces a corrupt conversation
  to the top of the recent-list UI (council Hunt #7 finding).
* The exception catch covers ``ValueError`` (raised by
  ``datetime.fromisoformat`` on malformed strings), ``AttributeError``
  (raised when a non-string truthy input — int/list/dict/bool —
  reaches ``.endswith("Z")``), and ``TypeError`` (defensive: covers
  any future call site where the input type assumption holds even
  less). The earlier "only ValueError" stance was bit-for-bit parity
  with the pre-refactor duplicates and was tagged as deferred follow-
  up work; the council coercion-audit (bug-class #1) promoted it to
  HIGH after empirically reproducing the AttributeError path with
  ``parse_datetime(12345)`` and friends, where a single hand-edited
  / partial-write JSON file with ``"created_at": 12345`` 500'd the
  entire sidebar via the loop in ``store.list_conversations``.
* The Z-rewrite (``dt_str[:-1] + "+00:00"``) is retained even though
  Python 3.11+ handles a trailing ``Z`` natively — Python 3.10 does
  not, and the cost is zero. Uses ``endswith`` + slice (NOT
  ``str.replace``) so a stray ``Z`` mid-string isn't mangled.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _parse_iso_opt(dt_str: str | None) -> datetime | None:
    """Parse an ISO-8601-ish timestamp; return ``None`` on failure.

    The "no-fallback" primitive that :func:`parse_datetime` wraps. Use
    this directly when feeding ``min()`` / ``max()`` over a list of
    timestamps so that bad rows can be filtered out rather than
    silently substituting ``now(utc)`` (which would inflate ``max()``
    and bounce a corrupt conversation to the top of the recent list —
    council Hunt #7 finding).

    Accepts the ISO-8601 shapes Claude actually emits:

    * ``"2025-01-15T10:30:00Z"`` — Z-suffixed UTC. The ``"Z"`` is
      rewritten to ``"+00:00"`` before ``datetime.fromisoformat`` is
      called so this works identically on Python 3.10–3.12.
    * ``"2025-01-15T10:30:00+04:00"`` — explicit offset, preserved
      as-is (not normalized to UTC).
    * ``"2025-01-15T10:30:00"`` — naive; ``timezone.utc`` is attached
      so the return value is always tz-aware (prevents mixed
      naive/aware ``TypeError`` in downstream ``min`` / ``max``).

    Returns ``None`` for ``None``, empty string, non-string truthy
    input (int / list / dict / bool), or any unparseable string.
    """
    if not dt_str:
        return None
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, AttributeError, TypeError):
        return None


def parse_datetime(dt_str: str | None) -> datetime:
    """Parse a datetime string from Claude's JSON / JSONL formats.

    Thin wrapper around :func:`_parse_iso_opt` that substitutes
    ``datetime.now(timezone.utc)`` for any parse failure. See the
    module docstring for why the fallback is "now in UTC" rather than
    ``None`` (downstream consumers have no ``None``-handling codepath)
    and for the coercion-audit rationale on the widened exception
    catch.

    Emits a ``logger.debug`` on every fallback firing so corruption
    rates stay observable without inflating the log level for
    correctly-formatted input. Bump to ``warning`` if the silent
    masking becomes a recurring debugging-time-sink.
    """
    result = _parse_iso_opt(dt_str)
    if result is not None:
        return result
    logger.debug("parse_datetime fallback to now(utc) for input %r", dt_str)
    return datetime.now(UTC)
