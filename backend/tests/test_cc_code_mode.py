"""Issue #90: the Claude Code reader folds a Code Mode run into one block.

* The outer ``REPL`` call becomes one ``code_run`` block: the script, the
  inner calls in order, the returned value, stdout, stderr, the error, and
  the refused calls.
* Inner calls link by ``ranByToolUseId`` only (``link_source`` =
  ``transcript_field``), never by position: Claude Code writes them after
  the outer result.
* The inner call rows and the outer ``tool_result`` leave the message list;
  the parent chain skips them.
* A virtual call with a Code Mode id and no parent field stays a tool call,
  marked ``from_code_mode``.
* Old sessions without Code Mode read as before.

All data is synthetic (``cc_code_mode_fake``, shape of CC 2.1.331).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.tests import cc_code_mode_fake as fake

W = fake.WORDS


def _read(path: Path) -> dict[str, Any]:
    from backend.claude_code_reader import read_claude_code_conversation

    detail = read_claude_code_conversation(path)
    assert detail is not None
    return detail


@pytest.fixture
def conv(tmp_path: Path) -> dict[str, Any]:
    return _read(fake.write_session(tmp_path / "claude"))


@pytest.fixture
def sub(tmp_path: Path) -> dict[str, Any]:
    fake.write_session(tmp_path / "claude")
    path = tmp_path / "claude" / "projects" / fake.PROJECT / fake.SESSION / "subagents" / f"agent-{fake.AGENT_ID}.jsonl"
    return _read(path)


def _blocks(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for m in conv["chat_messages"] for b in m["content"]]


def _runs(conv: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {b["id"]: b for b in _blocks(conv) if b["type"] == "code_run"}


def test_every_repl_call_is_one_code_run_block(conv: dict[str, Any]) -> None:
    runs = _runs(conv)
    assert list(runs) == list(fake.ALL_RUNS)
    for block in runs.values():
        assert block["name"] == "REPL"
        assert block["code_run"]["language"] == "javascript"


def test_a_completed_run_has_its_script_calls_returned_value_and_stdout(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_DONE]["code_run"]
    assert W["code"] in run["code"]
    assert run["description"] == "List src and read app.py"
    assert run["status"] == "completed"
    assert W["returned"] in run["result"]
    assert run["stdout"] == W["stdout"]
    assert run["error"] is None
    calls = run["inner_calls"]
    assert [c["name"] for c in calls] == ["Bash", "Read"]
    assert W["inner_input"] in calls[0]["input"]["command"]
    # The content is in hostContent; the message content is empty.
    assert W["inner_output"] in calls[0]["result"]
    assert calls[1]["result"] == "1\tx = 1"
    for c in calls:
        assert c["code_run_parent"] == fake.RUN_DONE
        assert c["link_source"] == "transcript_field"
        assert c["is_error"] is False and c["refused"] is False


def test_a_failed_run_has_its_error_and_failed_status(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_FAILED]["code_run"]
    assert run["status"] == "failed"
    assert W["error"] in run["error"]
    assert [c["name"] for c in run["inner_calls"]] == ["Bash"]


def test_a_refused_call_is_marked_refused_and_the_run_lists_it(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_REFUSED]["code_run"]
    assert run["status"] == "completed"
    assert "Write" in run["refused"]
    (call,) = run["inner_calls"]
    assert call["refused"] is True and call["is_error"] is True
    assert "Permission denied" in call["result"]


def test_two_runs_in_one_turn_each_get_their_own_calls_by_parent_id_not_position(conv: dict[str, Any]) -> None:
    runs = _runs(conv)
    # The file holds the second run's inner call first.
    assert [c["input"]["command"] for c in runs[fake.RUN_TWIN_A]["code_run"]["inner_calls"]] == ["ls"]
    assert [c["input"]["command"] for c in runs[fake.RUN_TWIN_B]["code_run"]["inner_calls"]] == ["echo done"]
    twin_msgs = [m for m in conv["chat_messages"] if any(b.get("id") == fake.RUN_TWIN_A for b in m["content"])]
    assert len(twin_msgs) == 1
    assert [b["id"] for b in twin_msgs[0]["content"]] == [fake.RUN_TWIN_A, fake.RUN_TWIN_B]


def test_the_script_is_kept_whole_and_its_line_count_is_given(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_TWIN_B]["code_run"]
    assert run["line_count"] == 21
    assert run["code"].count("\n") == 20


def test_a_background_run_settles_from_its_poll_event(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_BACKGROUND]["code_run"]
    assert run["background"] is True
    assert run["status"] == "completed"
    assert W["background_result"] in run["result"]
    assert [c["input"]["command"] for c in run["inner_calls"]] == ["sleep 1 && ls"]


def test_a_background_run_that_never_settled_is_running(conv: dict[str, Any]) -> None:
    run = _runs(conv)[fake.RUN_UNSETTLED]["code_run"]
    assert run["background"] is True
    assert run["status"] == "running"
    assert run["result"] is None


def test_linked_inner_rows_and_the_outer_result_leave_the_message_list(conv: dict[str, Any]) -> None:
    ids = {b.get("id") for b in _blocks(conv)} | {b.get("tool_use_id") for b in _blocks(conv)}
    assert not any(i and i.startswith("toolu_cc_") and i != fake.LEGACY_CALL for i in ids)
    results = [b for b in _blocks(conv) if b["type"] == "tool_result"]
    assert [b["tool_use_id"] for b in results] == [fake.LEGACY_CALL]
    # The placeholder hostContent of the outer record never shows.
    assert not any(b.get("name") == "Bash" and b.get("id", "").startswith("toolu_01CM") for b in _blocks(conv))


def test_the_parent_chain_skips_the_removed_rows(conv: dict[str, Any]) -> None:
    from backend.store import resolve_active_branch

    msgs = conv["chat_messages"]
    branch = resolve_active_branch(msgs, conv["current_leaf_message_uuid"])
    assert [m["uuid"] for m in branch] == [m["uuid"] for m in msgs]


def test_a_code_mode_call_with_no_parent_field_is_a_tool_call_marked_from_code_mode(conv: dict[str, Any]) -> None:
    (use,) = [b for b in _blocks(conv) if b.get("id") == fake.LEGACY_CALL]
    assert use["type"] == "tool_use"
    assert use["from_code_mode"] is True
    assert not use.get("ambiguous_with")
    (result,) = [b for b in _blocks(conv) if b.get("tool_use_id") == fake.LEGACY_CALL]
    # The empty message content is filled from hostContent.
    assert W["legacy_output"] in str(result["content"])


def test_a_subagent_run_without_tool_use_result_reads_the_repl_output_text(sub: dict[str, Any]) -> None:
    (block,) = [b for b in _blocks(sub) if b["type"] == "code_run"]
    run = block["code_run"]
    assert block["id"] == fake.RUN_SUBAGENT
    assert W["subagent_code"] in run["code"]
    assert run["result"] == "app.py\nutil.py"
    assert run["status"] == "completed"
    assert [c["code_run_parent"] for c in run["inner_calls"]] == [fake.RUN_SUBAGENT]


def test_the_api_model_keeps_the_code_run_and_hides_empty_code_mode_fields(conv: dict[str, Any]) -> None:
    from backend.store import _parse_message

    by_id = {}
    for raw in conv["chat_messages"]:
        msg = _parse_message(raw)
        for b in msg.content:
            by_id[b.id] = b.model_dump()
    run = by_id[fake.RUN_DONE]
    assert run["code_run"]["inner_calls"][0]["link_source"] == "transcript_field"
    text_block = next(b.model_dump() for raw in conv["chat_messages"] for b in _parse_message(raw).content if b.type == "text")
    for key in ("code_run", "from_code_mode", "ambiguous_with"):
        assert key not in text_block
    assert by_id[fake.LEGACY_CALL]["from_code_mode"] is True


def test_a_session_without_code_mode_reads_as_before(tmp_path: Path) -> None:
    lines = [fake.user("u1", None, "hi"), fake.text_reply("t1", "u1", "hello")]
    conv = _read(fake.write_lines(tmp_path / "p" / "s.jsonl", lines))
    assert [b["type"] for m in conv["chat_messages"] for b in m["content"]] == ["text", "text"]
    assert all("code_run" not in b for m in conv["chat_messages"] for b in m["content"])
