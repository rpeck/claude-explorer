"""Workstream A — FTS5 snippet() + structured fragments.

PLANS/PERFORMANCE_PHASE_2.md §Workstream A.

The current ``_search_via_index`` walks ``get_all_conversations_raw()``
even after FTS5 narrowed the match set. On a cold cache that walk is
~15 s on a 991-conv / 1.5 GB corpus; on a warm cache it's still 300 ms+
of dict iteration. The plan replaces this with FTS5's built-in
``snippet()`` function for ``context_size="snippet"`` requests.

Wire-format change: a new ``fragments`` field on ``MessageSnippet``
lets the frontend render highlights without parsing inline HTML and
without a new sanitizer dependency. The shape is
``list[SnippetFragment]`` where each fragment is ``{text, mark}``.
Concatenating ``f.text for f in fragments`` reconstructs the rendered
snippet text.

Contract pinned by these tests:
  1. ``MessageSnippet`` has an optional ``fragments`` field.
  2. ``SnippetFragment`` is ``{text: str, mark: bool}``.
  3. After ``context_size="snippet"`` queries hit the fast path,
     fragments are populated AND ``"".join(f.text for f in fragments)``
     equals the rendered snippet text.
  4. At least one fragment has ``mark=True`` (we matched something).
  5. The matched token (case-insensitively) is in the marked fragment.
  6. Equivalence with linear scan: same set of matched
     ``conversation_uuid``s for unambiguous whole-word queries.
     Snippet TEXT may differ (FTS5 picks a bm25-ranked window;
     linear picks first-match), so we DON'T pin char-for-char
     equality on the snippet string itself.
  7. ``context_size="full"`` requests STILL use the old scatter-
     gather path; ``fragments`` is None there.
  8. Backward compat: ``snippet`` / ``match_start`` / ``match_end``
     stay populated even when ``fragments`` is set, so clients that
     don't consume the new field keep working.

Bidirectional verification per TESTING.md §2:
  These tests FAIL today because:
    * MessageSnippet has no ``fragments`` field (Pydantic strict).
    * Even if we added the field as None default, the FTS5 path
      doesn't populate it.
"""

from __future__ import annotations

import pytest

from backend import search_index as si
from backend.cache import clear_cache
from backend.models import MessageSnippet
from backend.search import (
    _search_via_linear_scan,
    search_conversations,
)
from backend.store import ConversationStore
from backend.tests import builders as B


# ----- fixture ---------------------------------------------------------
# Conversation builders moved to ``backend.tests.builders`` (C4 in
# PLANS/2026.05.18-test-hardening.md). The previous inline helpers
# were byte-equivalent to ``B.build_desktop_conv`` /
# ``B.write_desktop_conv``.


@pytest.fixture
def fixture_store(tmp_path, monkeypatch):
    """5-conversation synthetic store with predictable matches.

    Layout:
      * conv-py: body contains "the pythonic prose I love"
      * conv-py2: body contains "running a python script today"
      * conv-budget: title 'budget review', body 'spend less'
      * conv-misc: name 'Unrelated'; body has no needles
      * conv-long: body has 5+ paragraphs so FTS5's snippet()
        actually has a window to choose (without this, snippet()
        returns the entire body and we can't tell the window-
        selection logic works).
    """
    by_org = tmp_path / "by-org" / "org-1"
    long_body = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5
        + "The python interpreter is fast and ergonomic for prototyping. "
        + "Lorem ipsum more padding text " * 10
    )
    convs = [
        B.build_desktop_conv(uuid="conv-py", name="Notebook A", body="the pythonic prose I love"),
        B.build_desktop_conv(uuid="conv-py2", name="Notebook B", body="running a python script today"),
        B.build_desktop_conv(uuid="conv-budget", name="budget review", body="spend less this quarter"),
        B.build_desktop_conv(uuid="conv-misc", name="Unrelated title", body="nothing of interest"),
        B.build_desktop_conv(uuid="conv-long", name="Long content sample", body=long_body),
    ]
    for c in convs:
        B.write_desktop_conv(by_org, c)

    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    clear_cache()
    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    monkeypatch.setattr(si, "_search_index", idx)

    yield store, idx

    idx.close()
    si.reset_search_index_for_tests()
    clear_cache()


# ----- model surface ---------------------------------------------------


def test_message_snippet_has_optional_fragments_field():
    """MessageSnippet must accept ``fragments`` (None or a list of
    SnippetFragment-shaped dicts).

    Pinning the wire-format addition: clients that don't ship the
    field still validate cleanly (None default); clients that do
    ship it get parsed into the structured shape.
    """
    # Default — no fragments.
    s_no_frag = MessageSnippet(
        message_uuid="m1", sender="human", snippet="hi", match_start=0, match_end=0,
    )
    assert s_no_frag.fragments is None

    # With fragments.
    s_with_frag = MessageSnippet(
        message_uuid="m1", sender="human", snippet="foo bar baz",
        match_start=4, match_end=7,
        fragments=[
            {"text": "foo ", "mark": False},
            {"text": "bar", "mark": True},
            {"text": " baz", "mark": False},
        ],
    )
    assert s_with_frag.fragments is not None
    assert len(s_with_frag.fragments) == 3
    assert s_with_frag.fragments[0].text == "foo "
    assert s_with_frag.fragments[0].mark is False
    assert s_with_frag.fragments[1].text == "bar"
    assert s_with_frag.fragments[1].mark is True


def test_snippet_fragment_concatenation_reconstructs_snippet():
    """``"".join(f.text for f in fragments)`` must equal the rendered
    snippet that the legacy ``snippet`` field carries.

    Frontend invariant: rendering the fragments yields the SAME text
    the legacy ``HighlightedSnippet`` component would have rendered.
    """
    s = MessageSnippet(
        message_uuid="m1",
        sender="human",
        snippet="alpha beta gamma",
        match_start=6, match_end=10,
        fragments=[
            {"text": "alpha ", "mark": False},
            {"text": "beta", "mark": True},
            {"text": " gamma", "mark": False},
        ],
    )
    reconstructed = "".join(f.text for f in s.fragments)
    assert reconstructed == s.snippet


# ----- fast-path produces fragments ------------------------------------


def test_fast_path_populates_fragments_for_snippet_mode(fixture_store):
    """``context_size="snippet"`` queries via search_conversations()
    return ``MessageSnippet`` objects with ``fragments`` populated.

    Each result's matching_messages MUST have at least one
    fragment with ``mark=True`` (otherwise we silently dropped
    the highlight signal).
    """
    store, idx = fixture_store
    assert idx.is_ready()

    results = search_conversations(store, "python", context_size="snippet").results
    assert len(results) >= 2, (
        f"expected >=2 hits for 'python' (conv-py, conv-py2); got "
        f"{[r.conversation_uuid for r in results]}"
    )

    for r in results:
        for ms in r.matching_messages:
            # Title pseudo-message rows may or may not have fragments;
            # body matches MUST.
            if ms.sender == "title":
                continue
            assert ms.fragments is not None, (
                f"conv={r.conversation_uuid} msg={ms.message_uuid}: "
                f"fragments missing on snippet-mode result"
            )
            assert any(f.mark for f in ms.fragments), (
                f"conv={r.conversation_uuid} msg={ms.message_uuid}: "
                f"fragments present but no marked fragment"
            )
            # The marked fragments' text concatenated should contain
            # 'python' (case-insensitive) — that's what we searched for.
            marked = "".join(f.text for f in ms.fragments if f.mark).lower()
            assert "python" in marked, (
                f"conv={r.conversation_uuid}: 'python' not in marked "
                f"fragment text {marked!r}"
            )


def test_fast_path_fragment_concatenation_matches_snippet(fixture_store):
    """For every body-match result, the fragments concat to the legacy
    ``snippet`` field. Backward-compat invariant: clients that consume
    ``snippet`` (HTML-escaped) MUST get the same text the fragments
    consumer renders.
    """
    store, _ = fixture_store
    results = search_conversations(store, "python", context_size="snippet").results

    for r in results:
        for ms in r.matching_messages:
            if ms.sender == "title" or ms.fragments is None:
                continue
            reconstructed = "".join(f.text for f in ms.fragments)
            assert reconstructed == ms.snippet, (
                f"conv={r.conversation_uuid} msg={ms.message_uuid}: "
                f"fragments don't reconstruct snippet.\n"
                f"  snippet:       {ms.snippet!r}\n"
                f"  reconstructed: {reconstructed!r}"
            )


def test_full_mode_does_not_populate_fragments(fixture_store):
    """``context_size="full"`` requests use the old scatter-gather path;
    ``fragments`` MUST be None there. Full mode is the rare branch
    used by the "expand to full message" UX — it tolerates the
    extra Python work and doesn't benefit from FTS5 snippet().
    """
    store, _ = fixture_store
    results = search_conversations(store, "python", context_size="full").results
    assert len(results) >= 2

    for r in results:
        for ms in r.matching_messages:
            assert ms.fragments is None, (
                f"context_size='full' must not populate fragments; got "
                f"{ms.fragments!r} on conv={r.conversation_uuid}"
            )


# ----- equivalence with linear scan (matched UUIDs, not snippet text) -----


def test_fast_path_matches_same_conv_uuids_as_linear(fixture_store):
    """Same set of conversation UUIDs as the linear-scan path for
    unambiguous whole-word queries. Snippet TEXT is allowed to differ
    (FTS5's bm25-window-pick can pick a different excerpt than
    create_snippet's first-match-window).
    """
    store, _ = fixture_store
    for q in ("python", "budget", "spend"):
        via_linear = _search_via_linear_scan(store, q)
        via_fast = search_conversations(store, q, context_size="snippet").results

        linear_uuids = sorted(r.conversation_uuid for r in via_linear)
        fast_uuids = sorted(r.conversation_uuid for r in via_fast)
        assert linear_uuids == fast_uuids, (
            f"divergent conv set for q={q!r}.\n"
            f"  linear: {linear_uuids}\n"
            f"  fast:   {fast_uuids}"
        )


def test_fast_path_does_not_walk_corpus_for_snippet_mode(fixture_store, monkeypatch):
    """The fast path MUST NOT call ``store.get_all_conversations_raw``.

    This is THE refactor: the whole point of Workstream A is to drop
    the corpus walk. If a future change re-introduces it for
    snippet mode, this test catches it.

    We patch the method on the store instance with a sentinel that
    raises; the snippet-mode query must succeed without invoking it.
    """
    store, _ = fixture_store

    def _boom(*args, **kwargs):
        raise AssertionError(
            "fast-path snippet mode called get_all_conversations_raw; "
            "Workstream A refactor regressed"
        )

    monkeypatch.setattr(store, "get_all_conversations_raw", _boom)

    # Should succeed without triggering the corpus walk.
    results = search_conversations(store, "python", context_size="snippet").results
    assert len(results) >= 2


def test_fast_path_still_populates_legacy_match_positions(fixture_store):
    """Backward compat: ``snippet``, ``match_start``, ``match_end`` are
    still populated on the new path so frontends that haven't
    switched to fragments keep working.

    ``match_start < match_end`` AND the slice
    ``snippet[match_start:match_end]`` (case-insensitively) contains
    a query token.
    """
    store, _ = fixture_store
    results = search_conversations(store, "python", context_size="snippet").results

    for r in results:
        for ms in r.matching_messages:
            if ms.sender == "title":
                continue
            # snippet must be non-empty
            assert ms.snippet, (
                f"empty snippet on conv={r.conversation_uuid} "
                f"msg={ms.message_uuid}"
            )
            # match positions in bounds (>= 0, <= len)
            assert 0 <= ms.match_start <= len(ms.snippet)
            assert ms.match_start <= ms.match_end <= len(ms.snippet)
            # Highlighted slice MUST contain a token (case-insensitive).
            highlighted = ms.snippet[ms.match_start:ms.match_end].lower()
            assert "python" in highlighted, (
                f"highlighted slice {highlighted!r} doesn't contain query "
                f"token on conv={r.conversation_uuid}"
            )


# ----- title sweep still works on fast path ---------------------------


def test_fast_path_title_only_match_still_surfaces(fixture_store):
    """A title-only hit (no body match) still surfaces via the fast
    path. The conv-budget fixture has 'budget' in the title and
    'spend less this quarter' in the body — query 'budget' must
    return conv-budget.

    Negative-space: if the fast path drops the title sweep, this
    fails because 'budget' won't be in the body of conv-budget at
    all (only in its name).
    """
    store, _ = fixture_store

    results = search_conversations(store, "budget", context_size="snippet").results
    uuids = {r.conversation_uuid for r in results}
    assert "conv-budget" in uuids, (
        f"title-only hit dropped on fast path; got {uuids}"
    )
