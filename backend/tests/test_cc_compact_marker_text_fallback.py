"""Pin the CC compact-marker text-prefix fallback contract.

Bug observed live 2026-05-26: a Claude Code session from Aug 2025
(conv ``da9eec3e-20f1-4007-829a-1af242265ebd``) has the canonical
Claude compaction-prompt text as its first user message body, but
the older CC binary that wrote that session did NOT stamp
``isCompactSummary: true`` on the entry. Result:

* ``extract_compact_markers`` returns []
* conv's ``compact_markers`` field is empty
* frontend's ``hasCompactMarkers`` is False
* the 'Show Compactions' checkbox does not render

Same problem Cowork hit (commit `96e6b2d`), same fix shape: add a
text-prefix fallback for the canonical Claude compaction prompt.
``isCompactSummary: true`` still wins when present (newer CC
sessions); the text-prefix is a defense for legacy entries.

This file pins the contract using the shared
``is_compaction_prefix_text`` helper from ``backend.compact_prefixes``
(introduced in commit `fdbbe4c`), so the CC detector and the
Cowork detector and the FTS title-leak gate ALL agree on what
counts as a compaction.
"""

from __future__ import annotations



CANONICAL_PREFIX = (
    "This session is being continued from a previous conversation that "
    "ran out of context."
)


def _user_entry(text: str, uuid: str = "u1", flagged: bool = False) -> dict:
    """Build a CC JSONL entry shape. ``flagged=True`` sets
    ``isCompactSummary: true`` (the canonical CC marker)."""
    entry = {
        "type": "user",
        "uuid": uuid,
        "sessionId": "test-session",
        "timestamp": "2026-05-26T10:00:00Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }
    if flagged:
        entry["isCompactSummary"] = True
    return entry


def test_extract_compact_markers_text_prefix_fallback_no_flag(monkeypatch):
    """RED-first: an entry whose text starts with the canonical Claude
    compaction prompt but does NOT have ``isCompactSummary: true``
    MUST still be detected. This is the older-CC-binary case observed
    live."""
    from backend.cc_image_markers import extract_compact_markers

    entries = [
        _user_entry("Earlier turn", uuid="u-pre", flagged=False),
        _user_entry(
            CANONICAL_PREFIX + " The conversation is summarized below.\nSummary:\n...",
            uuid="u-compact",
            flagged=False,  # OLD CC: no flag
        ),
        _user_entry("Continuing from the summary", uuid="u-post", flagged=False),
    ]
    markers = extract_compact_markers(entries)
    assert len(markers) == 1, (
        f"text-prefix fallback must detect the compact-summary message; "
        f"got {len(markers)}: {markers!r}"
    )
    assert markers[0]["message_uuid"] == "u-compact"


def test_extract_compact_markers_flag_still_wins_when_present():
    """Bidirectional: when ``isCompactSummary: true`` IS present, the
    existing extractor path still works (no regression). Same shape,
    same result."""
    from backend.cc_image_markers import extract_compact_markers

    entries = [
        _user_entry(
            "Some user prompt — the flag is what matters, not the text.",
            uuid="u-flagged",
            flagged=True,
        ),
    ]
    markers = extract_compact_markers(entries)
    assert len(markers) == 1
    assert markers[0]["message_uuid"] == "u-flagged"


def test_extract_compact_markers_no_false_positive_on_quoted_text():
    """Bidirectional pair (anchored prefix): a user message that
    discusses the compaction feature mid-text must NOT be tagged.
    The detector keys on ``.startswith`` (after lstrip), not a
    substring match."""
    from backend.cc_image_markers import extract_compact_markers

    entries = [
        _user_entry(
            "I want to understand why my session feels like it 'ran out "
            "of context'. This session is being continued — actually it "
            "isn't, I'm just curious.",
            uuid="u-meta",
            flagged=False,
        ),
    ]
    markers = extract_compact_markers(entries)
    assert markers == []


def test_extract_compact_markers_flag_and_prefix_no_double_count():
    """An entry with BOTH the flag and the canonical prefix must
    produce exactly ONE marker, not two. Prevents a double-tagging
    bug when newer CC stamps the flag AND emits the canonical text."""
    from backend.cc_image_markers import extract_compact_markers

    entries = [
        _user_entry(
            CANONICAL_PREFIX + " summary follows",
            uuid="u-both",
            flagged=True,
        ),
    ]
    markers = extract_compact_markers(entries)
    assert len(markers) == 1, (
        f"flag + prefix must produce one marker, not duplicates; "
        f"got {len(markers)}: {markers!r}"
    )
    assert markers[0]["message_uuid"] == "u-both"


def test_extract_compact_markers_skipped_for_non_user_role():
    """Bidirectional defense: an ASSISTANT entry whose text starts
    with the canonical prefix (would be impossible in practice, but
    pin the contract) does NOT count as a compact marker. The
    runtime only ever injects the compaction prompt as a user-role
    synthesized message."""
    from backend.cc_image_markers import extract_compact_markers

    entries = [
        {
            "type": "assistant",
            "uuid": "a-rare",
            "sessionId": "test-session",
            "timestamp": "2026-05-26T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": CANONICAL_PREFIX + " ..."}],
            },
        },
    ]
    markers = extract_compact_markers(entries)
    assert markers == []
