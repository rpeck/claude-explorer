"""Pin the Cowork compact-marker extraction contract.

Bug observed 2026-05-26: search for "ran out of context" with Show
Compactions OFF returned a Cowork hit on the canonical Claude
compaction-summary text ("This session is being continued from a
previous conversation that ran out of context. The summary below
covers the earlier portion of the conversation."). Investigation:
``backend/cowork_reader.py:158`` hardcoded ``compact_markers: []``,
so the FTS5 ``is_compaction_summary`` column (populated from
``conv['compact_markers']`` in ``upsert_conversation``) was always 0
for Cowork sessions, and the filter ``is_compaction_summary = 0``
let the compaction summary through.

Same bug cascades to the viewer's "Show Compactions" checkbox: the
checkbox gate ``isCC && hasCompactMarkers`` skipped Cowork
sessions entirely, AND Cowork's empty ``compact_markers`` would have
hidden the checkbox even if the gate were widened.

Cowork audit.jsonl does NOT have CC's ``isCompactSummary: true``
field. The detection heuristic is text-prefix matching on the
canonical Claude compaction prompt — a string the Claude runtime
itself injects, not user content.
"""

from __future__ import annotations

import json
from pathlib import Path

CANONICAL_PREFIX = (
    "This session is being continued from a previous conversation that "
    "ran out of context."
)


def _make_cowork_audit(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a synthetic Cowork audit.jsonl + sidecar at the canonical
    layout depth and return the session dir."""
    dep_dir = tmp_path / "deployment-uuid" / "org-uuid" / "local_SESSION"
    dep_dir.mkdir(parents=True)
    audit = dep_dir / "audit.jsonl"
    with audit.open("w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    sidecar = (
        tmp_path / "deployment-uuid" / "org-uuid" / "local_SESSION.json"
    )
    sidecar.write_text(json.dumps({"sessionId": "SESSION", "title": "t"}))
    return dep_dir


def _user_msg(text: str, uuid: str = "u1") -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "session_id": "SESSION",
        "_audit_timestamp": "2026-05-26T10:00:00Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_msg(text: str, uuid: str = "a1") -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "session_id": "SESSION",
        "_audit_timestamp": "2026-05-26T10:00:01Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def test_cowork_reader_extracts_compact_markers_from_canonical_prefix(tmp_path):
    """RED-first: the cowork reader must populate ``compact_markers``
    for user messages whose text content STARTS with the canonical
    Claude compaction prompt. Today it returns []."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = _make_cowork_audit(
        tmp_path,
        [
            _user_msg("Hello, please help me research X.", uuid="u-pre"),
            _assistant_msg("Sure, I will help.", uuid="a-pre"),
            _user_msg(
                CANONICAL_PREFIX
                + " The summary below covers the earlier portion of the "
                + "conversation.\n\nSummary:\n1. ...",
                uuid="u-compact-1",
            ),
            _assistant_msg("Continuing from the summary.", uuid="a-post"),
        ],
    )
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    markers = conv.get("compact_markers", [])
    assert len(markers) == 1, (
        f"expected 1 compact marker; got {len(markers)}: {markers!r}"
    )
    assert markers[0]["message_uuid"] == "u-compact-1"
    # Marker text contract: surface enough text for the UI to render
    # the summary preamble. The full text being present is what lets
    # `_is_compact_summary_message` in search_index.py match the row.
    assert "ran out of context" in markers[0]["summary_text"]


def test_cowork_reader_does_not_false_positive_on_quoted_text(tmp_path):
    """Bidirectional pair: a regular conversation that QUOTES the
    canonical phrase mid-message (e.g. user discussing the compaction
    feature) must NOT be classified as a compaction marker. The
    detection keys on the message TEXT starting with the prefix, not
    on the phrase appearing anywhere."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = _make_cowork_audit(
        tmp_path,
        [
            _user_msg(
                "I want to understand why my session feels like it 'ran out "
                "of context'. This session is being continued — well, "
                "actually it isn't, I'm just curious.",
                uuid="u-meta",
            ),
            _assistant_msg("Here's how the compactor works...", uuid="a-meta"),
        ],
    )
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert conv.get("compact_markers", []) == []


def test_cowork_reader_with_no_compaction_markers_returns_empty_list(tmp_path):
    """Bidirectional pair: a normal Cowork session without any
    compactions still returns an EMPTY compact_markers list (not
    None, not absent — the key must be present so consumers can rely
    on ``conv['compact_markers']`` without ``.get``)."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = _make_cowork_audit(
        tmp_path,
        [
            _user_msg("Plain question.", uuid="u1"),
            _assistant_msg("Plain answer.", uuid="a1"),
        ],
    )
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert "compact_markers" in conv
    assert conv["compact_markers"] == []


def test_search_index_flags_cowork_compaction_row_as_summary(tmp_path, monkeypatch):
    """Integration: a Cowork session with a compaction marker, when
    indexed, has the compaction-summary row tagged
    ``is_compaction_summary=1`` so the
    ``include_compactions=False`` filter drops it.

    This is the regression that surfaced in production on
    2026-05-26: the search filter let Cowork compaction summaries
    leak. The path goes through ``upsert_conversation`` which derives
    the tag from ``conv['compact_markers']``."""
    from backend.cowork_reader import read_cowork_conversation
    from backend.search_index import SearchIndex

    session_dir = _make_cowork_audit(
        tmp_path,
        [
            _user_msg(
                CANONICAL_PREFIX + " Summary follows.",
                uuid="u-compact",
            ),
            _assistant_msg("Picking up from the summary.", uuid="a-post"),
        ],
    )
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert conv["compact_markers"], "fixture must produce a marker for this test"

    idx_path = tmp_path / "search-index.sqlite"
    idx = SearchIndex(idx_path)
    audit = session_dir / "audit.jsonl"
    idx.upsert_conversation(conv, audit, audit.stat().st_mtime)

    # Row-level check: the compaction summary message must be tagged.
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(str(idx_path))) as conn:
        rows = conn.execute(
            "SELECT message_uuid, is_compaction_summary FROM messages "
            "WHERE conv_uuid = ? ORDER BY message_uuid",
            (conv["uuid"],),
        ).fetchall()
    by_uuid = {r[0]: r[1] for r in rows}
    assert by_uuid.get("u-compact") == 1, (
        f"compaction summary row must be tagged 1; got {by_uuid!r}"
    )
    # Bidirectional: the assistant message that FOLLOWS the marker must
    # NOT be tagged — the tag is per-row, not per-conversation.
    assert by_uuid.get("a-post") == 0, (
        f"non-compaction row must be tagged 0; got {by_uuid!r}"
    )
