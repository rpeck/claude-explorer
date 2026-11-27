"""L2 (issue #91, part 2): session state (plan §2.2.7).

The contract (orchestrator, 2026-11-27):

* ``backend/session_state.py``:
  * ``SessionState = Literal["running", "open", "ended", "ended_no_exit"]``,
    ``OPEN_WINDOW = timedelta(days=7)``.
  * ``cc_end_after_last_turn(records)``: True only when a ``cost-state`` or a
    ``continued-in`` record comes after the last turn (a ``user`` or
    ``assistant`` record).
  * ``live_session_ids(claude_dir)``: the ``sessionId`` of each
    ``<claude_dir>/sessions/*.json`` whose ``pid`` is alive. A file that does not
    parse is skipped; a missing folder gives an empty set.
  * ``state_of(*, ended, last_activity, now, running=False)``: running wins, then
    ended, then "open" under 7 days, else "ended_no_exit".
* ``state`` on ConversationSummary, ConversationListItem, and ConversationDetail.
  Codex and Cowork: by age only. Desktop (CLAUDE_AI): null.

The corpus is ``peer_tree_fake`` (synthetic).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests import peer_tree_fake as f
from backend.tests.test_codex_source import CodexEnv, cx  # noqa: F401  (the cx fixture)

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)


def _user(n: int) -> dict[str, Any]:
    return {"type": "user", "uuid": f"u{n}", "message": {"role": "user", "content": "hi"}}


def _assistant(n: int) -> dict[str, Any]:
    return {"type": "assistant", "uuid": f"a{n}", "message": {"role": "assistant", "content": []}}


COST = {"type": "cost-state", "sessionId": "s", "totalCostUSD": 0.0, "modelUsage": {}}
CONT = {"type": "continued-in", "timestamp": "2026-11-27T00:00:00Z", "sessionId": "s", "continuedInSessionId": "t"}


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def test_the_open_window_is_seven_days() -> None:
    from backend.session_state import OPEN_WINDOW

    assert timedelta(days=7) == OPEN_WINDOW


def test_a_cost_state_after_the_last_turn_is_an_end() -> None:
    from backend.session_state import cc_end_after_last_turn

    assert cc_end_after_last_turn([_user(1), _assistant(1), COST]) is True


def test_a_cost_state_before_a_resumed_turn_is_not_an_end() -> None:
    from backend.session_state import cc_end_after_last_turn

    assert cc_end_after_last_turn([_user(1), _assistant(1), COST, _user(2), _assistant(2)]) is False


def test_no_end_record_is_not_an_end() -> None:
    from backend.session_state import cc_end_after_last_turn

    assert cc_end_after_last_turn([_user(1), _assistant(1)]) is False


def test_a_continued_in_record_after_the_last_turn_is_an_end() -> None:
    from backend.session_state import cc_end_after_last_turn

    assert cc_end_after_last_turn([_user(1), _assistant(1), CONT]) is True


def test_other_records_after_the_end_record_keep_the_end() -> None:
    from backend.session_state import cc_end_after_last_turn

    system = {"type": "system", "subtype": "turn_duration"}
    attach = {"type": "attachment", "attachment": {"type": "deferred_tools_delta"}}
    assert cc_end_after_last_turn([_user(1), _assistant(1), COST, system, attach]) is True


def test_running_wins_over_an_end_record() -> None:
    from backend.session_state import state_of

    assert state_of(ended=True, last_activity=NOW - timedelta(days=30), now=NOW, running=True) == "running"


def test_an_end_record_gives_ended_also_when_old() -> None:
    from backend.session_state import state_of

    assert state_of(ended=True, last_activity=NOW - timedelta(hours=1), now=NOW) == "ended"
    assert state_of(ended=True, last_activity=NOW - timedelta(days=30), now=NOW) == "ended"


def test_no_end_record_and_less_than_seven_days_is_open() -> None:
    from backend.session_state import state_of

    assert state_of(ended=False, last_activity=NOW - timedelta(days=7) + timedelta(seconds=1), now=NOW) == "open"


@pytest.mark.parametrize("age", [timedelta(days=7), timedelta(days=10)])
def test_no_end_record_and_seven_days_or_more_is_ended_no_exit(age: timedelta) -> None:
    from backend.session_state import state_of

    assert state_of(ended=False, last_activity=NOW - age, now=NOW) == "ended_no_exit"


# ---------------------------------------------------------------------------
# The live registry
# ---------------------------------------------------------------------------


def _entry(reg: Path, pid: int, session_id: str | None) -> None:
    reg.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {"pid": pid, "cwd": "/home/fixture", "status": "idle", "kind": "interactive"}
    if session_id is not None:
        body["sessionId"] = session_id
    (reg / f"{pid}.json").write_text(json.dumps(body), encoding="utf-8")


def test_the_registry_lists_a_session_whose_pid_is_alive(tmp_path: Path) -> None:
    from backend.session_state import live_session_ids

    _entry(tmp_path / "sessions", os.getpid(), "live-session")
    assert live_session_ids(tmp_path) == {"live-session"}


def test_the_registry_leaves_out_a_session_whose_pid_is_dead(tmp_path: Path) -> None:
    from backend.session_state import live_session_ids

    _entry(tmp_path / "sessions", f.dead_pid(), "dead-session")
    assert live_session_ids(tmp_path) == set()


def test_a_registry_file_that_does_not_parse_is_skipped(tmp_path: Path) -> None:
    from backend.session_state import live_session_ids

    _entry(tmp_path / "sessions", os.getpid(), "live-session")
    (tmp_path / "sessions" / "123.json").write_text('{"pid": 123, "sessionId": "cut', encoding="utf-8")
    (tmp_path / "sessions" / "124.json").write_text("[1, 2]", encoding="utf-8")
    assert live_session_ids(tmp_path) == {"live-session"}


def test_a_registry_entry_without_a_session_id_is_skipped(tmp_path: Path) -> None:
    from backend.session_state import live_session_ids

    _entry(tmp_path / "sessions", os.getpid(), None)
    assert live_session_ids(tmp_path) == set()


def test_a_missing_registry_folder_gives_no_sessions(tmp_path: Path) -> None:
    from backend.session_state import live_session_ids

    assert live_session_ids(tmp_path / "nothing-here") == set()


# ---------------------------------------------------------------------------
# The list and the detail
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[f.Tree]:
    with f.use_tree(tmp_path, monkeypatch) as t:
        yield t


def _client() -> Any:
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


def _list_states() -> dict[str, Any]:
    r = _client().get("/api/conversations")
    assert r.status_code == 200, r.text
    return {row["uuid"]: row.get("state", "<missing>") for row in r.json()}


EXPECTED = {
    f.X_ENDED: "ended",
    f.X_RESUMED: "open",
    f.X_OLD: "ended_no_exit",
    f.X_CONT: "ended",
    f.X_RUN: "running",
    f.X_DEAD: "ended",
    f.CO: "open",
    f.W11: "open",
}


@pytest.mark.parametrize("session", list(EXPECTED), ids=[f.NAMES[s] for s in EXPECTED])
def test_the_list_carries_the_state_of_each_claude_code_session(tree: f.Tree, session: str) -> None:
    assert _list_states()[session] == EXPECTED[session]


def test_a_registry_file_that_does_not_parse_does_not_break_the_list(tree: f.Tree) -> None:
    # The corpus registry holds one cut file; the live entry still counts.
    assert (tree.claude_dir / "sessions" / "77777.json").exists()
    assert _list_states()[f.X_RUN] == "running"


@pytest.mark.parametrize("session", [f.X_ENDED, f.X_RUN, f.CO], ids=["ended", "running", "open"])
def test_the_detail_carries_the_state(tree: f.Tree, session: str) -> None:
    from backend.store import invalidate_detail_dict_cache

    invalidate_detail_dict_cache()
    r = _client().get(f"/api/conversations/{session}")
    assert r.status_code == 200, r.text
    assert r.json().get("state", "<missing>") == EXPECTED[session]


def test_the_list_item_and_the_summary_models_both_have_state() -> None:
    from backend.models import (
        ConversationDetail,
        ConversationListItem,
        ConversationSummary,
    )

    for model in (ConversationListItem, ConversationSummary, ConversationDetail):
        assert "state" in model.model_fields, model.__name__


# ---------------------------------------------------------------------------
# Codex and Desktop
# ---------------------------------------------------------------------------

RECENT_THREAD = "019ab000-0000-7000-8000-0000000000f1"
DESKTOP_UUID = "dddd0000-0000-4000-8000-00000000d001"


def test_codex_state_comes_from_age_only(cx: CodexEnv) -> None:  # noqa: F811
    from backend.tests import codex_fake as fake

    t0 = datetime.now(UTC) - timedelta(hours=1)
    fake.write_rollout(
        fake.rollout_path(cx.codex, t0, RECENT_THREAD),
        fake._simple_paginated_lines(t0, RECENT_THREAD, "/home/fixture/proj-new", "A recent question.", "A recent answer."),
    )
    cx.sync()
    rows = cx.rows()
    assert rows[RECENT_THREAD].state == "open"
    # The default corpus is from 2026-11-12 or older.
    old = [r for u, r in rows.items() if u != RECENT_THREAD]
    assert old
    for r in old:
        age = datetime.now(UTC) - r.updated_at.astimezone(UTC)
        assert r.state == ("open" if age < timedelta(days=7) else "ended_no_exit"), (r.uuid, r.state)


def test_a_desktop_conversation_has_no_state(cx: CodexEnv) -> None:  # noqa: F811
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(cx.data_dir, build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"))
    rows = {r.uuid: r for r in cx.store().list_conversations()}
    assert rows[DESKTOP_UUID].state is None
