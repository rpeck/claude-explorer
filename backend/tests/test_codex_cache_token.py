"""A Codex detail and list row also depend on other thread files (N11).

A fork's history base and a thread's children live in other thread files.
The store's detail cache and the adapter's row cache must see a change of
those files without a restart: ``CodexAdapter.cache_token``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.tests import codex_fake as fake
from backend.tests.test_codex_source import CodexEnv, cx  # noqa: F401  (the fixture)

PARENT = "019a0000-0000-7000-8000-00000000a001"
CHILD = "019a0000-0000-7000-8000-00000000a002"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CWD = "/home/fixture/proj-links"


def _lines(thread_id: str, when: datetime, text: str, **meta: Any) -> list[dict[str, Any] | str]:
    """One paginated turn; each line gets an ordinal, as Codex writes them."""
    lines = fake._simple_paginated_lines(when, thread_id, CWD, f"{text} question", f"{text} reply")
    lines[0]["payload"].update(meta)
    start = meta.get("history_base", {}).get("end_ordinal_exclusive", 0)
    for n, line in enumerate(lines):
        line["ordinal"] = start + n
    return lines


def _write(env: CodexEnv, thread_id: str, when: datetime, text: str, **meta: Any) -> None:
    fake.write_rollout(fake.rollout_path(env.codex, when, thread_id), _lines(thread_id, when, text, **meta))


def test_a_child_that_appears_after_the_parent_detail_is_cached_shows_on_the_next_call(cx: CodexEnv) -> None:  # noqa: F811
    _write(cx, PARENT, T0, "parent")
    cx.sync()
    store = cx.store()
    assert store.get_conversation_dict(PARENT)["child_uuids"] == []

    _write(cx, CHILD, T0 + timedelta(hours=1), "child", forked_from_id=PARENT)
    cx.sync()

    assert store.get_conversation_dict(PARENT)["child_uuids"] == [CHILD]


def test_a_base_copied_after_the_child_detail_is_cached_resolves_on_the_next_call(cx: CodexEnv) -> None:  # noqa: F811
    base = {"thread_id": PARENT, "end_ordinal_exclusive": 4}
    _write(cx, CHILD, T0 + timedelta(hours=1), "child", forked_from_id=PARENT, history_base=base)
    cx.sync()
    store = cx.store()
    assert "incomplete: history_base missing" in store.get_conversation_dict(CHILD)["notices"]
    assert "incomplete: history_base missing" in cx.rows()[CHILD].notices

    _write(cx, PARENT, T0, "parent")
    cx.sync()

    assert not [n for n in cx.rows()[CHILD].notices if n.startswith("incomplete:")]
    detail = store.get_conversation_dict(CHILD)
    assert not [n for n in detail["notices"] if n.startswith("incomplete:")]
    texts = [m["text"] for m in detail["messages"]]
    assert texts[0] == "parent question"  # the inherited messages come first
