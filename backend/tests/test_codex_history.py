"""N10: the Codex history projection (slice 2).

Plan: PLANS/2026.11.18-codex-source.md, §4.4 (the N10 part), §4.5 (message
ids), and the N10 row of §6.1. The API is the orchestrator's N10 contract:

* ``RolloutRecord.ordinal``: the top-level ``ordinal`` of a rollout line,
  ``None`` when the line has none.
* ``project_rollout(records, *, ..., load_base=None)``. ``load_base`` takes a
  ``HistoryPosition.thread_id`` (a rollout id) and returns the records of
  that rollout, or ``None`` when it is missing.
* New output key ``parent_uuid``; ``compact_markers`` holds one
  ``CompactMarker`` dict for each compaction.
* New incomplete reasons: ``history_base missing``, ``history_base cycle``,
  ``history_base too deep``.

The record shapes follow ``openai/codex`` at commit ``f2d3086``:

* ``RolloutLine.ordinal`` and ``CompactedItem`` (``codex-rs/history/src/lib.rs``);
* ``SessionMeta`` (``forked_from_id``, ``attached_thread``,
  ``forked_from_ordinal_exclusive``, ``history_base``,
  ``subagent_history_start_ordinal``), ``HistoryPosition``, and
  ``ThreadRolledBackEvent`` (``codex-rs/protocol/src/protocol.rs``);
* the lineage cut (``thread-store/src/local/rollout_lineage.rs``) and the
  rollback rule (``app-server-protocol/src/protocol/thread_history.rs``,
  ``handle_thread_rollback``);
* one compaction writes a ``compacted`` record and then a ``ContextCompaction``
  item in the same turn (``core/src/compact.rs``).

The slice 2 record helpers live in this file. No test reads a real ``~/.codex``.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests.codex_fake import (
    DAMAGED_LINE,
    THREAD_GUARDIAN,
    THREAD_LEGACY,
    THREAD_PAGINATED,
    THREAD_SUBAGENT,
    agent_message_item,
    build_codex_home,
    function_call,
    function_call_output,
    input_text,
    item_completed,
    message,
    output_text,
    response_item,
    rollout_line,
    session_meta,
    turn_context,
    user_message_item,
    write_rollout,
)

T0 = datetime(2026, 11, 16, 9, 0, 0, tzinfo=UTC)
CWD = "/home/fixture/proj-sigma"
TID = "019aa000-0000-7000-8000-0000000010aa"
PARENT = "019aa000-0000-7000-8000-0000000010bb"
GRANDPARENT = "019aa000-0000-7000-8000-0000000010cc"
REVERT_ROLLOUT = "019aa000-0000-7000-8000-0000000010dd"
MODEL = "gpt-5.5-codex"


def _s(n: int) -> datetime:
    return T0 + timedelta(seconds=n)


# ---------------------------------------------------------------------------
# Slice 2 record helpers
# ---------------------------------------------------------------------------


class _Rollout:
    """A paginated rollout under construction. Each line gets the next ordinal.

    Codex writes the ``session_meta`` at the ordinal where the thread's own
    history starts: 0 for a root rollout, ``history_base.end_ordinal_exclusive``
    for a rollout with a base (``rollout/src/ordinal.rs``).
    """

    def __init__(
        self,
        thread_id: str,
        *,
        history_base: dict[str, Any] | None = None,
        cwd: str = CWD,
        clock: int = 0,
        **meta: Any,
    ) -> None:
        self.thread_id = thread_id
        self.lines: list[dict[str, Any] | str] = []
        self.next = history_base["end_ordinal_exclusive"] if history_base else 0
        self.clock = clock
        self.cwd = cwd
        extra = {k: meta.pop(k) for k in list(meta) if k not in _SESSION_META_KWARGS}
        line = session_meta(
            _s(self.clock), thread_id, cwd=cwd, history_mode="paginated", **meta
        )
        line["payload"].update(extra)
        if history_base is not None:
            line["payload"]["history_base"] = history_base
        self.add(line)

    def add(self, line: dict[str, Any], *, ordinal: int | None = None) -> int:
        line = dict(line)
        line["ordinal"] = self.next if ordinal is None else ordinal
        self.next = line["ordinal"] + 1
        self.lines.append(line)
        return line["ordinal"]

    def tick(self) -> datetime:
        self.clock += 1
        return _s(self.clock)

    def turn(self, turn_id: str, user: str, reply: str) -> int:
        """Add one turn (context, question, reply). Return the ordinal where it starts."""
        start = self.add(turn_context(self.tick(), turn_id, cwd=self.cwd, model=MODEL))
        self.add(
            item_completed(
                self.tick(),
                self.thread_id,
                turn_id,
                user_message_item(f"{turn_id}-u", user),
            )
        )
        self.add(
            item_completed(
                self.tick(),
                self.thread_id,
                turn_id,
                agent_message_item(f"{turn_id}-r", reply),
            )
        )
        return start

    def base_at(self, end_ordinal_exclusive: int | None = None) -> dict[str, Any]:
        """A ``HistoryPosition`` into this rollout (all of it by default)."""
        end = self.next if end_ordinal_exclusive is None else end_ordinal_exclusive
        return {
            "thread_id": self.thread_id,
            "end_ordinal_exclusive": end,
            "end_byte_offset": 64 * end,
        }


_SESSION_META_KWARGS = {
    "source",
    "thread_source",
    "parent_thread_id",
    "agent_nickname",
    "git",
}


def _compacted(
    when: datetime, summary: str, *replacement: dict[str, Any]
) -> dict[str, Any]:
    # RolloutItem::Compacted. replacement_history holds ResponseItemEnvelope values.
    return rollout_line(
        when,
        "compacted",
        {
            "message": summary,
            "replacement_history": [{"item": r, "metadata": None} for r in replacement],
        },
    )


def _compaction_item(
    when: datetime, thread_id: str, turn_id: str, item_id: str
) -> dict[str, Any]:
    return item_completed(
        when, thread_id, turn_id, {"type": "ContextCompaction", "id": item_id}
    )


def _rolled_back(when: datetime, num_turns: int) -> dict[str, Any]:
    return rollout_line(
        when, "event_msg", {"type": "thread_rolled_back", "num_turns": num_turns}
    )


# ---------------------------------------------------------------------------
# Read and project
# ---------------------------------------------------------------------------


def _records(
    tmp_path: Path, rollout_id: str, lines: list[dict[str, Any] | str]
) -> list[Any]:
    from backend.codex.rollout import read_rollout

    folder = tmp_path / rollout_id
    path = write_rollout(
        folder / f"rollout-2026-11-16T09-00-00-{rollout_id}.jsonl", lines
    )
    return read_rollout(path).records


def _project(records: Sequence[Any], **kwargs: Any) -> dict[str, Any]:
    from backend.codex.projection import project_rollout

    return project_rollout(list(records), **kwargs)


def _loader(bases: dict[str, list[Any]]) -> Callable[[str], list[Any] | None]:
    return lambda rollout_id: bases.get(rollout_id)


def _texts(conv: dict[str, Any]) -> list[str]:
    return [m["text"] for m in conv["chat_messages"] if m["text"]]


def _turn_texts(conv: dict[str, Any]) -> list[str]:
    """The texts of human and agent messages only (no marker rows)."""
    marker_ids = {m["message_uuid"] for m in conv["compact_markers"]}
    return [
        m["text"]
        for m in conv["chat_messages"]
        if m["uuid"] not in marker_ids and m["text"]
    ]


def _uuid_of(conv: dict[str, Any], text: str) -> str:
    (found,) = [m["uuid"] for m in conv["chat_messages"] if m["text"] == text]
    return found


def _reasons(conv: dict[str, Any], prefix: str) -> list[str]:
    return [r for r in conv["incomplete_reasons"] if r.startswith(prefix)]


def _assert_complete(conv: dict[str, Any]) -> None:
    assert conv["incomplete"] is False
    assert conv["incomplete_reasons"] == []


def _three_turns(thread_id: str = TID, **meta: Any) -> tuple[_Rollout, list[int]]:
    r = _Rollout(thread_id, **meta)
    starts = [r.turn(f"turn-{n}", f"Question {n}.", f"Answer {n}.") for n in (1, 2, 3)]
    return r, starts


@pytest.fixture(scope="module")
def codex_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_codex_home(tmp_path_factory.mktemp("codex_home_n10"))


def _fake(home: Path, thread_id: str) -> dict[str, Any]:
    from backend.codex.rollout import read_rollout

    (path,) = list(home.rglob(f"rollout-*-{thread_id}.jsonl*"))
    read = read_rollout(path)
    return _project(read.records, damaged_lines=read.damaged_lines)


# ---------------------------------------------------------------------------
# The ordinal of a record
# ---------------------------------------------------------------------------


def test_read_rollout_keeps_the_ordinal_of_each_record(tmp_path: Path) -> None:
    r, _ = _three_turns()
    records = _records(tmp_path, TID, r.lines)
    assert [rec.ordinal for rec in records] == list(range(len(r.lines)))


def test_read_rollout_ordinal_is_none_when_the_line_has_none(tmp_path: Path) -> None:
    lines = [
        session_meta(_s(0), TID, cwd=CWD, history_mode="legacy"),
        response_item(_s(1), message("user", input_text("Hello."))),
    ]
    assert [rec.ordinal for rec in _records(tmp_path, TID, lines)] == [None, None]


# ---------------------------------------------------------------------------
# Clean sessions stay as slice 1 showed them
# ---------------------------------------------------------------------------


def test_fake_sessions_have_no_markers_and_stay_complete(codex_home: Path) -> None:
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        conv = _fake(codex_home, tid)
        assert conv["compact_markers"] == []
        assert conv["parent_uuid"] is None
        _assert_complete(conv)


def test_root_session_has_no_parent_uuid(codex_home: Path) -> None:
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        assert _fake(codex_home, tid)["parent_uuid"] is None


# ---------------------------------------------------------------------------
# Parent links (forks and subagents)
# ---------------------------------------------------------------------------


def test_fork_parent_uuid_is_forked_from_id(tmp_path: Path) -> None:
    r = _Rollout(TID, forked_from_id=PARENT)
    r.turn("turn-1", "Question 1.", "Answer 1.")
    assert _project(_records(tmp_path, TID, r.lines))["parent_uuid"] == PARENT


def test_subagent_parent_uuid_is_parent_thread_id(tmp_path: Path) -> None:
    r = _Rollout(
        TID, thread_source="subagent", parent_thread_id=PARENT, agent_nickname="Scout"
    )
    r.turn("turn-1", "Find the callers.", "Two callers.")
    assert _project(_records(tmp_path, TID, r.lines))["parent_uuid"] == PARENT


def test_subagent_parent_uuid_comes_from_the_source_thread_spawn(
    tmp_path: Path,
) -> None:
    source = {
        "subagent": {
            "thread_spawn": {
                "parent_thread_id": PARENT,
                "depth": 1,
                "agent_nickname": "Scout",
            }
        }
    }
    r = _Rollout(TID, thread_source="subagent", source=source)
    r.turn("turn-1", "Find the callers.", "Two callers.")
    assert _project(_records(tmp_path, TID, r.lines))["parent_uuid"] == PARENT


def test_fake_subagent_links_to_its_parent(codex_home: Path) -> None:
    assert _fake(codex_home, THREAD_SUBAGENT)["parent_uuid"] == THREAD_PAGINATED


def test_guardian_review_has_no_parent_uuid(codex_home: Path) -> None:
    # Its source is {"subagent": "review"}: no parent thread.
    assert _fake(codex_home, THREAD_GUARDIAN)["parent_uuid"] is None


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def _compacted_session(tmp_path: Path) -> dict[str, Any]:
    """Turn 1, then one compaction (``compacted`` + ``ContextCompaction``) in turn 2, then a reply."""
    r = _Rollout(TID)
    r.turn("turn-1", "Question 1.", "Answer 1.")
    r.add(turn_context(r.tick(), "turn-2", cwd=CWD, model=MODEL))
    r.add(
        item_completed(
            r.tick(), TID, "turn-2", user_message_item("turn-2-u", "Question 2.")
        )
    )
    r.add(
        _compacted(
            _s(100),
            "Summary: the user asked question 1 and got answer 1.",
            message(
                "user", input_text("REPLACEMENT CONTEXT: question 1 and answer 1.")
            ),
        )
    )
    r.add(_compaction_item(_s(100), TID, "turn-2", "turn-2-compact"))
    r.add(
        item_completed(
            r.tick(), TID, "turn-2", agent_message_item("turn-2-r", "Answer 2.")
        )
    )
    return _project(_records(tmp_path, TID, r.lines))


def test_compaction_becomes_one_compact_marker(tmp_path: Path) -> None:
    assert len(_compacted_session(tmp_path)["compact_markers"]) == 1


def test_compact_marker_summary_is_the_compacted_message(tmp_path: Path) -> None:
    (marker,) = _compacted_session(tmp_path)["compact_markers"]
    assert (
        marker["summary_text"] == "Summary: the user asked question 1 and got answer 1."
    )


def test_compact_marker_timestamp_is_the_compaction_time(tmp_path: Path) -> None:
    (marker,) = _compacted_session(tmp_path)["compact_markers"]
    assert marker["timestamp"].startswith("2026-11-16T09:01:40")


def test_compact_marker_validates_as_the_model(tmp_path: Path) -> None:
    from backend.models import CompactMarker, Message

    conv = _compacted_session(tmp_path)
    assert conv["compact_markers"]
    for marker in conv["compact_markers"]:
        CompactMarker.model_validate(marker)
    for msg in conv["chat_messages"]:
        Message.model_validate(msg)


def test_compact_marker_points_to_a_row_at_the_compaction_position(
    tmp_path: Path,
) -> None:
    conv = _compacted_session(tmp_path)
    (marker,) = conv["compact_markers"]
    ids = [m["uuid"] for m in conv["chat_messages"]]
    assert ids.count(marker["message_uuid"]) == 1
    at = ids.index(marker["message_uuid"])
    assert at == ids.index(_uuid_of(conv, "Question 2.")) + 1
    assert at == ids.index(_uuid_of(conv, "Answer 2.")) - 1


def test_messages_before_a_compaction_stay_visible(tmp_path: Path) -> None:
    conv = _compacted_session(tmp_path)
    assert len(conv["compact_markers"]) == 1
    assert _turn_texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
    ]


def test_replacement_history_is_not_a_turn(tmp_path: Path) -> None:
    conv = _compacted_session(tmp_path)
    assert len(conv["compact_markers"]) == 1
    assert not any("REPLACEMENT CONTEXT" in t for t in _texts(conv))


def test_compaction_does_not_set_incomplete(tmp_path: Path) -> None:
    _assert_complete(_compacted_session(tmp_path))


def test_context_compaction_item_alone_gives_one_marker(tmp_path: Path) -> None:
    r = _Rollout(TID)
    r.turn("turn-1", "Question 1.", "Answer 1.")
    r.add(_compaction_item(r.tick(), TID, "turn-1", "turn-1-compact"))
    conv = _project(_records(tmp_path, TID, r.lines))
    (marker,) = conv["compact_markers"]
    assert isinstance(marker["summary_text"], str)
    assert marker["message_uuid"] in [m["uuid"] for m in conv["chat_messages"]]
    _assert_complete(conv)


def test_compacted_record_alone_gives_one_marker(tmp_path: Path) -> None:
    r = _Rollout(TID)
    r.turn("turn-1", "Question 1.", "Answer 1.")
    r.add(_compacted(r.tick(), "Summary of turn 1."))
    r.turn("turn-2", "Question 2.", "Answer 2.")
    conv = _project(_records(tmp_path, TID, r.lines))
    (marker,) = conv["compact_markers"]
    assert marker["summary_text"] == "Summary of turn 1."
    _assert_complete(conv)


def test_legacy_compaction_gives_one_marker(tmp_path: Path) -> None:
    lines = [
        session_meta(_s(0), TID, cwd=CWD, history_mode="legacy"),
        turn_context(_s(1), "turn-1", cwd=CWD, model=MODEL),
        response_item(_s(2), message("user", input_text("Question 1."))),
        response_item(_s(3), message("assistant", output_text("Answer 1."))),
        _compacted(
            _s(4),
            "Legacy summary.",
            message("user", input_text("REPLACEMENT CONTEXT legacy.")),
        ),
        rollout_line(_s(4), "event_msg", {"type": "context_compacted"}),
        response_item(_s(5), message("user", input_text("Question 2."))),
        response_item(_s(6), message("assistant", output_text("Answer 2."))),
    ]
    conv = _project(_records(tmp_path, TID, lines))
    (marker,) = conv["compact_markers"]
    assert marker["summary_text"] == "Legacy summary."
    assert _turn_texts(conv) == ["Question 1.", "Answer 1.", "Question 2.", "Answer 2."]
    assert not any("REPLACEMENT CONTEXT" in t for t in _texts(conv))
    _assert_complete(conv)


def test_two_compactions_give_two_markers_in_order(tmp_path: Path) -> None:
    r = _Rollout(TID)
    r.turn("turn-1", "Question 1.", "Answer 1.")
    r.add(_compacted(r.tick(), "First summary."))
    r.add(_compaction_item(r.tick(), TID, "turn-1", "turn-1-compact"))
    r.turn("turn-2", "Question 2.", "Answer 2.")
    r.add(_compacted(r.tick(), "Second summary."))
    r.add(_compaction_item(r.tick(), TID, "turn-2", "turn-2-compact"))
    conv = _project(_records(tmp_path, TID, r.lines))
    assert [m["summary_text"] for m in conv["compact_markers"]] == [
        "First summary.",
        "Second summary.",
    ]
    ids = [m["uuid"] for m in conv["chat_messages"]]
    first, second = (ids.index(m["message_uuid"]) for m in conv["compact_markers"])
    assert first < ids.index(_uuid_of(conv, "Question 2.")) < second


def test_compact_marker_ids_are_stable_and_unique(tmp_path: Path) -> None:
    a, b = _compacted_session(tmp_path / "a"), _compacted_session(tmp_path / "b")
    assert a["compact_markers"] == b["compact_markers"]
    ids = [m["uuid"] for m in a["chat_messages"]]
    assert len(ids) == len(set(ids))
    assert uuid.UUID(a["compact_markers"][0]["message_uuid"])


# ---------------------------------------------------------------------------
# Rollback: rolled-back turns are hidden
# ---------------------------------------------------------------------------


def _rolled_back_session(tmp_path: Path, num_turns: int, *after: str) -> dict[str, Any]:
    r, _ = _three_turns()
    r.add(_rolled_back(r.tick(), num_turns))
    for n in after:
        r.turn(f"turn-{n}", f"Question {n}.", f"Answer {n}.")
    return _project(_records(tmp_path, TID, r.lines))


def test_rollback_hides_the_last_turn(tmp_path: Path) -> None:
    conv = _rolled_back_session(tmp_path, 1)
    assert _texts(conv) == ["Question 1.", "Answer 1.", "Question 2.", "Answer 2."]


def test_rollback_of_two_turns_hides_two_turns(tmp_path: Path) -> None:
    assert _texts(_rolled_back_session(tmp_path, 2)) == ["Question 1.", "Answer 1."]


def test_rollback_of_zero_turns_hides_nothing(tmp_path: Path) -> None:
    conv = _rolled_back_session(tmp_path, 0)
    assert len(_texts(conv)) == 6
    _assert_complete(conv)


def test_rollback_of_more_turns_than_exist_hides_all_earlier_turns(
    tmp_path: Path,
) -> None:
    assert _texts(_rolled_back_session(tmp_path, 99, "4")) == [
        "Question 4.",
        "Answer 4.",
    ]


def test_turns_after_a_rollback_are_visible(tmp_path: Path) -> None:
    conv = _rolled_back_session(tmp_path, 1, "4")
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
        "Question 4.",
        "Answer 4.",
    ]


def test_two_rollbacks_each_hide_their_turns(tmp_path: Path) -> None:
    r, _ = _three_turns()
    r.add(_rolled_back(r.tick(), 1))
    r.turn("turn-4", "Question 4.", "Answer 4.")
    r.add(_rolled_back(r.tick(), 1))
    r.turn("turn-5", "Question 5.", "Answer 5.")
    conv = _project(_records(tmp_path, TID, r.lines))
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
        "Question 5.",
        "Answer 5.",
    ]


def test_rollback_keeps_the_ids_of_the_kept_messages(tmp_path: Path) -> None:
    plain, _ = _three_turns()
    before = _project(_records(tmp_path / "plain", TID, plain.lines))
    after = _rolled_back_session(tmp_path / "rolled", 1)
    assert "Question 3." not in _texts(after)
    for text in ("Question 1.", "Answer 1.", "Question 2.", "Answer 2."):
        assert _uuid_of(after, text) == _uuid_of(before, text)


def test_rollback_does_not_set_incomplete(tmp_path: Path) -> None:
    _assert_complete(_rolled_back_session(tmp_path, 1))


def test_rollback_leaves_a_consistent_parent_chain(tmp_path: Path) -> None:
    conv = _rolled_back_session(tmp_path, 1, "4")
    msgs = conv["chat_messages"]
    assert len(msgs) == 6
    assert msgs[0]["parent_message_uuid"] is None
    for prev, msg in itertools.pairwise(msgs):
        assert msg["parent_message_uuid"] == prev["uuid"]
    assert conv["current_leaf_message_uuid"] == msgs[-1]["uuid"]


def test_legacy_rollback_hides_the_last_human_turn(tmp_path: Path) -> None:
    lines = [
        session_meta(_s(0), TID, cwd=CWD, history_mode="legacy"),
        turn_context(_s(1), "turn-1", cwd=CWD, model=MODEL),
        response_item(_s(2), message("user", input_text("Question 1."))),
        response_item(_s(3), message("assistant", output_text("Answer 1."))),
        response_item(_s(4), message("user", input_text("Question 2."))),
        response_item(_s(5), function_call("call_2", "shell", {"command": ["ls"]})),
        response_item(_s(6), function_call_output("call_2", "ROLLED BACK TOOL OUTPUT")),
        response_item(_s(7), message("assistant", output_text("Answer 2."))),
        _rolled_back(_s(8), 1),
        response_item(_s(9), message("user", input_text("Question 3."))),
        response_item(_s(10), message("assistant", output_text("Answer 3."))),
    ]
    conv = _project(_records(tmp_path, TID, lines))
    assert _texts(conv) == ["Question 1.", "Answer 1.", "Question 3.", "Answer 3."]
    assert not any(
        b.get("type") in ("tool_use", "tool_result")
        for m in conv["chat_messages"]
        for b in m["content"]
    )
    _assert_complete(conv)


# ---------------------------------------------------------------------------
# history_base prefix references (forks)
# ---------------------------------------------------------------------------


def _fork_of_parent(
    tmp_path: Path, **fork_meta: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The parent has turns 1-3. The fork inherits turns 1-2 and adds its own turn."""
    parent, starts = _three_turns(PARENT)
    fork = _Rollout(
        TID,
        history_base=parent.base_at(starts[2]),
        forked_from_id=PARENT,
        cwd="/home/fixture/fork",
        **fork_meta,
    )
    fork.turn("turn-f", "Fork question.", "Fork answer.")
    parent_records = _records(tmp_path, PARENT, parent.lines)
    conv = _project(
        _records(tmp_path, TID, fork.lines), load_base=_loader({PARENT: parent_records})
    )
    return conv, _project(parent_records)


def test_history_base_prefix_is_followed(tmp_path: Path) -> None:
    conv, _ = _fork_of_parent(tmp_path)
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
        "Fork question.",
        "Fork answer.",
    ]


def test_resolved_history_base_is_not_incomplete(tmp_path: Path) -> None:
    conv, _ = _fork_of_parent(tmp_path)
    _assert_complete(conv)


def test_history_base_keeps_the_childs_session_meta(tmp_path: Path) -> None:
    conv, _ = _fork_of_parent(tmp_path)
    assert conv["uuid"] == TID
    assert conv["project_path"] == "/home/fixture/fork"
    assert conv["parent_uuid"] == PARENT


def test_inherited_messages_get_the_childs_message_ids(tmp_path: Path) -> None:
    conv, parent = _fork_of_parent(tmp_path)
    fork_ids = {m["uuid"] for m in conv["chat_messages"]}
    assert fork_ids.isdisjoint(m["uuid"] for m in parent["chat_messages"])
    assert len(fork_ids) == len(conv["chat_messages"])


def test_attached_fork_still_shows_the_inherited_prefix(tmp_path: Path) -> None:
    # Orchestrator decision (2026-11-19): an attached fork is like any fork (principle 1).
    conv, _ = _fork_of_parent(
        tmp_path, attached_thread=True, forked_from_ordinal_exclusive=7
    )
    assert _texts(conv)[:4] == ["Question 1.", "Answer 1.", "Question 2.", "Answer 2."]
    _assert_complete(conv)


def test_history_base_cut_uses_the_ordinal_field_not_the_line_position(
    tmp_path: Path,
) -> None:
    # Codex ordinals can have gaps, and a damaged line has no ordinal.
    parent = _Rollout(PARENT)
    parent.lines.append(DAMAGED_LINE)
    parent.turn("turn-1", "Question 1.", "Answer 1.")
    parent.next = 20
    parent.turn("turn-2", "Question 2.", "Answer 2.")
    cut = parent.turn("turn-3", "Question 3.", "Answer 3.")
    assert cut == 23
    fork = _Rollout(TID, history_base=parent.base_at(cut), forked_from_id=PARENT)
    fork.turn("turn-f", "Fork question.", "Fork answer.")
    conv = _project(
        _records(tmp_path, TID, fork.lines),
        load_base=_loader({PARENT: _records(tmp_path, PARENT, parent.lines)}),
    )
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
        "Fork question.",
        "Fork answer.",
    ]


def test_history_base_chain_of_two_links(tmp_path: Path) -> None:
    grand, gstarts = _three_turns(GRANDPARENT)
    parent = _Rollout(
        PARENT, history_base=grand.base_at(gstarts[1]), forked_from_id=GRANDPARENT
    )
    parent.turn("turn-p1", "Parent question 1.", "Parent answer 1.")
    cut = parent.turn("turn-p2", "Parent question 2.", "Parent answer 2.")
    fork = _Rollout(TID, history_base=parent.base_at(cut), forked_from_id=PARENT)
    fork.turn("turn-f", "Fork question.", "Fork answer.")
    bases = {
        GRANDPARENT: _records(tmp_path, GRANDPARENT, grand.lines),
        PARENT: _records(tmp_path, PARENT, parent.lines),
    }
    conv = _project(_records(tmp_path, TID, fork.lines), load_base=_loader(bases))
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Parent question 1.",
        "Parent answer 1.",
        "Fork question.",
        "Fork answer.",
    ]
    _assert_complete(conv)
    assert conv["parent_uuid"] == PARENT


def test_history_base_projection_is_deterministic(tmp_path: Path) -> None:
    a, _ = _fork_of_parent(tmp_path / "a")
    b, _ = _fork_of_parent(tmp_path / "b")
    assert a == b


# ---------------------------------------------------------------------------
# Missing, cyclic, and too-deep bases
# ---------------------------------------------------------------------------


def _fork_lines(base_id: str, end: int = 4) -> list[dict[str, Any] | str]:
    fork = _Rollout(
        TID,
        history_base={
            "thread_id": base_id,
            "end_ordinal_exclusive": end,
            "end_byte_offset": 256,
        },
        forked_from_id=base_id,
    )
    fork.turn("turn-f", "Fork question.", "Fork answer.")
    return fork.lines


def test_missing_base_sets_incomplete_and_keeps_own_messages(tmp_path: Path) -> None:
    conv = _project(_records(tmp_path, TID, _fork_lines(PARENT)), load_base=_loader({}))
    assert conv["incomplete"] is True
    assert _reasons(conv, "history_base missing")
    assert _texts(conv) == ["Fork question.", "Fork answer."]


def test_history_base_without_load_base_is_missing(tmp_path: Path) -> None:
    conv = _project(_records(tmp_path, TID, _fork_lines(PARENT)))
    assert conv["incomplete"] is True
    assert _reasons(conv, "history_base missing")
    assert _texts(conv) == ["Fork question.", "Fork answer."]


def test_history_base_cycle_sets_incomplete(tmp_path: Path) -> None:
    # The fork's base is A; A's base is B; B's base is A.
    a = _Rollout(
        PARENT,
        history_base={
            "thread_id": GRANDPARENT,
            "end_ordinal_exclusive": 4,
            "end_byte_offset": 256,
        },
    )
    a.turn("turn-a", "A question.", "A answer.")
    b = _Rollout(
        GRANDPARENT,
        history_base={
            "thread_id": PARENT,
            "end_ordinal_exclusive": 4,
            "end_byte_offset": 256,
        },
    )
    b.turn("turn-b", "B question.", "B answer.")
    bases = {
        PARENT: _records(tmp_path, PARENT, a.lines),
        GRANDPARENT: _records(tmp_path, GRANDPARENT, b.lines),
    }
    conv = _project(
        _records(tmp_path, TID, _fork_lines(PARENT, end=a.next)),
        load_base=_loader(bases),
    )
    assert conv["incomplete"] is True
    assert _reasons(conv, "history_base cycle")
    assert _texts(conv)[-2:] == ["Fork question.", "Fork answer."]


def test_history_base_self_reference_is_a_cycle(tmp_path: Path) -> None:
    a = _Rollout(
        PARENT,
        history_base={
            "thread_id": PARENT,
            "end_ordinal_exclusive": 4,
            "end_byte_offset": 256,
        },
    )
    a.turn("turn-a", "A question.", "A answer.")
    bases = {PARENT: _records(tmp_path, PARENT, a.lines)}
    conv = _project(
        _records(tmp_path, TID, _fork_lines(PARENT, end=a.next)),
        load_base=_loader(bases),
    )
    assert conv["incomplete"] is True
    assert _reasons(conv, "history_base cycle")
    assert _texts(conv)[-2:] == ["Fork question.", "Fork answer."]


def _chain(tmp_path: Path, links: int) -> dict[str, Any]:
    """A rollout whose history_base chain has ``links`` links; each rollout has one turn."""
    ids = [f"019aa100-0000-7000-8000-{n:012d}" for n in range(links + 1)]
    bases: dict[str, list[Any]] = {}
    prev: _Rollout | None = None
    for n, rid in enumerate(ids):
        r = _Rollout(rid, history_base=prev.base_at() if prev else None)
        r.turn(f"turn-{n}", f"Question {n}.", f"Answer {n}.")
        if n < links:
            bases[rid] = _records(tmp_path, rid, r.lines)
        prev = r
    assert prev is not None
    return _project(_records(tmp_path, ids[-1], prev.lines), load_base=_loader(bases))


def test_history_base_chain_of_32_links_is_complete(tmp_path: Path) -> None:
    conv = _chain(tmp_path, 32)
    _assert_complete(conv)
    assert len(_texts(conv)) == 2 * 33
    assert _texts(conv)[:2] == ["Question 0.", "Answer 0."]
    assert _texts(conv)[-2:] == ["Question 32.", "Answer 32."]


def test_history_base_chain_of_33_links_is_too_deep(tmp_path: Path) -> None:
    conv = _chain(tmp_path, 33)
    assert conv["incomplete"] is True
    assert _reasons(conv, "history_base too deep")
    assert _texts(conv)[-2:] == ["Question 33.", "Answer 33."]


# ---------------------------------------------------------------------------
# Revert: a new rollout of the same thread over a cut of the old one
# ---------------------------------------------------------------------------


def _reverted(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The original rollout has turns 1-3. The revert keeps turns 1-2 and adds turn 3b."""
    original, starts = _three_turns(TID)
    # A revert keeps SessionMeta.id; the new file has its own rollout id.
    revert = _Rollout(TID, history_base=original.base_at(starts[2]))
    revert.turn("turn-3b", "Question 3b.", "Answer 3b.")
    original_records = _records(tmp_path, TID, original.lines)
    conv = _project(
        _records(tmp_path, REVERT_ROLLOUT, revert.lines),
        load_base=_loader({TID: original_records}),
    )
    return conv, _project(original_records)


def test_revert_hides_the_reverted_turns(tmp_path: Path) -> None:
    conv, _ = _reverted(tmp_path)
    assert _texts(conv) == [
        "Question 1.",
        "Answer 1.",
        "Question 2.",
        "Answer 2.",
        "Question 3b.",
        "Answer 3b.",
    ]


def test_revert_keeps_the_message_ids_of_the_kept_turns(tmp_path: Path) -> None:
    conv, original = _reverted(tmp_path)
    for text in ("Question 1.", "Answer 1.", "Question 2.", "Answer 2."):
        assert _uuid_of(conv, text) == _uuid_of(original, text)


def test_revert_is_complete_and_has_no_parent(tmp_path: Path) -> None:
    conv, _ = _reverted(tmp_path)
    _assert_complete(conv)
    assert conv["uuid"] == TID
    assert conv["parent_uuid"] is None


# ---------------------------------------------------------------------------
# Subagents: inherited context before subagent_history_start_ordinal
# ---------------------------------------------------------------------------


def test_subagent_hides_records_before_its_history_start(tmp_path: Path) -> None:
    child = _Rollout(
        TID,
        thread_source="subagent",
        parent_thread_id=PARENT,
        agent_nickname="Scout",
        subagent_history_start_ordinal=4,
    )
    # Ordinals 1-3: the parent's turn, copied in as model context.
    child.turn("turn-parent", "PARENT QUESTION.", "PARENT ANSWER.")
    assert child.turn("turn-child", "Child task.", "Child result.") == 4
    conv = _project(_records(tmp_path, TID, child.lines))
    assert _texts(conv) == ["Child task.", "Child result."]
    assert conv["parent_uuid"] == PARENT
    _assert_complete(conv)


# ---------------------------------------------------------------------------
# Every output still has the store shape
# ---------------------------------------------------------------------------


def test_slice_2_messages_validate_as_the_message_model(tmp_path: Path) -> None:
    from backend.models import Message

    convs = [
        _compacted_session(tmp_path / "c"),
        _rolled_back_session(tmp_path / "r", 1, "4"),
        _fork_of_parent(tmp_path / "f")[0],
        _reverted(tmp_path / "v")[0],
    ]
    for conv in convs:
        for msg in conv["chat_messages"]:
            Message.model_validate(msg)
