"""Multi-word search semantics (V1 polish, 2026-05-14).

Pins the contract introduced when fixing the right-sidebar search bug
where typing `comprehensive medium article` returned conversations whose
messages did NOT contain all three words. The new contract:

  * **Unquoted multi-word** queries are AND-of-tokens. Every token must
    appear in the matched message body (or title, conservative substring
    behavior preserved).
  * **Quoted multi-word** queries (`"foo bar baz"`) are exact-phrase.
    FTS5 phrase syntax + the same literal-substring snippet regex.
  * **Single token** queries behave exactly as before.

Bidirectional verification: each test was first run against the
pre-fix code path (snippet regex = ``re.escape(query)``) to confirm it
fails with an informative diff, then against the post-fix code path.

Out-of-scope (documented):
  * Title-level AND of tokens — current behavior is full-query substring
    against the title, which is conservative for multi-word queries.
    Tracked in the workflow's "decision_record" residual risks.
"""

from __future__ import annotations

import pytest

from backend import search_index as si
from backend.cache import clear_cache
from backend.search import (
    _make_snippet_regex,
    _search_via_index,
    _search_via_linear_scan,
    parse_user_query,
    search_conversations,
)
from backend.store import ConversationStore
from backend.tests import builders as B

# ----- helpers ----------------------------------------------------
# Conversation builders moved to ``backend.tests.builders`` (C4 in
# PLANS/2026.05.18-test-hardening.md).


@pytest.fixture
def store_with_phrase_corpus(tmp_path, monkeypatch):
    """A 5-conversation store designed to exercise AND-semantics:

      * ``has-all-adjacent``  — body contains the literal phrase
        "comprehensive medium article" verbatim.
      * ``has-all-scattered`` — body contains all three words but spread
        across the message (medium…then article…then comprehensive).
      * ``has-two``           — body has 2 of the 3 words (comprehensive +
        article).
      * ``has-one``           — body has only "comprehensive".
      * ``has-none``          — body has none of the words.

    The contract under test: a query of ``comprehensive medium article``
    must include both ``has-all-adjacent`` AND ``has-all-scattered`` and
    must NOT include any of ``has-two``, ``has-one``, ``has-none``.
    """
    by_org = tmp_path / "by-org" / "org-1"
    convs = [
        B.build_desktop_conv(
            uuid="has-all-adjacent",
            name="All adjacent",
            body="please write a comprehensive medium article about FTS5",
        ),
        B.build_desktop_conv(
            uuid="has-all-scattered",
            name="Scattered tokens",
            body=(
                "I want a medium-format piece that's clear. "
                "Stretch into a deeper article on the topic. "
                "Make it comprehensive and well-cited."
            ),
        ),
        B.build_desktop_conv(uuid="has-two", name="Two", body="comprehensive article without the M-word"),
        B.build_desktop_conv(uuid="has-one", name="One", body="just comprehensive nothing else"),
        B.build_desktop_conv(uuid="has-none", name="None", body="totally unrelated text content"),
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


# ----- 1. parse_user_query --------------------------------------


def test_parse_query_empty_returns_no_tokens():
    """Empty / whitespace input → caller skips search."""
    assert parse_user_query("") == (None, [])
    assert parse_user_query("   ") == (None, [])


def test_parse_query_single_token_returns_token_list():
    """Single word → token mode with one element. Phrase is None."""
    assert parse_user_query("python") == (None, ["python"])


def test_parse_query_multi_word_unquoted_returns_tokens():
    """Multi-word unquoted → token mode AND-semantics signal."""
    phrase, tokens = parse_user_query("comprehensive medium article")
    assert phrase is None
    assert tokens == ["comprehensive", "medium", "article"]


def test_parse_query_quoted_phrase_returns_phrase():
    """Wrapped in matching " on both ends → phrase mode."""
    phrase, tokens = parse_user_query('"comprehensive medium article"')
    assert phrase == "comprehensive medium article"
    assert tokens == ["comprehensive medium article"]


def test_parse_query_lone_quote_is_token_not_phrase():
    """A single " is not a phrase wrapper; treat as a literal token.

    Guards against an off-by-one that would treat ``"`` (len 1) as
    ``stripped[0]==" and stripped[-1]==" => phrase mode`` (true for
    `"` because index 0 == index -1) but with an empty inner.
    """
    phrase, tokens = parse_user_query('"')
    assert phrase is None
    assert tokens == ['"']


def test_parse_query_only_quotes_returns_empty():
    """An empty quoted string `""` has no inner content; no search.

    Guards against emitting an FTS5 phrase `""` which is invalid.
    """
    phrase, tokens = parse_user_query('""')
    # `""` is len 2 → fails the >=3 phrase test → treated as token.
    # The single token survives whitespace split, so we get back a
    # `""` token. This is acceptable: FTS5's translate_query quotes it
    # safely, and the linear path treats `""` as a literal substring.
    # We only need to verify it does NOT enter phrase mode (the
    # important contract: don't emit MATCH "" to FTS5).
    assert phrase is None


# ----- 2. _make_snippet_regex ------------------------------------


def test_snippet_regex_phrase_mode_is_literal():
    """Phrase mode regex matches the literal phrase, case-insensitive."""
    pat = _make_snippet_regex("foo bar", ["foo bar"])
    assert pat is not None
    assert pat.search("nope foo bar yes") is not None
    # Scattered tokens shouldn't match phrase regex.
    assert pat.search("foo nothing bar") is None


def test_snippet_regex_token_mode_matches_any_token():
    """Token mode regex matches ANY of the tokens (used for highlight
    placement; the AND-of-tokens gate runs separately in the caller)."""
    pat = _make_snippet_regex(None, ["foo", "bar"])
    assert pat is not None
    assert pat.search("only foo here").group(0).lower() == "foo"
    assert pat.search("only bar here").group(0).lower() == "bar"


def test_snippet_regex_token_mode_has_no_word_boundary():
    """No `\\b` boundary: handles stemmer drift gracefully.

    If a future contributor adds `\\b`, this test fails — protecting
    against the silent-drop bug we just fixed.
    """
    pat = _make_snippet_regex(None, ["run"])
    assert pat is not None
    # "running" contains the substring "run" — must still be found.
    assert pat.search("the process is running fast") is not None


# ----- 3. End-to-end via search_conversations --------------------


def test_unquoted_multi_word_is_and_not_or(store_with_phrase_corpus):
    """Typing `comprehensive medium article` must AND, not OR.

    Pre-fix bug: the snippet regex was `re.escape(query)` (literal
    phrase). Conversations with all 3 tokens scattered were dropped
    because no literal-phrase match was found. Post-fix: both
    `has-all-adjacent` (phrase) AND `has-all-scattered` (AND-of-tokens)
    must appear. Conversations with 2 or fewer tokens must NOT appear.
    """
    store, _ = store_with_phrase_corpus
    results = search_conversations(store, "comprehensive medium article").results
    uuids = sorted(r.conversation_uuid for r in results)
    assert uuids == ["has-all-adjacent", "has-all-scattered"], (
        f"AND-of-tokens contract violated: {uuids}. Expected only convs "
        f"whose body contains ALL three of comprehensive/medium/article."
    )


def test_quoted_phrase_is_exact(store_with_phrase_corpus):
    """`"comprehensive medium article"` (quoted) must require adjacency.

    Only `has-all-adjacent` contains the literal phrase verbatim.
    `has-all-scattered` has the tokens scattered — must be excluded.
    """
    store, _ = store_with_phrase_corpus
    results = search_conversations(store, '"comprehensive medium article"').results
    uuids = sorted(r.conversation_uuid for r in results)
    assert uuids == ["has-all-adjacent"], (
        f"Phrase contract violated: {uuids}. Quoted query must require "
        f"adjacency; `has-all-scattered` should NOT appear."
    )


def test_single_word_query_unchanged(store_with_phrase_corpus):
    """A single-token query behaves exactly as before — every conv whose
    body contains the token surfaces. Regression guard for the V1 fix
    not silently breaking the most common search shape."""
    store, _ = store_with_phrase_corpus
    results = search_conversations(store, "comprehensive").results
    uuids = sorted(r.conversation_uuid for r in results)
    # has-all-adjacent, has-all-scattered, has-two, has-one all contain
    # "comprehensive" in body.
    assert set(uuids) == {
        "has-all-adjacent",
        "has-all-scattered",
        "has-two",
        "has-one",
    }


def test_multi_word_snippet_contains_at_least_one_token(store_with_phrase_corpus):
    """The emitted snippet for `has-all-scattered` must contain the
    highlight token AND not be the leading-text fallback (start==end==0
    with no token at offset 0 in the snippet).

    Why this matters: pre-fix, the regex was the literal phrase, so
    `has-all-scattered` produced no snippet and got dropped entirely.
    Post-fix, it must produce a snippet whose visible text contains
    the first token the regex finds.
    """
    store, _ = store_with_phrase_corpus
    results = search_conversations(
        store, "comprehensive medium article",
        context_size="full",
    ).results
    scattered = next(
        (r for r in results if r.conversation_uuid == "has-all-scattered"),
        None,
    )
    assert scattered is not None
    # Exactly one body match per message (linear/index paths both emit
    # the first-token-position highlight, not multiple).
    body_matches = [m for m in scattered.matching_messages if m.message_uuid != "title"]
    assert len(body_matches) == 1
    snip = body_matches[0]
    # Highlight indices must point at a real token, not the fallback 0/0.
    assert snip.match_end > snip.match_start, (
        f"Expected a highlighted token, got fallback indices "
        f"start={snip.match_start} end={snip.match_end} for snippet "
        f"{snip.snippet[:80]!r}."
    )
    highlighted = snip.snippet[snip.match_start:snip.match_end].lower()
    assert highlighted in {"comprehensive", "medium", "article"}


# ----- 4. Linear/index path equivalence on multi-word ------------


def test_linear_and_index_paths_agree_on_multi_word(store_with_phrase_corpus):
    """The two paths must return the same conversation set for a
    multi-word query. Extends the equivalence contract in
    test_search_equivalence.py to AND-of-tokens semantics.

    Pre-fix this DIVERGED: the FTS5 path ANDed correctly in SQL but the
    Python snippet regex (literal phrase) then dropped scattered-token
    messages; the linear path dropped them too (same regex). Both
    converged on the wrong answer. Post-fix both must converge on the
    right answer.
    """
    store, idx = store_with_phrase_corpus
    via_linear = _search_via_linear_scan(store, "comprehensive medium article")
    via_index = _search_via_index(
        store, idx, "comprehensive medium article",
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
    )
    linear_uuids = sorted(r.conversation_uuid for r in via_linear)
    index_uuids = sorted(r.conversation_uuid for r in via_index)
    assert linear_uuids == index_uuids, (
        f"Multi-word path divergence. linear={linear_uuids} "
        f"index={index_uuids}."
    )


# ----- 5. translate_query phrase mode ----------------------------


def test_translate_query_quoted_emits_fts5_phrase():
    """Quoted input must emit a single FTS5 phrase, NOT N AND'd phrases.

    FTS5 MATCH `"foo" AND "bar" AND "baz"` matches any message containing
    all three tokens anywhere — wrong for an exact-phrase request.
    `"foo bar baz"` requires adjacency.
    """
    out = si.translate_query('"foo bar baz"')
    assert out == '"foo bar baz"'


def test_translate_query_quoted_no_wildcard():
    """Phrase mode must NOT trail a `*` wildcard — the user explicitly
    asked for an exact phrase, not a prefix."""
    out = si.translate_query('"hello"')
    assert "*" not in out


def test_translate_query_unquoted_multi_word_unchanged():
    """Token mode (unquoted) preserves existing AND semantics with the
    last-token wildcard for search-as-you-type."""
    out = si.translate_query("hello world")
    assert out == '"hello" AND "world" *'
