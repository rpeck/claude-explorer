"""Functional pin for the v3 SCHEMA_VERSION thinking-purge contract.

Context (V1 polish 2026-05-13): :func:`backend.search._extract_searchable_text`
stopped indexing ``thinking`` content blocks because the frontend has no
``case 'thinking':`` renderer in V1, so indexed thinking content produced
search "ghosts" — hits whose bubble shows nothing matching. The
companion :data:`backend.search_index.SCHEMA_VERSION` was bumped 2 → 3
to force a one-time drop+rebuild on next process startup so stale
thinking-only matches don't poison FTS5 top-N ranking.

What was already pinned (`test_search_index.py`):
  * ``test_schema_version_mismatch_triggers_full_rebuild`` — the
    *abstract* upgrade contract: bumping ``SCHEMA_VERSION`` wipes all
    rows on next open.

What was MISSING and is pinned here (Bug 2, council-found 2026-05-14):
  * The *functional* contract: against the CURRENT v3 code, a token
    that lives only inside a ``thinking`` block does NOT appear in the
    FTS5 index, while a token that lives in a normal ``text`` block
    DOES. Without this test, a future refactor could re-introduce
    thinking indexing (and remember to bump SCHEMA_VERSION) and the
    schema-rebuild test would still pass even though the functional
    contract regressed.

Test strategy — "no v2 seeding" (DS/ML + Python Expert agreed in
council 2026-05-14):
  * Reason: the upgrade-path mechanism is orthogonal to the projection
    logic, and is already covered by the schema-version test. The gap
    is whether the v3 :func:`_extract_searchable_text` itself respects
    the purge.
  * Approach: build a fresh v3 index against a fixture that
    deliberately keeps the thinking-only token OUT of
    ``message["text"]`` (which is always indexed regardless of block
    type), and only inside a ``{"type": "thinking", "text": "..."}``
    content block. Query both tokens; assert thinking returns 0 hits
    and text returns ≥1 hit with the expected ``message_uuid`` (the
    ``message_uuid`` assertion is diagnostic — if the fixture grows
    later, a stray hit elsewhere wouldn't silently pass the count
    assertion).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import search_index as si

# Two unique, FTS5-friendly tokens. Single words, no punctuation, no
# accidental subword overlap with anything else in the index.
_TEXT_ONLY_TOKEN = "alphafoxtrot"  # MUST live only in a `text` block
_THINKING_ONLY_TOKEN = "bravowhiskey"  # MUST live only in a `thinking` block


def _conv_with_thinking_and_text(
    conv_uuid: str = "conv-thinking-purge",
    msg_uuid: str = "msg-thinking-purge",
) -> dict:
    """Build a minimal conversation whose single message has:

      * ``message["text"]``: empty (so we know the positive hit comes
        from the ``text`` content block, not the flattened plain-text
        field that ``_extract_searchable_text`` always indexes).
      * a ``{"type": "text", "text": "alphafoxtrot"}`` block (indexed).
      * a ``{"type": "thinking", "text": "bravowhiskey"}`` block
        (NOT indexed under v3).

    GPT-5.2 (LLM council 2026-05-14) specifically flagged that putting
    the thinking token in ``message["text"]`` would make the test fail
    for the wrong reason (since ``_extract_searchable_text`` always
    indexes ``message["text"]``). This fixture is constructed to
    eliminate that confound.
    """
    return {
        "uuid": conv_uuid,
        "name": "Thinking purge fixture",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-14T12:00:00Z",
        "updated_at": "2026-05-14T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": msg_uuid,
        "project_path": None,
        "source": "CLAUDE_AI",
        "chat_messages": [
            {
                "uuid": msg_uuid,
                "sender": "assistant",
                "text": "",  # deliberately empty — see fixture docstring
                "content": [
                    {"type": "text", "text": _TEXT_ONLY_TOKEN},
                    {"type": "thinking", "text": _THINKING_ONLY_TOKEN},
                ],
                "created_at": "2026-05-14T12:00:00Z",
                "updated_at": "2026-05-14T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }


@pytest.fixture
def fresh_index(tmp_path):
    """A fresh v3 ``SearchIndex`` pointed at a per-test sqlite file."""
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    yield idx
    idx.close()


def test_v3_index_does_not_return_thinking_only_token(fresh_index):
    """V1 polish (Bug 2 council fix, 2026-05-14): a token that lives only
    inside a ``thinking`` content block MUST NOT be retrievable via the
    v3 FTS5 index.

    Bug it would surface: re-introducing the
    ``elif btype == "thinking": parts.append(block.get("text"))`` branch
    in :func:`backend.search._extract_searchable_text` without bumping
    ``SCHEMA_VERSION`` and rebuilding. The
    ``test_schema_version_mismatch_triggers_full_rebuild`` companion
    test would still pass (it pins the abstract mechanism, not the
    projection), so without this functional test the regression would
    ship silently.
    """
    conv = _conv_with_thinking_and_text()
    fresh_index.upsert_conversation(conv, Path("/fake/thinking-purge.json"), 1.0)
    fresh_index.mark_ready()

    # Negative: thinking-only token must return 0 hits.
    hits = fresh_index.query(_THINKING_ONLY_TOKEN)
    assert hits == [], (
        f"v3 FTS5 index must NOT return any rows for a token that lives "
        f"only inside a `thinking` content block. The fixture put "
        f"{_THINKING_ONLY_TOKEN!r} only in a thinking block; if this hit "
        f"is non-empty, _extract_searchable_text is indexing thinking "
        f"content (regression of the V1 polish 2026-05-13 purge). Got: "
        f"{hits!r}"
    )


def test_v3_index_still_returns_text_only_token(fresh_index):
    """Bidirectional pair to the negative test above: the v3 index MUST
    still return hits for normal ``text`` content blocks.

    Without this, the negative test could pass trivially because the
    index is empty / dead, not because the thinking purge works.

    Asserts ``message_uuid`` so a future fixture growth can't make the
    positive count assertion pass via an unrelated hit (Python Expert
    diagnostic-quality recommendation, 2026-05-14).
    """
    conv = _conv_with_thinking_and_text()
    fresh_index.upsert_conversation(conv, Path("/fake/thinking-purge.json"), 1.0)
    fresh_index.mark_ready()

    hits = fresh_index.query(_TEXT_ONLY_TOKEN)
    assert len(hits) >= 1, (
        f"v3 FTS5 index must still index normal `text` content blocks; "
        f"{_TEXT_ONLY_TOKEN!r} is in the fixture's text block and should "
        f"return at least one hit. Got: {hits!r}"
    )
    # Diagnostic: confirm the hit is on the expected message.
    assert any(h["message_uuid"] == "msg-thinking-purge" for h in hits), (
        f"positive hit must be on the fixture's known message_uuid; "
        f"otherwise the count assertion above could pass via an unrelated "
        f"row. Got hits: {hits!r}"
    )


def test_v3_index_thinking_and_text_tokens_are_independent(fresh_index):
    """Belt-and-suspenders: in a single test, build the index once and
    query both tokens. Pins the joint contract — purge is one-sided,
    not a global "drop everything" bug.

    Bug it would surface: a refactor that accidentally drops ALL content
    blocks (e.g., changes the loop guard so neither text nor thinking
    is indexed). The two single-direction tests above would each pass
    in isolation under different broken impls; this joint check ties
    them together.
    """
    conv = _conv_with_thinking_and_text()
    fresh_index.upsert_conversation(conv, Path("/fake/thinking-purge.json"), 1.0)
    fresh_index.mark_ready()

    thinking_hits = fresh_index.query(_THINKING_ONLY_TOKEN)
    text_hits = fresh_index.query(_TEXT_ONLY_TOKEN)

    assert thinking_hits == [], (
        "thinking-only token must NOT hit (v3 purge contract)"
    )
    assert len(text_hits) >= 1, (
        "text-only token must hit (proves the index is alive — without "
        "this, the thinking-token-absent assertion is vacuous)"
    )
