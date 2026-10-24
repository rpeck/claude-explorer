"""Hunt #7 regression: corrupt timestamps must not bounce ``updated_at`` to now.

``backend/cc_message_transforms._extract_conversation_metadata`` and
``backend/cc_agent_reader._extract_agent_metadata`` both build
``all_timestamps`` lists from per-entry ``timestamp`` fields and take
``min()`` / ``max()`` to derive a conversation's ``created_at`` and
``updated_at``.

Pre-Hunt-#7, these sites parsed inline with
``datetime.fromisoformat(ts.replace("Z", "+00:00"))`` wrapped in a bare
``except (ValueError, TypeError): pass``. A naive refactor to the
canonical ``parse_datetime`` helper would have introduced a UX
regression: ``parse_datetime`` substitutes ``datetime.now(timezone.utc)``
on failure, which is strictly greater than every legitimate historical
timestamp, so ``max()`` would yield ``now`` and the conversation would
bounce to the top of the sidebar's recent list.

The fix uses ``_parse_iso_opt`` (the ``None``-on-failure primitive)
and filters ``None`` out of the aggregation. These tests pin that
contract on both call sites by feeding in a mix of well-formed and
corrupt timestamps and asserting the aggregates ignore the bad rows
rather than substituting ``now``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.cc_agent_reader import _extract_agent_metadata
from backend.cc_message_transforms import _extract_conversation_metadata

_OLD_TIMESTAMP_STR = "2024-06-01T12:00:00Z"
_OLD_TIMESTAMP = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_NEWER_TIMESTAMP_STR = "2024-06-02T13:00:00Z"
_NEWER_TIMESTAMP = datetime(2024, 6, 2, 13, 0, 0, tzinfo=UTC)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _close_to_now(dt: datetime, tolerance_seconds: int = 30) -> bool:
    return abs(_now_utc() - dt) <= timedelta(seconds=tolerance_seconds)


# ---------------------------------------------------------------------------
# _extract_conversation_metadata (cc_message_transforms.py)
# ---------------------------------------------------------------------------


def test_conversation_metadata_drops_corrupt_timestamps_from_max():
    """A corrupt ``timestamp`` field must NOT inflate ``updated_at`` to now.

    Entries: one well-formed + one with an int timestamp (would have
    crashed inline ``.replace("Z", ...)`` pre-fix; would have inflated
    ``max()`` to ``now`` if naively routed through ``parse_datetime``).
    """
    entries = [
        {"type": "user", "timestamp": _OLD_TIMESTAMP_STR, "sessionId": "s1"},
        {"type": "user", "timestamp": 12345, "sessionId": "s1"},  # corrupt
        {"type": "user", "timestamp": _NEWER_TIMESTAMP_STR, "sessionId": "s1"},
    ]
    meta = _extract_conversation_metadata(entries, Path("/tmp/fake.jsonl"))
    assert meta["created_at"] == _OLD_TIMESTAMP
    # KEY ASSERTION: updated_at is the newest VALID timestamp,
    # NOT now(utc) substituted in for the corrupt int row.
    assert meta["updated_at"] == _NEWER_TIMESTAMP
    assert not _close_to_now(meta["updated_at"])


def test_conversation_metadata_drops_garbage_string_timestamps_from_max():
    """Malformed string ``timestamp`` rows are dropped, not now-substituted."""
    entries = [
        {"type": "user", "timestamp": _OLD_TIMESTAMP_STR, "sessionId": "s1"},
        {"type": "user", "timestamp": "not a date", "sessionId": "s1"},
        {"type": "user", "timestamp": _NEWER_TIMESTAMP_STR, "sessionId": "s1"},
    ]
    meta = _extract_conversation_metadata(entries, Path("/tmp/fake.jsonl"))
    assert meta["updated_at"] == _NEWER_TIMESTAMP
    assert not _close_to_now(meta["updated_at"])


def test_conversation_metadata_all_corrupt_falls_back_to_now():
    """If EVERY timestamp is corrupt, ``now(utc)`` is the documented fallback.

    Pins the documented "no valid timestamps → now" behavior at the
    aggregation level. Only the all-bad case falls back, never the
    mixed case.
    """
    entries = [
        {"type": "user", "timestamp": 12345, "sessionId": "s1"},
        {"type": "user", "timestamp": "garbage", "sessionId": "s1"},
        {"type": "user", "timestamp": ["not", "a", "string"], "sessionId": "s1"},
    ]
    meta = _extract_conversation_metadata(entries, Path("/tmp/fake.jsonl"))
    assert _close_to_now(meta["updated_at"])
    assert _close_to_now(meta["created_at"])


def test_conversation_metadata_no_timestamps_falls_back_to_now():
    """Entries with no ``timestamp`` field at all → now(utc) fallback."""
    entries = [
        {"type": "user", "sessionId": "s1"},
        {"type": "user", "sessionId": "s1"},
    ]
    meta = _extract_conversation_metadata(entries, Path("/tmp/fake.jsonl"))
    assert _close_to_now(meta["updated_at"])
    assert _close_to_now(meta["created_at"])


# ---------------------------------------------------------------------------
# _extract_agent_metadata (cc_agent_reader.py)
# ---------------------------------------------------------------------------


def test_agent_metadata_drops_corrupt_timestamps_from_max():
    """Mirror of the conversation test; same regression class on agent rows."""
    entries = [
        {"type": "user", "timestamp": _OLD_TIMESTAMP_STR, "agentId": "a1"},
        {"type": "user", "timestamp": {"bad": "shape"}, "agentId": "a1"},
        {"type": "user", "timestamp": _NEWER_TIMESTAMP_STR, "agentId": "a1"},
    ]
    meta = _extract_agent_metadata(entries, Path("/tmp/agent-a1.jsonl"))
    assert meta["created_at"] == _OLD_TIMESTAMP
    assert meta["updated_at"] == _NEWER_TIMESTAMP
    assert not _close_to_now(meta["updated_at"])


def test_agent_metadata_all_corrupt_falls_back_to_now():
    """Mirror: agent rows with all-corrupt timestamps fall back to now."""
    entries = [
        {"type": "user", "timestamp": 12345, "agentId": "a1"},
        {"type": "user", "timestamp": "garbage", "agentId": "a1"},
    ]
    meta = _extract_agent_metadata(entries, Path("/tmp/agent-a1.jsonl"))
    assert _close_to_now(meta["updated_at"])
    assert _close_to_now(meta["created_at"])
