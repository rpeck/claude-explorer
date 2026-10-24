"""``SearchIndex.count_matches`` — RED phase (will fail until impl lands).

The truncation disclosure envelope needs a cheap COUNT(*) of the FTS5
MATCH rows under the same WHERE clauses as ``query_with_snippets`` —
without paying the snippet() cost. This file pins the contract.

Plan reference: ``PLANS/SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE.md``
§B (count_matches) and Risk #5 (the SQL between count_matches and
query_with_snippets must NEVER drift; shared helper required).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from backend import search_index as si

# ----- fixtures --------------------------------------------------------


def _msg(uuid: str, *, sender: str = "human", text: str,
         created_at: str = "2026-05-16T12:00:00Z") -> dict[str, Any]:
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "created_at": created_at,
        "updated_at": created_at,
        "parent_message_uuid": None,
    }


def _conv(uuid: str, name: str, messages: list[dict[str, Any]],
          *, source: str = "CLAUDE_AI") -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-16T12:00:00Z",
        "updated_at": "2026-05-16T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "project_path": None,
        "source": source,
        "chat_messages": messages,
    }


@pytest.fixture(autouse=True)
def _reset_singleton():
    si.reset_search_index_for_tests()
    yield
    si.reset_search_index_for_tests()


@pytest.fixture
def count_idx(tmp_path):
    """SearchIndex with a deterministic mix of needle and non-needle messages.

    Needle: ``countcanary``. 7 messages contain it; 3 don't.
    """
    idx = si.SearchIndex(tmp_path / "count.sqlite")
    # 7 conversations with the needle (one CC, six Desktop).
    for i in range(7):
        text = f"document {i} carries countcanary keyword"
        src = "CLAUDE_CODE" if i == 0 else "CLAUDE_AI"
        c = _conv(
            f"conv-needle-{i:03d}",
            f"Needle conv {i}",
            [_msg(f"m-needle-{i:03d}", text=text)],
            source=src,
        )
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)

    # 3 conversations without the needle.
    for i in range(3):
        text = f"unrelated document {i} mentions nothing important"
        c = _conv(
            f"conv-bare-{i:03d}",
            f"Bare conv {i}",
            [_msg(f"m-bare-{i:03d}", text=text)],
        )
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)

    idx.mark_ready()
    yield idx
    idx.close()


# ----- 14. count_matches returns exact COUNT(*) -------------------------


def test_count_matches_returns_exact_count(count_idx) -> None:
    """The needle appears in exactly 7 messages. ``count_matches`` returns 7.

    Bug it would surface: ``count_matches`` reading from a different
    table or applying an extra LIMIT.
    """
    n = count_idx.count_matches("countcanary")
    assert n == 7, f"expected 7 matches; got {n}"


# ----- 15. count_matches honors filters identically to query_with_snippets


def test_count_matches_honors_source_filter(count_idx) -> None:
    """Filter to source=CLAUDE_AI: 6 messages match (we seeded 1 CC + 6 Desktop).
    ``count_matches`` and ``query_with_snippets`` must agree on the count
    of distinct (conv_uuid, message_uuid) pairs returned.

    Bug it would surface: WHERE-clause skew between the two SQL paths —
    Risk #5 in the plan. The shared ``_build_match_where_clause`` helper
    is the regression guard.
    """
    n_all = count_idx.count_matches("countcanary", source="all")
    n_ai = count_idx.count_matches("countcanary", source="CLAUDE_AI")
    n_cc = count_idx.count_matches("countcanary", source="CLAUDE_CODE")
    assert n_all == 7
    assert n_ai == 6
    assert n_cc == 1

    # And query_with_snippets agrees on the matched-row set size (under
    # a LIMIT much higher than our fixture's count so no truncation).
    rows_ai = count_idx.query_with_snippets(
        "countcanary", source="CLAUDE_AI", limit=10_000,
    )
    assert len(rows_ai) == n_ai, (
        f"count_matches and query_with_snippets must agree on filter "
        f"behavior; count={n_ai}, snippets={len(rows_ai)}"
    )
    rows_cc = count_idx.query_with_snippets(
        "countcanary", source="CLAUDE_CODE", limit=10_000,
    )
    assert len(rows_cc) == n_cc


def test_count_matches_honors_include_tool_calls(tmp_path) -> None:
    """When the toggle is OFF, tool-only matches are excluded from the
    count too — same column-MATCH semantics as query_with_snippets.

    Bug it would surface: count_matches always reading body (even with
    flag False) — the envelope's total would lie.
    """
    idx = si.SearchIndex(tmp_path / "count-tools.sqlite")
    try:
        # Text-only conv with the token.
        text_conv = _conv("conv-text", "Text", [
            _msg("m-text", text="alphacatcount in text body"),
        ])
        # Tool-only conv with the token.
        tool_conv = _conv("conv-tool", "Tool", [
            {
                "uuid": "m-tool",
                "sender": "assistant",
                "text": "",
                "content": [{
                    "type": "tool_use",
                    "id": "tu",
                    "name": "Bash",
                    "input": {"command": "echo alphacatcount"},
                }],
                "created_at": "2026-05-16T12:00:00Z",
                "updated_at": "2026-05-16T12:00:00Z",
                "parent_message_uuid": None,
            },
        ])
        idx.upsert_conversation(text_conv, tmp_path / "text.json", 1.0)
        idx.upsert_conversation(tool_conv, tmp_path / "tool.json", 1.0)
        idx.mark_ready()

        # include_tool_calls=True: both messages match.
        n_full = idx.count_matches("alphacatcount", include_tool_calls=True)
        assert n_full == 2, f"full projection should find both; got {n_full}"

        # include_tool_calls=False: only the text-block match counts.
        n_text = idx.count_matches("alphacatcount", include_tool_calls=False)
        assert n_text == 1, (
            f"text-only projection should exclude tool-only; got {n_text}"
        )
    finally:
        idx.close()


# ----- 16. count_matches cost (soft latency check) ----------------------


@pytest.mark.serial
def test_count_matches_is_cheap(count_idx) -> None:
    """count_matches should be substantially cheaper than
    query_with_snippets on any non-trivial corpus — no snippet() walk,
    no per-row work. We assert a soft 50 ms ceiling and print the
    actual elapsed so future tuning has a baseline.

    Bug it would surface: count_matches doing per-row work (e.g.
    iterating rows in Python) — would be visible as O(n) vs the
    expected O(matches).
    """
    t0 = time.perf_counter()
    for _ in range(10):
        count_idx.count_matches("countcanary")
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 10
    print(f"\ncount_matches: {elapsed_ms:.2f} ms / call (10-iter mean)")
    # 50 ms is generous for a 10-row test corpus on any machine; real
    # corpora are bounded by FTS5's BM25 walk, not row count.
    assert elapsed_ms < 50, (
        f"count_matches took {elapsed_ms:.2f} ms — far above the 50 ms "
        f"ceiling; check for per-row Python work"
    )
