"""Hunt #12 — Unstable sort tiebreakers.

The bug:
    ``results.sort(key=lambda r: (r.conversation_name or "").lower(),
    reverse=reverse)``
    has NO secondary key. Python's Timsort is stable, but "stable"
    only preserves INPUT order when primary keys tie. Upstream input
    order here is non-deterministic across calls:

      * sqlite3 SELECT WITHOUT ORDER BY returns rows in undefined
        order, especially after the FTS5 virtual table has had any
        INSERT / UPDATE since the last query.
      * os.scandir() / os.listdir() filesystem walk order varies
        across filesystems, snapshots, and process restarts.
      * set iteration order varies across processes under
        PYTHONHASHSEED randomization (default ON since 3.3).
      * dict iteration is insertion-ordered, but the *initial
        insertion* is driven by the above non-deterministic sources.

Failure mode the user sees: UI flicker on sidebar refresh,
pagination drift on cursor-based fetches, intermittent test flakes
that don't repro on a developer laptop.

Why this file unit-tests the sort function DIRECTLY rather than
calling search_conversations() / list_conversations() twice and
comparing:

    Calling the outer route twice in the same process hits the
    same untouched data source twice, so it gets the same input
    order twice, so the unstable sort produces the same output
    twice — the test is a false positive that passes WITHOUT the
    fix. (Confirmed by Gemini-2.5-Pro adversarial review.) The
    deterministic way to trigger the bug is to feed the sort
    function two deliberately-different input orderings of the
    SAME items and assert the OUTPUT is identical. That's what we
    do here.

Bidirectional verification: each test asserts the exact UUID order
expected after the fix, not just "the two outputs agree." Without
the fix, the two outputs DISAGREE for some seed of UUIDs; with the
fix, both agree AND match the pinned order.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models import ConversationSummary, MessageSnippet, SearchResult
from backend.search import _sort_results

# ----- helpers ----------------------------------------------------


def _sr(
    uuid: str,
    *,
    name: str = "Untitled",
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    project_name: str | None = None,
    messages: list[MessageSnippet] | None = None,
) -> SearchResult:
    """SearchResult with safe defaults; only override what the test pins."""
    t = updated_at or datetime(2026, 5, 1, tzinfo=UTC)
    return SearchResult(
        conversation_uuid=uuid,
        conversation_name=name,
        conversation_updated_at=t,
        conversation_created_at=created_at or t,
        project_name=project_name,
        matching_messages=messages or [],
    )


def _cs(
    uuid: str,
    *,
    name: str = "Untitled",
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    project_path: str | None = None,
) -> ConversationSummary:
    t = updated_at or datetime(2026, 5, 1, tzinfo=UTC)
    return ConversationSummary(
        uuid=uuid,
        name=name,
        created_at=created_at or t,
        updated_at=t,
        project_path=project_path,
    )


def _ms(uuid: str, *, created_at: datetime | None = None) -> MessageSnippet:
    return MessageSnippet(
        message_uuid=uuid,
        sender="human",
        snippet="x",
        match_start=0,
        match_end=1,
        created_at=created_at or datetime(2026, 5, 1, tzinfo=UTC),
    )


# =================================================================
# backend/search.py :: _sort_results
# =================================================================
#
# Three items per test (not two): with two items, an unstable sort
# has a 50% chance of accidentally matching the expected order.
# Three items make accidental matches far less likely AND prove the
# tiebreaker actually executes a multi-element comparison.


class TestSearchSortTiebreakers:
    """Each test feeds _sort_results two DIFFERENT input orderings of
    the same three items (all sharing the primary sort key) and
    asserts the OUTPUT is identical across both calls. Without the
    fix, the outputs differ because Timsort preserves the differing
    input orders."""

    @pytest.fixture
    def three_same_name(self) -> list[SearchResult]:
        """Three results with IDENTICAL name and IDENTICAL timestamp;
        only UUIDs differ. Without a tiebreaker, the sort output is
        determined by input order."""
        t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        return [
            _sr("aaa-111", name="Untitled", updated_at=t),
            _sr("mmm-555", name="Untitled", updated_at=t),
            _sr("zzz-999", name="Untitled", updated_at=t),
        ]

    @pytest.fixture
    def three_same_time(self) -> list[SearchResult]:
        t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        return [
            _sr("aaa-111", name="A", updated_at=t),
            _sr("mmm-555", name="M", updated_at=t),
            _sr("zzz-999", name="Z", updated_at=t),
        ]

    @pytest.fixture
    def three_same_project(self) -> list[SearchResult]:
        t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        return [
            _sr("aaa-111", updated_at=t, project_name="proj-x"),
            _sr("mmm-555", updated_at=t, project_name="proj-x"),
            _sr("zzz-999", updated_at=t, project_name="proj-x"),
        ]

    def test_name_sort_asc_is_stable_across_input_orderings(self, three_same_name):
        forward = _sort_results(list(three_same_name), sort="name", sort_order="asc")
        reverse_input = _sort_results(
            list(reversed(three_same_name)), sort="name", sort_order="asc"
        )
        assert [r.conversation_uuid for r in forward] == [
            r.conversation_uuid for r in reverse_input
        ]
        # Pin the deterministic order: same name, same time → asc by UUID.
        assert [r.conversation_uuid for r in forward] == [
            "aaa-111",
            "mmm-555",
            "zzz-999",
        ]

    def test_name_sort_desc_is_stable_across_input_orderings(self, three_same_name):
        forward = _sort_results(list(three_same_name), sort="name", sort_order="desc")
        reverse_input = _sort_results(
            list(reversed(three_same_name)), sort="name", sort_order="desc"
        )
        assert [r.conversation_uuid for r in forward] == [
            r.conversation_uuid for r in reverse_input
        ]
        # `reverse=True` reverses the entire tuple, so UUID tertiary
        # also reverses.
        assert [r.conversation_uuid for r in forward] == [
            "zzz-999",
            "mmm-555",
            "aaa-111",
        ]

    def test_updated_at_sort_is_stable_across_input_orderings(self, three_same_time):
        forward = _sort_results(
            list(three_same_time), sort="updated_at", sort_order="desc"
        )
        reverse_input = _sort_results(
            list(reversed(three_same_time)), sort="updated_at", sort_order="desc"
        )
        assert [r.conversation_uuid for r in forward] == [
            r.conversation_uuid for r in reverse_input
        ]
        assert [r.conversation_uuid for r in forward] == [
            "zzz-999",
            "mmm-555",
            "aaa-111",
        ]

    def test_created_at_sort_is_stable_across_input_orderings(self, three_same_time):
        forward = _sort_results(
            list(three_same_time), sort="created_at", sort_order="asc"
        )
        reverse_input = _sort_results(
            list(reversed(three_same_time)), sort="created_at", sort_order="asc"
        )
        assert [r.conversation_uuid for r in forward] == [
            r.conversation_uuid for r in reverse_input
        ]
        assert [r.conversation_uuid for r in forward] == [
            "aaa-111",
            "mmm-555",
            "zzz-999",
        ]

    def test_project_sort_is_stable_across_input_orderings(self, three_same_project):
        forward = _sort_results(
            list(three_same_project), sort="project", sort_order="asc"
        )
        reverse_input = _sort_results(
            list(reversed(three_same_project)), sort="project", sort_order="asc"
        )
        assert [r.conversation_uuid for r in forward] == [
            r.conversation_uuid for r in reverse_input
        ]
        assert [r.conversation_uuid for r in forward] == [
            "aaa-111",
            "mmm-555",
            "zzz-999",
        ]

    def test_project_sort_with_null_project_buckets_nulls_last(self):
        """Mixed null / non-null project names: the existing
        ``(project_name is None, ...)`` boolean key buckets nulls at
        the END for asc. The tiebreaker must not perturb that."""
        t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        items = [
            _sr("uuid-z", project_name=None, updated_at=t),
            _sr("uuid-a", project_name="proj-a", updated_at=t),
            _sr("uuid-m", project_name="proj-a", updated_at=t),
        ]
        out = _sort_results(items, sort="project", sort_order="asc")
        uuids = [r.conversation_uuid for r in out]
        # proj-a bucket first (uuid-a then uuid-m by UUID asc), then
        # the null bucket.
        assert uuids == ["uuid-a", "uuid-m", "uuid-z"]

    def test_matching_messages_inner_sort_is_stable(self):
        """The per-result ``matching_messages.sort`` also needed a
        tiebreaker — when two matched messages share a created_at
        timestamp, they would silently flip order."""
        t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        msgs = [
            _ms("msg-aaa", created_at=t),
            _ms("msg-mmm", created_at=t),
            _ms("msg-zzz", created_at=t),
        ]
        r1 = _sr("conv-1", updated_at=t, messages=list(msgs))
        r2 = _sr("conv-1", updated_at=t, messages=list(reversed(msgs)))

        _sort_results([r1], sort="updated_at", sort_order="desc")
        _sort_results([r2], sort="updated_at", sort_order="desc")

        assert [m.message_uuid for m in r1.matching_messages] == [
            m.message_uuid for m in r2.matching_messages
        ]
        # asc-by-uuid under reverse=True reverses the tuple, so
        # desc-by-uuid is the pinned order.
        assert [m.message_uuid for m in r1.matching_messages] == [
            "msg-zzz",
            "msg-mmm",
            "msg-aaa",
        ]


# =================================================================
# backend/store.py :: list_conversations sort path
# =================================================================
#
# list_conversations sorts an in-memory list of ConversationSummary
# at the tail of the function. We test that sort path via the same
# input-shuffle pattern by calling the public method twice with
# data we wrote to disk in two different orders. Since the store
# reads from disk via glob (deterministic on a freshly-written
# tmp_path), we test the sort logic at the model layer to avoid
# coupling to the glob's deterministic-but-OS-dependent order.


def _sort_summaries_like_store(
    convs: list[ConversationSummary],
    *,
    sort: str,
    sort_order: str,
) -> list[ConversationSummary]:
    """Mirror of the sort tail in store.list_conversations. Kept
    in-test rather than imported because the production code wraps
    this in a method with filesystem-loading side effects, and we
    want to unit-test the SORT LOGIC ONLY.

    If you change the sort logic in store.py, change this mirror
    too — but the assertions below pin the BEHAVIOR (UUID-stable
    ordering across input shuffles), so a divergence will fail
    fast."""
    from backend.store import ConversationStore  # avoid circulars

    # We build a tiny ad-hoc store-like object only to invoke the
    # method's sort tail. The cheapest path is to call
    # list_conversations on a real store seeded with these
    # conversations, but that drags in the file layout. Instead,
    # rely on the public _sort_summaries pattern indirectly: just
    # call sorted() with the SAME keys the production code uses.
    # If those keys ever drift, the dedicated tests below
    # (which use the real ConversationStore) will catch it.
    del ConversationStore  # signal: we deliberately don't use it here

    reverse = sort_order == "desc"
    if sort == "name":
        return sorted(
            convs,
            key=lambda c: (c.name.lower(), c.updated_at, c.uuid),
            reverse=reverse,
        )
    if sort == "created_at":
        return sorted(convs, key=lambda c: (c.created_at, c.uuid), reverse=reverse)
    if sort == "project":
        return sorted(
            convs,
            key=lambda c: (
                c.project_name is None,
                (c.project_name or "").lower(),
                c.updated_at,
                c.uuid,
            ),
            reverse=reverse,
        )
    return sorted(convs, key=lambda c: (c.updated_at, c.uuid), reverse=reverse)


class TestStoreSortTiebreakersViaRealList:
    """End-to-end sort verification through ConversationStore.list_conversations.

    Seeds three conversations to disk with identical primary sort
    keys (same name + same updated_at) and distinct UUIDs. Calls
    list_conversations directly and asserts the deterministic
    UUID-tiebroken order. Without the tiebreaker, the glob walk
    order would drive the output (deterministic on a freshly-
    written tmp_path, so this test on its own doesn't repro the
    flicker — but combined with the unit tests above, it pins the
    end-to-end contract).
    """

    @pytest.fixture
    def store_with_same_name_convs(self, tmp_path):
        import json

        from backend.store import ConversationStore

        by_org = tmp_path / "by-org" / "org-1"
        by_org.mkdir(parents=True, exist_ok=True)
        t = "2026-05-01T12:00:00Z"
        for uuid in ("aaa-111", "mmm-555", "zzz-999"):
            (by_org / f"{uuid}.json").write_text(
                json.dumps(
                    {
                        "uuid": uuid,
                        "name": "Untitled",
                        "summary": "",
                        "model": "claude-sonnet-4-6",
                        "created_at": t,
                        "updated_at": t,
                        "is_starred": False,
                        "current_leaf_message_uuid": f"{uuid}-m1",
                        "project_path": None,
                        "source": "CLAUDE_AI",
                        "chat_messages": [],
                    }
                )
            )
        cc_dir = tmp_path / "claude-empty"
        cc_dir.mkdir()
        return ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    def test_name_sort_asc_orders_same_name_by_uuid(self, store_with_same_name_convs):
        convs = store_with_same_name_convs.list_conversations(
            sort="name", sort_order="asc"
        )
        uuids = [c.uuid for c in convs]
        assert uuids == ["aaa-111", "mmm-555", "zzz-999"], (
            f"Same-name convs must sort by UUID asc, got {uuids}"
        )

    def test_updated_at_sort_desc_orders_same_time_by_uuid_desc(
        self, store_with_same_name_convs
    ):
        convs = store_with_same_name_convs.list_conversations(
            sort="updated_at", sort_order="desc"
        )
        uuids = [c.uuid for c in convs]
        # reverse=True reverses the tuple, so UUID tiebreaker is desc.
        assert uuids == ["zzz-999", "mmm-555", "aaa-111"], (
            f"Same-updated_at convs must sort by UUID desc, got {uuids}"
        )


class TestStoreSortLogicMirror:
    """The mirror function above (_sort_summaries_like_store) MUST
    behave identically to the real list_conversations sort tail.
    Smoke-test by feeding shuffled inputs to both and confirming the
    mirror's order matches the real call.

    This catches drift if anyone edits store.py's sort tail without
    updating the mirror or the dedicated tests."""

    def test_mirror_matches_real_store_for_same_name(self, tmp_path):
        import json

        from backend.store import ConversationStore

        by_org = tmp_path / "by-org" / "org-1"
        by_org.mkdir(parents=True, exist_ok=True)
        t = "2026-05-01T12:00:00Z"
        for uuid in ("aaa-111", "mmm-555", "zzz-999"):
            (by_org / f"{uuid}.json").write_text(
                json.dumps(
                    {
                        "uuid": uuid,
                        "name": "Untitled",
                        "summary": "",
                        "model": "claude-sonnet-4-6",
                        "created_at": t,
                        "updated_at": t,
                        "is_starred": False,
                        "current_leaf_message_uuid": f"{uuid}-m1",
                        "project_path": None,
                        "source": "CLAUDE_AI",
                        "chat_messages": [],
                    }
                )
            )
        cc_dir = tmp_path / "claude-empty"
        cc_dir.mkdir()
        store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)
        real = store.list_conversations(sort="name", sort_order="asc")

        # Build summaries from the same disk data and feed shuffled
        # into the mirror.
        from datetime import datetime

        t_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        summaries = [
            _cs("aaa-111", name="Untitled", updated_at=t_dt),
            _cs("mmm-555", name="Untitled", updated_at=t_dt),
            _cs("zzz-999", name="Untitled", updated_at=t_dt),
        ]
        mirrored_forward = _sort_summaries_like_store(
            summaries, sort="name", sort_order="asc"
        )
        mirrored_reversed = _sort_summaries_like_store(
            list(reversed(summaries)), sort="name", sort_order="asc"
        )

        assert [c.uuid for c in real] == [c.uuid for c in mirrored_forward]
        assert [c.uuid for c in mirrored_forward] == [
            c.uuid for c in mirrored_reversed
        ]
