"""N4: the Codex rollout reader and the slice 1 projection.

Plan: PLANS/2026.11.18-codex-source.md, §4.4 (the N4 part), §4.5 (message
ids), the "Write rules" of §4.2 (a reader ignores a last line with no
newline), and the N4 row of §6.1.

The API is the orchestrator's contract for N4:

* ``backend.codex.rollout.read_rollout(path) -> RolloutRead`` with
  ``records`` (``RolloutRecord``: ``line_no``, ``timestamp``, ``type``,
  ``payload``) and ``damaged_lines``.
* ``backend.codex.projection.project_rollout(records, *, thread_id=None,
  history_mode=None, current_ambiguous=False, damaged_lines=0) -> dict``.
* ``backend.codex.projection.message_uuid(thread_id, *, turn_id=None,
  item_id=None, line_no=None) -> str``.

The data comes from ``backend.tests.codex_fake``; the extra rollouts that
one test needs are built in that test. Record names that the plan does not
spell out (``compacted``, ``thread_rolled_back``, ``history_base``) follow
``openai/codex`` at commit ``f2d3086``. No test reads a real ``~/.codex``.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests.codex_fake import (
    DAMAGED_LINE,
    PNG_DATA_URL,
    THREAD_LEGACY,
    THREAD_PAGINATED,
    THREAD_SUBAGENT,
    agent_message_item,
    build_codex_home,
    encode_lines,
    function_call,
    function_call_output,
    input_text,
    item_completed,
    message,
    output_text,
    reasoning_item,
    response_item,
    rollout_line,
    session_meta,
    token_usage_record,
    turn_context,
    user_message_item,
    world_state,
    write_rollout,
)

ENCRYPTED_LABEL = "Encrypted reasoning (not readable)"

T0 = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
TID = "019a9000-0000-7000-8000-0000000000aa"
OTHER_TID = "019a9000-0000-7000-8000-0000000000bb"
CWD = "/home/fixture/proj-omega"


def _s(n: int) -> datetime:
    return T0 + timedelta(seconds=n)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def codex_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_codex_home(tmp_path_factory.mktemp("codex_home_n4"))


def _rollout_of(home: Path, thread_id: str) -> Path:
    (path,) = list(home.rglob(f"rollout-*-{thread_id}.jsonl*"))
    return path


def _project(path: Path, **kwargs: Any) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    read = read_rollout(path)
    kwargs.setdefault("damaged_lines", read.damaged_lines)
    return project_rollout(read.records, **kwargs)


def _write(
    tmp_path: Path,
    lines: list[dict[str, Any] | str],
    *,
    tid: str = TID,
    zst: bool = False,
) -> Path:
    suffix = ".jsonl.zst" if zst else ".jsonl"
    return write_rollout(tmp_path / f"rollout-2026-11-15T10-00-00-{tid}{suffix}", lines)


def _paginated(
    *extra: dict[str, Any] | str, tid: str = TID, **meta: Any
) -> list[dict[str, Any] | str]:
    """One clean paginated turn (a question and a reply), then ``extra``."""
    return [
        session_meta(T0, tid, cwd=CWD, history_mode="paginated", **meta),
        turn_context(_s(1), "turn-1", cwd=CWD, model="gpt-5.5-codex"),
        item_completed(
            _s(2), tid, "turn-1", user_message_item("item-u1", "Hello there.")
        ),
        item_completed(
            _s(3), tid, "turn-1", agent_message_item("item-r1", "Hi. How can I help?")
        ),
        *extra,
    ]


def _legacy(*extra: dict[str, Any] | str, tid: str = TID) -> list[dict[str, Any] | str]:
    """One clean legacy turn (a question and a reply), then ``extra``."""
    return [
        session_meta(T0, tid, cwd=CWD, history_mode="legacy"),
        turn_context(_s(1), "turn-1", cwd=CWD, model="gpt-5.4-codex"),
        response_item(_s(2), message("user", input_text("Hello there."))),
        response_item(_s(3), message("assistant", output_text("Hi. How can I help?"))),
        *extra,
    ]


def _blocks(
    conv: dict[str, Any], sender: str | None = None
) -> Iterator[tuple[dict, dict]]:
    for msg in conv["chat_messages"]:
        if sender is None or msg["sender"] == sender:
            for block in msg.get("content") or []:
                yield msg, block


def _blocks_of_type(conv: dict[str, Any], type_: str) -> list[dict]:
    return [b for _, b in _blocks(conv) if b.get("type") == type_]


def _block_texts(block: dict) -> list[str]:
    out = [v for k in ("text", "thinking") if isinstance(v := block.get(k), str)]
    for inner in block.get("content") or []:
        if isinstance(inner, dict):
            out.extend(_block_texts(inner))
    return out


def _all_texts(conv: dict[str, Any]) -> list[str]:
    """Each distinct text once per message: ``text`` often repeats a block."""
    out: list[str] = []
    for msg in conv["chat_messages"]:
        texts = [msg["text"]] if isinstance(msg.get("text"), str) else []
        for block in msg.get("content") or []:
            texts.extend(_block_texts(block))
        out.extend(dict.fromkeys(texts))
    return out


def _message_with_text(
    conv: dict[str, Any], text: str, sender: str | None = None
) -> dict:
    found = [
        msg
        for msg in conv["chat_messages"]
        if (sender is None or msg["sender"] == sender)
        and (
            msg.get("text") == text
            or any(text in _block_texts(b) for b in msg.get("content") or [])
        )
    ]
    assert len(found) == 1, f"expected one message with {text!r}, found {len(found)}"
    return found[0]


def _uuids(conv: dict[str, Any]) -> list[str]:
    return [m["uuid"] for m in conv["chat_messages"]]


# ---------------------------------------------------------------------------
# The rollout reader
# ---------------------------------------------------------------------------


def test_read_rollout_plain_and_zst_give_the_same_records(tmp_path: Path) -> None:
    from backend.codex.rollout import read_rollout

    lines = _paginated()
    plain = read_rollout(_write(tmp_path / "plain", lines))
    zst = read_rollout(_write(tmp_path / "zst", lines, zst=True))
    as_tuples = lambda r: [
        (x.line_no, x.timestamp, x.type, x.payload) for x in r.records
    ]
    assert as_tuples(plain) == as_tuples(zst)
    assert len(plain.records) == len(lines)


def test_read_rollout_record_fields(tmp_path: Path) -> None:
    from backend.codex.rollout import read_rollout

    read = read_rollout(_write(tmp_path, _paginated()))
    first = read.records[0]
    assert first.line_no == 1
    assert first.type == "session_meta"
    assert first.payload["id"] == TID
    assert first.timestamp == "2026-11-15T10:00:00.000Z"
    assert [r.line_no for r in read.records] == [1, 2, 3, 4]


def test_read_rollout_counts_damaged_lines_and_does_not_return_them(
    codex_home: Path,
) -> None:
    from backend.codex.rollout import read_rollout

    read = read_rollout(_rollout_of(codex_home, THREAD_PAGINATED))
    # The fixture has DAMAGED_LINE and "not json at all" on lines 14 and 15.
    assert read.damaged_lines == 2
    line_nos = [r.line_no for r in read.records]
    assert 14 not in line_nos and 15 not in line_nos
    # Line numbers are file lines: the record after the damage is line 16.
    assert line_nos[line_nos.index(13) + 1] == 16


def test_read_rollout_damaged_lines_in_a_zst_rollout(codex_home: Path) -> None:
    from backend.codex.rollout import read_rollout

    # The legacy fixture is .jsonl.zst with one "{broken" line.
    read = read_rollout(_rollout_of(codex_home, THREAD_LEGACY))
    assert read.damaged_lines == 1
    assert read.records[0].type == "session_meta"


def test_read_rollout_ignores_a_last_line_without_newline(tmp_path: Path) -> None:
    from backend.codex.rollout import read_rollout

    path = tmp_path / f"rollout-2026-11-15T10-00-00-{TID}.jsonl"
    partial = json.dumps(
        item_completed(
            _s(9), TID, "turn-1", agent_message_item("item-r9", "Still writ")
        )
    )
    path.write_bytes(encode_lines(_paginated()) + partial.encode())
    read = read_rollout(path)
    assert len(read.records) == 4
    assert read.damaged_lines == 0


def test_read_rollout_opens_nothing_for_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex.rollout import read_rollout

    paths = [
        _write(tmp_path / "p", _paginated()),
        _write(tmp_path / "z", _paginated(), zst=True),
    ]
    before = {
        p: (p.stat().st_mtime_ns, sorted(x.name for x in p.parent.iterdir()))
        for p in paths
    }
    write_opens: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(c in mode for c in "wax+"):
            write_opens.append(f"{file} {mode}")
        return real_open(file, mode, *args, **kwargs)

    def spy_os_open(file: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            write_opens.append(f"{file} flags={flags}")
        return real_os_open(file, flags, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", spy_open)
        m.setattr(io, "open", spy_open)
        m.setattr(os, "open", spy_os_open)
        for p in paths:
            read_rollout(p)

    assert write_opens == []
    assert before == {
        p: (p.stat().st_mtime_ns, sorted(x.name for x in p.parent.iterdir()))
        for p in paths
    }


# ---------------------------------------------------------------------------
# Session fields
# ---------------------------------------------------------------------------


def test_uuid_is_the_thread_id_from_session_meta(codex_home: Path) -> None:
    assert (
        _project(_rollout_of(codex_home, THREAD_PAGINATED))["uuid"] == THREAD_PAGINATED
    )


def test_thread_id_argument_sets_the_uuid(tmp_path: Path) -> None:
    conv = _project(_write(tmp_path, _paginated()), thread_id=OTHER_TID)
    assert conv["uuid"] == OTHER_TID


def test_session_model_is_the_model_of_the_last_turn(codex_home: Path) -> None:
    assert (
        _project(_rollout_of(codex_home, THREAD_PAGINATED))["model"] == "gpt-5.6-codex"
    )
    assert _project(_rollout_of(codex_home, THREAD_LEGACY))["model"] == "gpt-5.4-codex"


def test_each_message_has_the_model_of_its_turn(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    assert (
        _message_with_text(conv, "The parser lives in src/parser.py.", "assistant")[
            "model"
        ]
        == "gpt-5.5-codex"
    )
    assert (
        _message_with_text(conv, "Plan: split the lexer, then add tests.", "assistant")[
            "model"
        ]
        == "gpt-5.6-codex"
    )


def test_project_path_is_the_session_meta_cwd(tmp_path: Path) -> None:
    lines = _paginated()
    lines[1] = turn_context(_s(1), "turn-1", cwd=f"{CWD}/subdir", model="gpt-5.5-codex")
    assert _project(_write(tmp_path, lines))["project_path"] == CWD


def test_compacted_record_gives_one_compact_marker(tmp_path: Path) -> None:
    # Slice 2 (N10) rebuilds compaction; test_codex_history.py has its rules.
    lines = _paginated(
        rollout_line(_s(5), "compacted", {"message": "Summary of the earlier turns."})
    )
    (marker,) = _project(_write(tmp_path, lines))["compact_markers"]
    assert marker["summary_text"] == "Summary of the earlier turns."


def test_current_leaf_is_the_last_message(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    assert conv["current_leaf_message_uuid"] == conv["chat_messages"][-1]["uuid"]


def test_session_times_come_from_the_records(tmp_path: Path) -> None:
    conv = _project(_write(tmp_path, _paginated()))
    created = datetime.fromisoformat(str(conv["created_at"]))
    updated = datetime.fromisoformat(str(conv["updated_at"]))
    assert T0 <= created <= updated <= _s(3)


# ---------------------------------------------------------------------------
# History mode
# ---------------------------------------------------------------------------


def test_history_mode_is_paginated_when_a_record_is_item_completed(
    codex_home: Path,
) -> None:
    assert (
        _project(_rollout_of(codex_home, THREAD_PAGINATED))["history_mode"]
        == "paginated"
    )


def test_history_mode_is_legacy_without_item_completed(codex_home: Path) -> None:
    assert _project(_rollout_of(codex_home, THREAD_LEGACY))["history_mode"] == "legacy"


def test_history_mode_paginated_from_state_5_wins(tmp_path: Path) -> None:
    conv = _project(_write(tmp_path, _legacy()), history_mode="paginated")
    assert conv["history_mode"] == "paginated"
    # Paginated history skips response_item, so the legacy records give no turns.
    assert conv["chat_messages"] == []


# ---------------------------------------------------------------------------
# Paginated history
# ---------------------------------------------------------------------------


def test_paginated_user_message_becomes_a_human_message(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    _message_with_text(
        conv, "Refactor the parser module and list the files first.", "human"
    )
    _message_with_text(conv, "Now write the plan.", "human")


def test_paginated_agent_message_becomes_an_assistant_text_block(
    codex_home: Path,
) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    texts = [
        b["text"] for _, b in _blocks(conv, "assistant") if b.get("type") == "text"
    ]
    assert "Plan: split the lexer, then add tests." in texts


def test_paginated_later_snapshot_replaces_the_earlier_one(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    texts = [t for t in _all_texts(conv) if t.startswith("The parser lives in")]
    assert texts == ["The parser lives in src/parser.py."]


def test_paginated_snapshot_key_includes_the_turn_id(tmp_path: Path) -> None:
    lines = _paginated(
        turn_context(_s(4), "turn-2", cwd=CWD, model="gpt-5.5-codex"),
        item_completed(
            _s(5), TID, "turn-2", user_message_item("item-u1", "A second question.")
        ),
        item_completed(
            _s(6), TID, "turn-2", agent_message_item("item-r1", "A second answer.")
        ),
    )
    texts = _all_texts(_project(_write(tmp_path, lines)))
    for text in (
        "Hello there.",
        "Hi. How can I help?",
        "A second question.",
        "A second answer.",
    ):
        assert text in texts


def test_paginated_reasoning_summary_becomes_a_thinking_block(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    summary = "I should list the files before I change anything."
    thinking = [
        b
        for b in _blocks_of_type(conv, "thinking")
        if summary in (b.get("thinking") or "")
    ]
    assert len(thinking) == 1
    assert summary in thinking[0]["text"]


def test_paginated_reasoning_without_summary_shows_the_encrypted_label(
    tmp_path: Path,
) -> None:
    lines = _paginated(
        item_completed(_s(4), TID, "turn-1", reasoning_item("item-think"))
    )
    thinking = _blocks_of_type(_project(_write(tmp_path, lines)), "thinking")
    assert [(b["thinking"], b["text"]) for b in thinking] == [
        (ENCRYPTED_LABEL, ENCRYPTED_LABEL)
    ]


def test_paginated_command_execution_becomes_tool_use_and_tool_result(
    codex_home: Path,
) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    (use,) = [
        b
        for b in _blocks_of_type(conv, "tool_use")
        if "ls src" in json.dumps(b.get("input"))
    ]
    assert isinstance(use["input"], dict)
    assert use["id"] and use["name"]
    (result,) = [
        b
        for b in _blocks_of_type(conv, "tool_result")
        if b.get("tool_use_id") == use["id"]
    ]
    assert result["content"] == [{"type": "text", "text": "parser.py\nlexer.py\n"}]


def test_paginated_tool_blocks_are_in_assistant_messages(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    senders = {
        msg["sender"]
        for msg, b in _blocks(conv)
        if b.get("type") in ("thinking", "tool_use")
    }
    assert senders == {"assistant"}


def test_paginated_skips_response_items(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    texts = _all_texts(conv)
    # The fixture repeats both turns as response_item records.
    assert texts.count("Refactor the parser module and list the files first.") == 1
    assert texts.count("The parser lives in src/parser.py.") == 1
    assert not any(
        "<permissions instructions>" in t or "<environment_context>" in t for t in texts
    )


def test_paginated_skips_world_state_and_token_usage(tmp_path: Path) -> None:
    lines = _paginated(world_state(_s(4)), token_usage_record(_s(5), TID, "turn-1"))
    conv = _project(_write(tmp_path, lines))
    assert len(conv["chat_messages"]) == 2
    assert conv["warnings"] == []
    assert conv["incomplete"] is False


def test_unknown_item_type_becomes_a_fallback_block_and_one_warning(
    tmp_path: Path,
) -> None:
    item = {"type": "HologramCall", "id": "item-holo", "beam": "blue"}
    conv = _project(
        _write(tmp_path, _paginated(item_completed(_s(4), TID, "turn-1", item)))
    )
    texts = [b["text"] for b in _blocks_of_type(conv, "text")]
    assert "Unsupported Codex item: HologramCall" in texts
    assert len(conv["warnings"]) == 1


def test_unknown_item_type_does_not_set_incomplete(tmp_path: Path) -> None:
    item = {"type": "HologramCall", "id": "item-holo", "beam": "blue"}
    conv = _project(
        _write(tmp_path, _paginated(item_completed(_s(4), TID, "turn-1", item)))
    )
    assert conv["incomplete"] is False


def test_item_type_not_mapped_in_slice_1_becomes_a_fallback_block(
    tmp_path: Path,
) -> None:
    item = {"type": "Plan", "id": "item-plan", "text": "1. Split the lexer."}
    conv = _project(
        _write(tmp_path, _paginated(item_completed(_s(4), TID, "turn-1", item)))
    )
    texts = [b["text"] for b in _blocks_of_type(conv, "text")]
    assert "Unsupported Codex item: Plan" in texts
    assert len(conv["warnings"]) == 1


def test_fixture_fallback_blocks(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    texts = [b["text"] for b in _blocks_of_type(conv, "text")]
    assert "Unsupported Codex item: HologramCall" in texts
    assert "Unsupported Codex item: Plan" in texts


# ---------------------------------------------------------------------------
# Legacy history
# ---------------------------------------------------------------------------


def test_legacy_messages_become_human_and_assistant_messages(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    _message_with_text(conv, "Why does the build fail? Here is the error.", "human")
    _message_with_text(conv, "Line 3 of main.c needs a semicolon.", "assistant")


def test_legacy_tool_output_matches_its_call_by_call_id(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    uses = _blocks_of_type(conv, "tool_use")
    results = {b.get("tool_use_id"): b for b in _blocks_of_type(conv, "tool_result")}
    (first,) = [u for u in uses if "build.log" in json.dumps(u.get("input"))]
    (second,) = [u for u in uses if "Makefile" in json.dumps(u.get("input"))]
    assert first["id"] != second["id"]
    # The outputs come in the other order; a match by position swaps them.
    assert _block_texts(results[first["id"]]) == ["main.c:3: error: missing ';'"]
    assert _block_texts(results[second["id"]]) == ["all:\n\tcc -o app main.c\n"]


def test_legacy_tool_result_content_is_text_blocks(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    for result in _blocks_of_type(conv, "tool_result"):
        assert [c["type"] for c in result["content"]] == ["text"]


def test_legacy_json_string_arguments_are_decoded(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    inputs = [b["input"] for b in _blocks_of_type(conv, "tool_use")]
    assert {"command": ["cat", "build.log"]} in inputs
    assert {"command": ["cat", "Makefile"]} in inputs


def test_legacy_readable_reasoning_becomes_a_thinking_block(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    thinking = [b.get("thinking") for b in _blocks_of_type(conv, "thinking")]
    assert "The log points to main.c line 3." in thinking


def test_legacy_encrypted_reasoning_shows_the_label(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    labels = [
        b
        for b in _blocks_of_type(conv, "thinking")
        if b.get("thinking") == ENCRYPTED_LABEL
    ]
    assert len(labels) == 1
    assert labels[0]["text"] == ENCRYPTED_LABEL


def test_encrypted_reasoning_is_never_in_the_output(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    assert "fixture-encrypted-reasoning" not in json.dumps(conv, default=str)


def test_legacy_skips_instructions_environment_and_tool_declarations(
    codex_home: Path,
) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    blob = json.dumps(conv, default=str)
    assert "<permissions instructions>" not in blob
    assert "<environment_context>" not in blob
    assert "additional_tools" not in blob


def test_legacy_skips_world_state_and_token_usage(tmp_path: Path) -> None:
    lines = _legacy(world_state(_s(4)), token_usage_record(_s(5), TID, "turn-1"))
    conv = _project(_write(tmp_path, lines))
    assert len(conv["chat_messages"]) == 2
    assert conv["warnings"] == []
    assert conv["incomplete"] is False


def test_legacy_function_call_text_arguments_stay_usable(tmp_path: Path) -> None:
    lines = _legacy(
        response_item(_s(4), function_call("call_x", "shell", {"command": ["pwd"]})),
        response_item(
            _s(5), function_call_output("call_x", "/home/fixture/proj-omega\n")
        ),
    )
    conv = _project(_write(tmp_path, lines))
    (use,) = _blocks_of_type(conv, "tool_use")
    assert use["input"] == {"command": ["pwd"]}
    assert use["name"] == "shell"


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


def test_each_image_reference_becomes_a_label(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_PAGINATED))
    msg = _message_with_text(
        conv, "Refactor the parser module and list the files first.", "human"
    )
    labels = [
        b
        for b in msg["content"]
        if b.get("type") == "text"
        and b.get("text") != "Refactor the parser module and list the files first."
    ]
    # One data URL image and one local_image path.
    assert len(labels) == 2


def test_legacy_input_image_becomes_a_label(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_LEGACY))
    msg = _message_with_text(
        conv, "Why does the build fail? Here is the error.", "human"
    )
    labels = [
        b
        for b in msg["content"]
        if b.get("type") == "text"
        and b.get("text") != "Why does the build fail? Here is the error."
    ]
    assert len(labels) == 1


def test_image_data_is_not_copied_into_the_output(codex_home: Path) -> None:
    payload = PNG_DATA_URL.split(",", 1)[1]
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        assert payload not in json.dumps(
            _project(_rollout_of(codex_home, tid)), default=str
        )


def test_image_url_is_never_fetched_and_image_path_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    sentinel = tmp_path / "secret.png"
    sentinel.write_bytes(b"not for Explorer")
    user = user_message_item(
        "item-u2",
        "Look at these.",
        {"type": "image", "image_url": "https://images.example.invalid/cat.png"},
        {"type": "local_image", "path": str(sentinel)},
    )
    legacy_user = message(
        "user",
        input_text("And this."),
        {"type": "input_image", "image_url": "https://images.example.invalid/dog.png"},
    )
    paths = [
        _write(tmp_path / "p", _paginated(item_completed(_s(4), TID, "turn-1", user))),
        _write(tmp_path / "l", _legacy(response_item(_s(4), legacy_user))),
    ]
    reads = [read_rollout(p) for p in paths]

    network: list[str] = []
    opened: list[str] = []

    def no_network(*args: Any, **kwargs: Any) -> Any:
        network.append(repr(args))
        raise OSError("network is off in this test")

    real_open, real_os_open = builtins.open, os.open

    def spy_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_os_open(file: Any, *args: Any, **kwargs: Any) -> int:
        opened.append(str(file))
        return real_os_open(file, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(socket, "getaddrinfo", no_network)
        m.setattr(socket, "create_connection", no_network)
        m.setattr(socket.socket, "connect", no_network)
        m.setattr(builtins, "open", spy_open)
        m.setattr(io, "open", spy_open)
        m.setattr(os, "open", spy_os_open)
        convs = [project_rollout(r.records) for r in reads]

    assert network == []
    assert str(sentinel) not in opened
    assert "Look at these." in _all_texts(convs[0])
    assert "And this." in _all_texts(convs[1])


# ---------------------------------------------------------------------------
# Damaged lines and warnings
# ---------------------------------------------------------------------------


def test_clean_rollout_has_no_warnings(tmp_path: Path) -> None:
    assert _project(_write(tmp_path / "p", _paginated()))["warnings"] == []
    assert _project(_write(tmp_path / "l", _legacy()))["warnings"] == []


def test_damaged_lines_are_skipped_and_counted_as_warnings(tmp_path: Path) -> None:
    lines = _paginated()
    lines[3:3] = [DAMAGED_LINE, "not json at all"]
    conv = _project(_write(tmp_path, lines))
    assert [m["sender"] for m in conv["chat_messages"]] == ["human", "assistant"]
    warnings = conv["warnings"]
    assert warnings, "two damaged lines must add a warning"
    assert len(warnings) == 2 or any("2" in w for w in warnings)


def test_damaged_lines_argument_adds_a_warning(tmp_path: Path) -> None:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    records = read_rollout(_write(tmp_path, _paginated())).records
    assert project_rollout(records, damaged_lines=0)["warnings"] == []
    assert project_rollout(records, damaged_lines=1)["warnings"] != []


# ---------------------------------------------------------------------------
# The incomplete classifier (slice 1)
# ---------------------------------------------------------------------------


def _assert_incomplete(conv: dict[str, Any]) -> None:
    assert conv["incomplete"] is True
    reasons = conv["incomplete_reasons"]
    assert reasons and all(isinstance(r, str) and r for r in reasons)


def _assert_complete(conv: dict[str, Any]) -> None:
    assert conv["incomplete"] is False
    assert conv["incomplete_reasons"] == []


def test_clean_session_is_not_incomplete(codex_home: Path) -> None:
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        conv = _project(_rollout_of(codex_home, tid))
        assert conv["incomplete"] is False
        assert conv["incomplete_reasons"] == []


def test_compacted_record_does_not_set_incomplete(tmp_path: Path) -> None:
    # RolloutItem::Compacted, CompactedItem: codex-rs/history/src/lib.rs at f2d3086.
    record = rollout_line(
        _s(5),
        "compacted",
        {"message": "Summary of the earlier turns.", "replacement_history": []},
    )
    _assert_complete(_project(_write(tmp_path / "p", _paginated(record))))
    _assert_complete(_project(_write(tmp_path / "l", _legacy(record))))


def test_context_compaction_item_does_not_set_incomplete(tmp_path: Path) -> None:
    item = {"type": "ContextCompaction", "id": "item-compact"}
    _assert_complete(
        _project(
            _write(tmp_path, _paginated(item_completed(_s(5), TID, "turn-1", item)))
        )
    )


def test_rollback_does_not_set_incomplete(tmp_path: Path) -> None:
    # EventMsg::ThreadRolledBack: codex-rs/protocol/src/protocol.rs at f2d3086.
    record = rollout_line(
        _s(5), "event_msg", {"type": "thread_rolled_back", "num_turns": 1}
    )
    _assert_complete(_project(_write(tmp_path / "p", _paginated(record))))
    _assert_complete(_project(_write(tmp_path / "l", _legacy(record))))


def _with_history_base(lines: list[dict[str, Any] | str]) -> list[dict[str, Any] | str]:
    # SessionMeta.history_base, HistoryPosition: codex-rs/protocol/src/protocol.rs at f2d3086.
    meta = lines[0]
    assert isinstance(meta, dict)
    meta["payload"]["history_base"] = {
        "thread_id": OTHER_TID,
        "end_ordinal_exclusive": 7,
        "end_byte_offset": 2048,
    }
    return lines


def test_history_base_sets_incomplete(tmp_path: Path) -> None:
    # A revert replaces the history base (protocol.rs, HistoryPosition), so it is this case too.
    _assert_incomplete(_project(_write(tmp_path, _with_history_base(_paginated()))))


def test_subagent_with_history_base_sets_incomplete(tmp_path: Path) -> None:
    lines = _with_history_base(
        _paginated(
            thread_source="subagent", parent_thread_id=OTHER_TID, agent_nickname="Scout"
        )
    )
    _assert_incomplete(_project(_write(tmp_path, lines)))


def test_current_ambiguous_sets_incomplete(tmp_path: Path) -> None:
    _assert_incomplete(_project(_write(tmp_path, _paginated()), current_ambiguous=True))


def test_unknown_record_type_sets_incomplete(tmp_path: Path) -> None:
    # Not a RolloutItem kind (history/src/lib.rs); it can change the history.
    record = rollout_line(_s(5), "history_rewrite", {"drop_turns": ["turn-1"]})
    _assert_incomplete(_project(_write(tmp_path, _paginated(record))))


def test_fork_without_history_base_is_not_incomplete(tmp_path: Path) -> None:
    lines = _paginated()
    meta = lines[0]
    assert isinstance(meta, dict)
    meta["payload"]["forked_from_id"] = OTHER_TID
    conv = _project(_write(tmp_path, lines))
    assert conv["incomplete"] is False
    assert conv["incomplete_reasons"] == []


def test_subagent_without_history_base_is_not_incomplete(codex_home: Path) -> None:
    conv = _project(_rollout_of(codex_home, THREAD_SUBAGENT))
    assert conv["incomplete"] is False
    assert conv["incomplete_reasons"] == []


# ---------------------------------------------------------------------------
# Message ids (§4.5)
# ---------------------------------------------------------------------------


def test_message_uuid_is_a_deterministic_uuid5() -> None:
    from backend.codex.projection import message_uuid

    a = message_uuid(TID, turn_id="turn-1", item_id="item-u1")
    assert a == message_uuid(TID, turn_id="turn-1", item_id="item-u1")
    assert uuid.UUID(a).version == 5
    assert uuid.UUID(message_uuid(TID, line_no=3)).version == 5


def test_message_uuid_depends_on_each_part() -> None:
    from backend.codex.projection import message_uuid

    base = message_uuid(TID, turn_id="turn-1", item_id="item-u1")
    assert base != message_uuid(OTHER_TID, turn_id="turn-1", item_id="item-u1")
    assert base != message_uuid(TID, turn_id="turn-2", item_id="item-u1")
    assert base != message_uuid(TID, turn_id="turn-1", item_id="item-u2")
    assert message_uuid(TID, line_no=3) != message_uuid(TID, line_no=4)
    assert message_uuid(TID, line_no=3) != message_uuid(OTHER_TID, line_no=3)


def test_paginated_message_ids_come_from_thread_turn_and_item(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    conv = _project(_write(tmp_path, _paginated()))
    assert _uuids(conv) == [
        message_uuid(TID, turn_id="turn-1", item_id="item-u1"),
        message_uuid(TID, turn_id="turn-1", item_id="item-r1"),
    ]


def test_legacy_message_ids_come_from_thread_and_line_number(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    conv = _project(_write(tmp_path, _legacy()))
    # Lines 3 and 4 hold the user and the assistant message.
    assert _uuids(conv) == [message_uuid(TID, line_no=3), message_uuid(TID, line_no=4)]


def test_paginated_message_ids_do_not_move_when_lines_are_added(tmp_path: Path) -> None:
    plain = _project(_write(tmp_path / "a", _paginated()))
    lines = _paginated()
    lines[1:1] = [world_state(_s(1)), "not json at all"]
    shifted = _project(_write(tmp_path / "b", lines))
    assert _uuids(plain) == _uuids(shifted)


def test_message_ids_are_unique_in_a_session(codex_home: Path) -> None:
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        ids = _uuids(_project(_rollout_of(codex_home, tid)))
        assert len(ids) == len(set(ids))


def test_fork_that_reuses_item_ids_gets_other_message_ids(tmp_path: Path) -> None:
    parent = _project(_write(tmp_path / "a", _paginated()))
    fork = _project(_write(tmp_path / "b", _paginated(tid=OTHER_TID), tid=OTHER_TID))
    assert set(_uuids(parent)).isdisjoint(_uuids(fork))


def test_legacy_sessions_with_the_same_lines_get_other_message_ids(
    tmp_path: Path,
) -> None:
    a = _project(_write(tmp_path / "a", _legacy()))
    b = _project(_write(tmp_path / "b", _legacy(tid=OTHER_TID), tid=OTHER_TID))
    assert set(_uuids(a)).isdisjoint(_uuids(b))


def test_projection_is_deterministic(codex_home: Path) -> None:
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        path = _rollout_of(codex_home, tid)
        assert json.dumps(_project(path), default=str) == json.dumps(
            _project(path), default=str
        )


# ---------------------------------------------------------------------------
# The store shape of each message
# ---------------------------------------------------------------------------


def test_messages_have_the_store_shape(codex_home: Path) -> None:
    keys = {
        "uuid",
        "sender",
        "text",
        "content",
        "created_at",
        "updated_at",
        "parent_message_uuid",
        "model",
    }
    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        for msg in _project(_rollout_of(codex_home, tid))["chat_messages"]:
            assert keys <= msg.keys()
            assert msg["sender"] in ("human", "assistant")
            assert isinstance(msg["content"], list)
            assert msg.get("attachments", []) == [] and msg.get("files", []) == []


def test_messages_validate_as_the_message_model(codex_home: Path) -> None:
    from backend.models import Message

    for tid in (THREAD_PAGINATED, THREAD_LEGACY):
        for msg in _project(_rollout_of(codex_home, tid))["chat_messages"]:
            Message.model_validate(msg)
