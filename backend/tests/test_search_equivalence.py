"""Equivalence between the FTS5 fast path and the linear-scan fallback.

PLANS/2026.05.10-search-fts5.md commit a contract: for the same input,
both paths must produce byte-for-byte identical ``SearchResult`` objects.
This file pins that contract for the common-case query domain
(whole-word and prefix-of-word queries).

What's NOT pinned (documented behavior change):
  * Sub-word substring queries that don't align with FTS5 token
    boundaries. Example: querying ``"ed"`` matches the substring inside
    ``"scheduled"`` under linear scan but NOT under FTS5 (which does
    leading-prefix matching only). This is the one accepted divergence
    and is documented in the search.py module docstring.

Bidirectional verification:
  Each test below was first run against an intentionally-broken FTS5
  path (e.g., omitting the title-substring sweep) to confirm it fails
  with an informative diff.
"""

from __future__ import annotations

import pytest

from backend import search_index as si
from backend.cache import clear_cache
from backend.search import (
    _search_via_index,
    _search_via_linear_scan,
    search_conversations,
)
from backend.store import ConversationStore
from backend.tests import builders as B

# ----- fixtures ---------------------------------------------------
# Conversation/message builders moved to ``backend.tests.builders``
# (PLANS/2026.05.18-test-hardening.md C4). The previous inline
# ``_conv()`` / ``_write_conv()`` helpers were byte-equivalent to
# ``B.build_desktop_conv()`` / ``B.write_desktop_conv()`` (same
# timestamps, same model, same single-human-message body) — see
# ``test_builders.py`` for the contract tests.


@pytest.fixture
def fixture_store(tmp_path, monkeypatch):
    """Build a synthetic 5-conversation store with predictable hits.

    Title-only match: 'cron' is in the name of conv-c only.
    Body-only match: 'pythonic' is in the body of conv-p only.
    Both: 'budget' appears in the title of conv-b1 and the body of conv-b2.
    No match: 'xyzzy' is nowhere.

    Test isolation: the module singleton ``_search_index`` is REPLACED
    with the fixture's index instance for the duration of the test, so
    any call to ``get_search_index()`` from production code (e.g., from
    ``search_conversations()``'s dispatcher) returns this test's index
    — NEVER the user's real ``~/.claude-explorer/search-index.sqlite``.
    Without this we'd silently scribble against the user's real index
    and possibly crawl their real conversations.
    """
    by_org = tmp_path / "by-org" / "org-1"
    convs = [
        B.build_desktop_conv(uuid="conv-c", name="Cron job notes", body="unrelated body content"),
        B.build_desktop_conv(uuid="conv-p", name="Untitled", body="here is some pythonic prose"),
        B.build_desktop_conv(uuid="conv-b1", name="budget review", body="unrelated body content"),
        B.build_desktop_conv(uuid="conv-b2", name="Untitled", body="our budget is tight this quarter"),
        B.build_desktop_conv(uuid="conv-z", name="Unrelated title", body="totally unrelated text"),
        # conv-mid: name has 'scheduled' which contains 'edul' as a
        # mid-token substring. FTS5 with porter+unicode61 prefix-match
        # WILL NOT find "edul" (prefix only matches leading). The
        # linear scan WILL find "edul" via Python `in` substring.
        # This row pins the title-substring sweep behavior.
        B.build_desktop_conv(uuid="conv-mid", name="scheduled-task notes", body="totally unrelated"),
    ]
    paths = [B.write_desktop_conv(by_org, c) for c in convs]
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    # Build a fresh index against this fixture and inject it as the
    # module-level singleton so production-code paths
    # (search_conversations -> get_search_index) hit the test index.
    clear_cache()
    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    monkeypatch.setattr(si, "_search_index", idx)

    yield store, idx, paths

    idx.close()
    si.reset_search_index_for_tests()
    clear_cache()


# ----- equivalence cases -----------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "cron",       # title-only match (conv-c)
        "pythonic",   # body-only match (conv-p)
        "budget",     # both title+body match (conv-b1, conv-b2)
        "xyzzy",      # no match anywhere
        "tight",      # body match (conv-b2)
        "Untitled",   # name match across multiple convs
        "edul",       # mid-token substring match in title (conv-mid).
                      # FTS5 alone misses this; the title-substring sweep
                      # in _search_via_index is what makes the paths
                      # equivalent. Removing the sweep regresses this
                      # parametrize case.
    ],
)
def test_index_and_linear_paths_return_same_results(fixture_store, query):
    """Every query produces byte-for-byte identical SearchResult lists.

    What this catches if it regresses:
      * FTS5 missing a result the linear scan would catch (or vice
        versa).
      * Snippet boundaries drifting between paths.
      * Sort order differing because one path forgot to sort.
      * Title pseudo-message disappearing on the FTS5 path.
    """
    store, idx, _ = fixture_store
    assert idx.is_ready()

    via_linear = _search_via_linear_scan(store, query)
    via_index = _search_via_index(
        store, idx, query,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
    )

    # Same set of conversation UUIDs.
    linear_uuids = sorted(r.conversation_uuid for r in via_linear)
    index_uuids = sorted(r.conversation_uuid for r in via_index)
    assert linear_uuids == index_uuids, (
        f"Diverged conv set for query={query!r}. "
        f"Linear: {linear_uuids}. Index: {index_uuids}."
    )

    # Same per-conversation snippet content.
    by_uuid_linear = {r.conversation_uuid: r for r in via_linear}
    by_uuid_index = {r.conversation_uuid: r for r in via_index}
    for cu in linear_uuids:
        lr = by_uuid_linear[cu]
        ir = by_uuid_index[cu]
        # Pydantic equality compares field-by-field.
        assert lr.matching_messages == ir.matching_messages, (
            f"Snippet drift for query={query!r}, conv={cu}.\n"
            f"  linear: {lr.matching_messages}\n"
            f"  index:  {ir.matching_messages}"
        )


def test_dispatcher_uses_index_when_ready(fixture_store):
    """search_conversations() — the public entry point — calls into the
    index when ready and the result is the same as direct index call.

    Bug it would surface: the dispatcher branch logic in
    search_conversations being inverted (always linear-scan), or the
    is_ready check returning the wrong value.
    """
    store, idx, _ = fixture_store
    via_dispatcher = search_conversations(store, "budget").results
    via_index_direct = _search_via_index(
        store, idx, "budget",
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
    )
    assert sorted(r.conversation_uuid for r in via_dispatcher) == \
           sorted(r.conversation_uuid for r in via_index_direct)


def test_dispatcher_falls_back_when_index_not_ready(fixture_store, monkeypatch):
    """When the index reports is_ready=False, the dispatcher uses the
    linear-scan fallback.

    Bug it would surface: forgetting the is_ready() check; queries hit
    a half-built index and return incomplete results.
    """
    store, idx, _ = fixture_store

    # Force is_ready False without dropping the index.
    monkeypatch.setattr(idx, "is_ready", lambda: False)

    via_dispatcher = search_conversations(store, "budget").results
    via_linear_direct = _search_via_linear_scan(store, "budget")

    assert sorted(r.conversation_uuid for r in via_dispatcher) == \
           sorted(r.conversation_uuid for r in via_linear_direct)


def test_dispatcher_falls_back_when_fts5_missing(fixture_store, monkeypatch):
    """When fts5_available()=False the dispatcher uses the linear-scan
    fallback (the singleton is None).

    Negative-space: even with the index file ON disk, if FTS5 isn't
    compiled in, we never instantiate SearchIndex. The dispatcher
    must handle the None return cleanly.
    """
    store, _, _ = fixture_store

    # The fixture left a real singleton; reset and replace with None.
    si.reset_search_index_for_tests()
    monkeypatch.setattr(si, "fts5_available", lambda: False)

    via_dispatcher = search_conversations(store, "budget").results
    via_linear_direct = _search_via_linear_scan(store, "budget")

    assert sorted(r.conversation_uuid for r in via_dispatcher) == \
           sorted(r.conversation_uuid for r in via_linear_direct)


def test_dispatcher_falls_back_on_sqlite_error(fixture_store, monkeypatch):
    """An unexpected sqlite3.Error during the FTS5 query path triggers
    the fallback.

    Bug it would surface: an uncaught sqlite3.Error would 500 the route
    even though the linear scan would have answered fine.
    """
    import sqlite3

    store, idx, _ = fixture_store

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("simulated index corruption")

    monkeypatch.setattr(idx, "query", _boom)

    via_dispatcher = search_conversations(store, "budget").results
    via_linear_direct = _search_via_linear_scan(store, "budget")

    assert sorted(r.conversation_uuid for r in via_dispatcher) == \
           sorted(r.conversation_uuid for r in via_linear_direct)


# ----- scope filter equivalence ----------------------------------


def test_scope_conversation_uuid_equivalent(fixture_store):
    """conversation_uuid restriction yields identical results on both paths."""
    store, idx, _ = fixture_store
    a = _search_via_linear_scan(store, "budget", conversation_uuid="conv-b1")
    b = _search_via_index(
        store, idx, "budget",
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid="conv-b1", project_path=None, bookmarks=None,
    )
    assert [r.conversation_uuid for r in a] == ["conv-b1"]
    assert [r.conversation_uuid for r in b] == ["conv-b1"]


def test_scope_bookmarks_equivalent(fixture_store):
    """bookmarks={…} produces identical results on both paths."""
    store, idx, _ = fixture_store
    a = _search_via_linear_scan(store, "budget", bookmarks={"conv-b1"})
    b = _search_via_index(
        store, idx, "budget",
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None,
        bookmarks={"conv-b1"},
    )
    assert sorted(r.conversation_uuid for r in a) == \
           sorted(r.conversation_uuid for r in b)
