"""N12: the Codex 0.170 record kinds and item types (issue #70).

The orchestrator's contract for N12 (R1-R8, with the Q1-Q3 decisions):

* R1. Every record kind of Codex 0.170 is known. None adds an "unknown record
  type" reason or a warning. ``inter_agent_communication_metadata`` makes no
  message.
* Q1. Each ``inter_agent_communication`` record becomes one assistant message
  with a ``tool_use`` named ``agent_message`` and its ``tool_result``. The
  result is the plain ``content``, or "[Encrypted agent message]".
* R2. ``McpToolCall``, ``FileChange``, ``CollabAgentToolCall``,
  ``SubAgentActivity``, ``ImageView``, and ``Extension`` each become one
  assistant message with exactly a ``tool_use`` and its ``tool_result``, and no
  text block, so Show Tools hides and shows them like ``CommandExecution``.
* R3/R4. The tool names, inputs, and result texts below.
* R5. No warning for the six types, except one warning for an ``Extension``
  with a kind that N12 does not know (Q3).
* R6. An item with only ``type`` and ``id`` never crashes.
* R7. The shape report counts none of these as unknown or unsupported.
* R8. ``CODEX_PROJECTION_VERSION`` is 2.

The shapes follow ``openai/codex`` at ``rust-v0.170.0`` (``72f97c3``):
``protocol/src/items.rs``, ``history/src/rollout_payload.rs``, and
``ext/items/src``. All data is synthetic; no test reads a real ``~/.codex``.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as cf
from backend.tests._platform_home import patch_home

T0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)
TID = "019a9100-0000-7000-8000-0000000000aa"
CWD = "/home/fixture/proj-items"
TURN = "turn-1"
BASE64_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mN8fixcSecretB64"

NEW_RECORD_KINDS = {
    "dynamic_tools_updated": {"thread_id": TID, "tools": []},
    "inter_agent_communication_metadata": {"trigger_turn": True},
    "security_risk_score": {"score": 0.1},
    "retained_context": {"kind": "fixture"},
    "realtime_item": {"kind": "fixture"},
}


def _s(n: int) -> datetime:
    return T0 + timedelta(seconds=n)


# ---------------------------------------------------------------------------
# Items (shapes from codex-rs/protocol/src/items.rs and ext/items/src)
# ---------------------------------------------------------------------------


def mcp_item(item_id: str = "item-mcp", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "McpToolCall",
        "id": item_id,
        "server": "docs",
        "tool": "search",
        "arguments": {"query": "lexer tokens"},
        "status": "completed",
        "result": {"content": [{"type": "text", "text": "Found 3 pages about the lexer."}]},
    }
    item.update(extra)
    return item


def file_change_item(item_id: str = "item-patch", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "FileChange",
        "id": item_id,
        "changes": {
            "/home/fixture/proj-items/src/lexer.py": {"type": "add", "content": "TOKENS = []\n"},
            "/home/fixture/proj-items/src/old.py": {"type": "delete", "content": "x = 1\n"},
            "/home/fixture/proj-items/src/parser.py": {
                "type": "update",
                "unified_diff": "@@ -1 +1 @@\n-a\n+b\n",
                "move_path": None,
            },
        },
        "status": "completed",
        "stdout": "Success. Updated the files.",
        "stderr": "",
    }
    item.update(extra)
    return item


def collab_item(item_id: str = "item-collab", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "CollabAgentToolCall",
        "id": item_id,
        "tool": "spawn_agent",
        "status": "completed",
        "sender_thread_id": TID,
        "receiver_thread_ids": ["019a9100-0000-7000-8000-0000000000c1", "019a9100-0000-7000-8000-0000000000c2"],
        "receiver_agents": [],
        "prompt": "Count the tests in the parser module.",
        "model": "gpt-5.5-codex",
        "agents_states": {
            "019a9100-0000-7000-8000-0000000000c1": {"completed": "There are 42 parser tests."},
            "019a9100-0000-7000-8000-0000000000c2": {"errored": "The sandbox refused the command."},
        },
    }
    item.update(extra)
    return item


def subagent_item(item_id: str = "item-sub", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "SubAgentActivity",
        "id": item_id,
        "kind": "started",
        "agent_thread_id": "019a9100-0000-7000-8000-0000000000c1",
        "agent_path": "/root/explorer",
        "model": "gpt-5.5-codex",
        "reasoning_effort": "medium",
    }
    item.update(extra)
    return item


def image_view_item(item_id: str = "item-view", path: str = "file:///home/fixture/proj-items/shot.png") -> dict[str, Any]:
    return {"type": "ImageView", "id": item_id, "path": path}


def web_search_item(item_id: str = "item-web", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "Extension",
        "kind": "web.search",
        "id": item_id,
        "query": "rust serde flatten",
        "action": {"type": "search", "query": "rust serde flatten", "queries": None},
        "results": [
            {"title": "Struct flattening", "url": "https://serde.example/attr-flatten"},
            {"title": "Container attributes", "url": "https://serde.example/container-attrs"},
        ],
    }
    item.update(extra)
    return item


def sleep_item(item_id: str = "item-sleep") -> dict[str, Any]:
    return {"type": "Extension", "kind": "clock.sleep", "id": item_id, "durationMs": 1500}


def image_gen_item(item_id: str = "item-imagegen", **extra: Any) -> dict[str, Any]:
    item = {
        "type": "Extension",
        "kind": "image_gen.generation",
        "id": item_id,
        "status": "completed",
        "revisedPrompt": "A blue square on a white page",
        "result": BASE64_IMAGE,
        "savedPath": "/home/fixture/proj-items/out/blue-square.png",
    }
    item.update(extra)
    return item


def unknown_extension_item(kind: Any = "holo.projector", item_id: str = "item-holo") -> dict[str, Any]:
    return {"type": "Extension", "kind": kind, "id": item_id, "beamColor": "teal"}


def inter_agent(
    when: datetime, *, content: str = "Please count the parser tests.", comm_id: str | None = None, **extra: Any
) -> dict[str, Any]:
    """An ``inter_agent_communication`` record (ItemPayload flattens the item)."""
    payload: dict[str, Any] = {
        "author": "/root",
        "recipient": "/root/explorer",
        "other_recipients": [],
        "content": content,
        "trigger_turn": True,
    }
    if comm_id is not None:
        payload["id"] = comm_id
    payload.update(extra)
    return cf.rollout_line(when, "inter_agent_communication", payload)


SIX_TYPES = {
    "McpToolCall": mcp_item,
    "FileChange": file_change_item,
    "CollabAgentToolCall": collab_item,
    "SubAgentActivity": subagent_item,
    "ImageView": image_view_item,
    "Extension": web_search_item,
}

EXTENSIONS = {
    "web.search": web_search_item,
    "clock.sleep": sleep_item,
    "image_gen.generation": image_gen_item,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paginated(*extra: dict[str, Any] | str, tid: str = TID) -> list[dict[str, Any] | str]:
    """One clean paginated turn (a question and a reply), then ``extra``."""
    return [
        cf.session_meta(T0, tid, cwd=CWD, history_mode="paginated"),
        cf.turn_context(_s(1), TURN, cwd=CWD, model="gpt-5.5-codex"),
        cf.item_completed(_s(2), tid, TURN, cf.user_message_item("item-u1", "Hello there.")),
        cf.item_completed(_s(3), tid, TURN, cf.agent_message_item("item-r1", "Hi. How can I help?")),
        *extra,
    ]


def _legacy(*extra: dict[str, Any] | str, tid: str = TID) -> list[dict[str, Any] | str]:
    return [
        cf.session_meta(T0, tid, cwd=CWD, history_mode="legacy"),
        cf.turn_context(_s(1), TURN, cwd=CWD, model="gpt-5.4-codex"),
        cf.response_item(_s(2), cf.message("user", cf.input_text("Hello there."))),
        cf.response_item(_s(3), cf.message("assistant", cf.output_text("Hi. How can I help?"))),
        *extra,
    ]


def _project_lines(tmp_path: Path, lines: list[dict[str, Any] | str]) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    path = cf.write_rollout(tmp_path / f"rollout-2026-11-19T10-00-00-{TID}.jsonl", lines)
    read = read_rollout(path)
    return project_rollout(read.records, damaged_lines=read.damaged_lines)


def _project_item(tmp_path: Path, item: dict[str, Any]) -> dict[str, Any]:
    return _project_lines(tmp_path, _paginated(cf.item_completed(_s(4), TID, TURN, item)))


def _item_message(conv: dict[str, Any], item_id: str, turn_id: str = TURN) -> dict[str, Any]:
    from backend.codex.projection import message_uuid

    want = message_uuid(TID, turn_id=turn_id, item_id=item_id)
    (msg,) = [m for m in conv["chat_messages"] if m["uuid"] == want]
    return msg


def _pair(msg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The message's tool_use and tool_result; it must hold exactly these two blocks."""
    assert [b.get("type") for b in msg["content"]] == ["tool_use", "tool_result"], msg["content"]
    return msg["content"][0], msg["content"][1]


def _result_text(result: dict[str, Any]) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))


def _tool_pair(tmp_path: Path, item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    conv = _project_item(tmp_path, item)
    use, result = _pair(_item_message(conv, item["id"]))
    return conv, use, result


# ---------------------------------------------------------------------------
# R1: the record kinds of Codex 0.170
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NEW_RECORD_KINDS))
def test_a_new_record_kind_is_known_and_shows_nothing(tmp_path: Path, kind: str) -> None:
    conv = _project_lines(tmp_path, _paginated(cf.rollout_line(_s(4), kind, NEW_RECORD_KINDS[kind])))
    assert conv["incomplete_reasons"] == []
    assert conv["incomplete"] is False
    assert conv["warnings"] == []
    assert len(conv["chat_messages"]) == 2


def test_inter_agent_communication_does_not_set_incomplete(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4))))
    assert conv["incomplete"] is False
    assert conv["warnings"] == []


@pytest.mark.parametrize("kind", [*sorted(NEW_RECORD_KINDS), "inter_agent_communication"])
def test_the_new_record_kinds_are_in_the_known_set(kind: str) -> None:
    from backend.codex.projection import _KNOWN_RECORD_TYPES

    assert kind in _KNOWN_RECORD_TYPES


def test_new_record_kinds_in_legacy_history_set_no_incomplete(tmp_path: Path) -> None:
    lines = _legacy(*(cf.rollout_line(_s(4), k, p) for k, p in NEW_RECORD_KINDS.items()))
    conv = _project_lines(tmp_path, lines)
    assert conv["incomplete"] is False
    assert conv["warnings"] == []
    assert len(conv["chat_messages"]) == 2


# ---------------------------------------------------------------------------
# Q1: inter_agent_communication becomes an agent_message tool pair
# ---------------------------------------------------------------------------


def _agent_messages(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        m for m in conv["chat_messages"]
        if any(b.get("type") == "tool_use" and b.get("name") == "agent_message" for b in m["content"])
    ]


def test_inter_agent_communication_becomes_one_assistant_message(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), comm_id="comm-1")))
    (msg,) = _agent_messages(conv)
    assert msg["sender"] == "assistant"
    assert len(conv["chat_messages"]) == 3


def test_inter_agent_communication_is_a_tool_pair_with_no_text_block(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), comm_id="comm-1")))
    (msg,) = _agent_messages(conv)
    use, result = _pair(msg)
    assert result["tool_use_id"] == use["id"]


def test_inter_agent_communication_input_holds_author_recipient_and_trigger_turn(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), comm_id="comm-1")))
    use, _ = _pair(_agent_messages(conv)[0])
    assert use["input"]["author"] == "/root"
    assert use["input"]["recipient"] == "/root/explorer"
    assert use["input"]["trigger_turn"] is True


def test_inter_agent_communication_result_is_the_plain_content(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), content="Please count the parser tests.")))
    _, result = _pair(_agent_messages(conv)[0])
    assert _result_text(result) == "Please count the parser tests."


def test_encrypted_inter_agent_communication_shows_a_label_and_never_the_ciphertext(tmp_path: Path) -> None:
    record = inter_agent(_s(4), content="", encrypted_content="gAAAAfixture-agent-ciphertext")
    conv = _project_lines(tmp_path, _paginated(record))
    _, result = _pair(_agent_messages(conv)[0])
    assert _result_text(result) == "[Encrypted agent message]"
    assert "fixture-agent-ciphertext" not in json.dumps(conv, default=str)


def test_encrypted_inter_agent_communication_without_content_shows_the_label(tmp_path: Path) -> None:
    record = inter_agent(_s(4), encrypted_content="gAAAAfixture-agent-ciphertext")
    del record["payload"]["content"]
    conv = _project_lines(tmp_path, _paginated(record))
    _, result = _pair(_agent_messages(conv)[0])
    assert _result_text(result) == "[Encrypted agent message]"


def test_inter_agent_communication_without_an_id_keys_its_uuid_on_the_line(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    lines = _paginated(inter_agent(_s(4)))
    conv = _project_lines(tmp_path, lines)
    (msg,) = _agent_messages(conv)
    assert msg["uuid"] == message_uuid(TID, line_no=len(lines))


def test_inter_agent_communication_with_an_id_keys_its_uuid_on_the_id(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), comm_id="comm-1")))
    (msg,) = _agent_messages(conv)
    # Its turn is the latest turn_context before it.
    assert msg["uuid"] == message_uuid(TID, turn_id=TURN, item_id="comm-1")


def test_inter_agent_communication_turn_id_in_the_payload_wins(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    conv = _project_lines(tmp_path, _paginated(inter_agent(_s(4), comm_id="comm-1", turn_id="turn-9")))
    (msg,) = _agent_messages(conv)
    assert msg["uuid"] == message_uuid(TID, turn_id="turn-9", item_id="comm-1")


def test_inter_agent_communication_before_any_turn_has_no_turn(tmp_path: Path) -> None:
    from backend.codex.projection import message_uuid

    lines = [
        cf.session_meta(T0, TID, cwd=CWD, history_mode="paginated"),
        inter_agent(_s(1), comm_id="comm-1"),
        cf.turn_context(_s(2), TURN, cwd=CWD, model="gpt-5.5-codex"),
        cf.item_completed(_s(3), TID, TURN, cf.agent_message_item("item-r1", "Done.")),
    ]
    conv = _project_lines(tmp_path, lines)
    (msg,) = _agent_messages(conv)
    assert msg["uuid"] == message_uuid(TID, item_id="comm-1")


def test_inter_agent_communication_keeps_its_place_in_line_order(tmp_path: Path) -> None:
    lines = [
        cf.session_meta(T0, TID, cwd=CWD, history_mode="paginated"),
        cf.turn_context(_s(1), TURN, cwd=CWD, model="gpt-5.5-codex"),
        cf.item_completed(_s(2), TID, TURN, cf.user_message_item("item-u1", "Start the subagent.")),
        inter_agent(_s(3), content="Please count the parser tests."),
        cf.item_completed(_s(4), TID, TURN, cf.agent_message_item("item-r1", "The subagent is done.")),
    ]
    conv = _project_lines(tmp_path, lines)
    order = [m["text"] or _result_text(m["content"][-1]) for m in conv["chat_messages"]]
    assert order == ["Start the subagent.", "Please count the parser tests.", "The subagent is done."]


def test_inter_agent_communication_shows_in_legacy_history(tmp_path: Path) -> None:
    conv = _project_lines(tmp_path, _legacy(inter_agent(_s(4), content="Report back when done.")))
    (msg,) = _agent_messages(conv)
    _, result = _pair(msg)
    assert _result_text(result) == "Report back when done."
    assert conv["warnings"] == []


# ---------------------------------------------------------------------------
# R2: each of the six types is one assistant message with a tool pair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item_type", sorted(SIX_TYPES))
def test_item_type_is_one_assistant_message_with_a_tool_use_and_its_tool_result(
    tmp_path: Path, item_type: str
) -> None:
    item = SIX_TYPES[item_type]()
    conv, use, result = _tool_pair(tmp_path, item)
    assert len(conv["chat_messages"]) == 3
    assert _item_message(conv, item["id"])["sender"] == "assistant"
    assert use["id"] == item["id"]
    assert result["tool_use_id"] == item["id"]
    assert isinstance(use["input"], dict)
    assert all(c.get("type") == "text" for c in result["content"])


@pytest.mark.parametrize("item_type", sorted(SIX_TYPES))
def test_item_type_has_no_visible_text_when_show_tools_is_off(tmp_path: Path, item_type: str) -> None:
    item = SIX_TYPES[item_type]()
    conv = _project_item(tmp_path, item)
    msg = _item_message(conv, item["id"])
    assert not [b for b in msg["content"] if b.get("type") == "text"]
    assert msg["text"] == ""


@pytest.mark.parametrize("item_type", sorted(SIX_TYPES))
def test_item_type_adds_no_warning_and_keeps_the_session_complete(tmp_path: Path, item_type: str) -> None:
    conv = _project_item(tmp_path, SIX_TYPES[item_type]())
    assert conv["warnings"] == []
    assert conv["incomplete"] is False


@pytest.mark.parametrize("item_type", sorted(SIX_TYPES))
def test_item_type_never_shows_the_unsupported_label(tmp_path: Path, item_type: str) -> None:
    conv = _project_item(tmp_path, SIX_TYPES[item_type]())
    assert "Unsupported Codex item" not in json.dumps(conv, default=str)


@pytest.mark.parametrize("kind", sorted(EXTENSIONS))
def test_each_known_extension_kind_is_a_tool_pair_with_no_warning(tmp_path: Path, kind: str) -> None:
    item = EXTENSIONS[kind]()
    conv, use, result = _tool_pair(tmp_path, item)
    assert result["tool_use_id"] == use["id"] == item["id"]
    assert conv["warnings"] == []


def test_a_later_snapshot_of_a_tool_item_replaces_the_earlier_one(tmp_path: Path) -> None:
    first = mcp_item(status="inProgress", result=None)
    latest = mcp_item(result={"content": [{"type": "text", "text": "The final answer."}]})
    lines = _paginated(
        cf.item_completed(_s(4), TID, TURN, first),
        cf.item_completed(_s(5), TID, TURN, latest),
    )
    conv = _project_lines(tmp_path, lines)
    assert len(conv["chat_messages"]) == 3
    _, result = _pair(_item_message(conv, "item-mcp"))
    assert "The final answer." in _result_text(result)


def test_the_six_types_in_one_turn_keep_their_order_and_parent_chain(tmp_path: Path) -> None:
    items = [make() for make in SIX_TYPES.values()]
    lines = _paginated(*(cf.item_completed(_s(4 + i), TID, TURN, it) for i, it in enumerate(items)))
    conv = _project_lines(tmp_path, lines)
    msgs = conv["chat_messages"]
    assert [m["content"][0].get("id") for m in msgs[2:]] == [it["id"] for it in items]
    for prev, msg in itertools.pairwise(msgs):
        assert msg["parent_message_uuid"] == prev["uuid"]


# ---------------------------------------------------------------------------
# R3/R4: McpToolCall
# ---------------------------------------------------------------------------


def test_mcp_tool_call_name_is_server_dot_tool(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, mcp_item())
    assert use["name"] == "docs.search"


def test_mcp_tool_call_without_a_server_is_named_by_the_tool(tmp_path: Path) -> None:
    item = mcp_item()
    del item["server"]
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["name"] == "search"


def test_mcp_tool_call_without_a_server_and_a_tool_is_named_mcp(tmp_path: Path) -> None:
    item = mcp_item()
    del item["server"], item["tool"]
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["name"] == "mcp"


def test_mcp_tool_call_input_is_the_arguments(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, mcp_item())
    assert use["input"] == {"query": "lexer tokens"}


def test_mcp_tool_call_arguments_in_json_text_are_decoded(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, mcp_item(arguments='{"query": "lexer tokens"}'))
    assert use["input"] == {"query": "lexer tokens"}


def test_mcp_tool_call_result_is_the_text_of_its_content(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, mcp_item())
    assert "Found 3 pages about the lexer." in _result_text(result)


def test_mcp_tool_call_result_image_becomes_a_label(tmp_path: Path) -> None:
    content = [
        {"type": "text", "text": "Here is the chart."},
        {"type": "image", "data": BASE64_IMAGE, "mimeType": "image/png"},
    ]
    conv, _, result = _tool_pair(tmp_path, mcp_item(result={"content": content}))
    text = _result_text(result)
    assert "Here is the chart." in text
    assert "[Image]" in text
    assert BASE64_IMAGE not in json.dumps(conv, default=str)


def test_mcp_tool_call_structured_content_shows_when_no_text_block(tmp_path: Path) -> None:
    item = mcp_item(result={"content": [], "structuredContent": {"pages": ["lexer-overview"]}})
    _, _, result = _tool_pair(tmp_path, item)
    assert "lexer-overview" in _result_text(result)


def test_mcp_tool_call_error_shows_the_error_message(tmp_path: Path) -> None:
    item = mcp_item(status="failed", result=None, error={"message": "The docs server timed out."})
    _, _, result = _tool_pair(tmp_path, item)
    assert _result_text(result) == "The docs server timed out."


def test_mcp_tool_call_plain_string_error_shows_the_string(tmp_path: Path) -> None:
    item = mcp_item(status="failed", result=None, error="The docs server timed out.")
    _, _, result = _tool_pair(tmp_path, item)
    assert _result_text(result) == "The docs server timed out."


# ---------------------------------------------------------------------------
# R3/R4: FileChange
# ---------------------------------------------------------------------------


def test_file_change_name_is_apply_patch(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, file_change_item())
    assert use["name"] == "apply_patch"


def test_file_change_input_holds_the_changes_as_is(tmp_path: Path) -> None:
    item = file_change_item()
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["input"] == {"changes": item["changes"]}


def test_file_change_result_holds_stdout_and_stderr(tmp_path: Path) -> None:
    item = file_change_item(stdout="Success. Updated the files.", stderr="warning: CRLF line ends")
    _, _, result = _tool_pair(tmp_path, item)
    text = _result_text(result)
    assert "Success. Updated the files." in text
    assert "warning: CRLF line ends" in text


@pytest.mark.parametrize("status", ["failed", "declined"])
def test_file_change_failed_or_declined_shows_the_status(tmp_path: Path, status: str) -> None:
    _, _, result = _tool_pair(tmp_path, file_change_item(status=status, stdout="", stderr=""))
    assert f"[status: {status}]" in _result_text(result)


def test_file_change_completed_shows_no_status_label(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, file_change_item())
    assert "[status:" not in _result_text(result)


# ---------------------------------------------------------------------------
# R3/R4: CollabAgentToolCall
# ---------------------------------------------------------------------------


def test_collab_agent_tool_call_name_is_the_tool_value(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, collab_item())
    assert use["name"] == "spawn_agent"


def test_collab_agent_tool_call_without_a_tool_is_named_collab_agent(tmp_path: Path) -> None:
    item = collab_item()
    del item["tool"]
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["name"] == "collab_agent"


def test_collab_agent_tool_call_input_holds_prompt_receivers_and_model(tmp_path: Path) -> None:
    item = collab_item()
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["input"]["prompt"] == "Count the tests in the parser module."
    assert use["input"]["receiver_thread_ids"] == item["receiver_thread_ids"]
    assert use["input"]["model"] == "gpt-5.5-codex"


def test_collab_agent_tool_call_result_holds_each_agent_and_its_status(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, collab_item())
    text = _result_text(result)
    assert "019a9100-0000-7000-8000-0000000000c1" in text
    assert "019a9100-0000-7000-8000-0000000000c2" in text
    assert "completed" in text
    assert "errored" in text


def test_collab_agent_tool_call_result_holds_the_final_and_error_messages(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, collab_item())
    text = _result_text(result)
    assert "There are 42 parser tests." in text
    assert "The sandbox refused the command." in text


def test_collab_agent_tool_call_result_lines_have_the_fixed_form(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, collab_item())
    lines = _result_text(result).splitlines()
    assert "019a9100-0000-7000-8000-0000000000c1: completed: There are 42 parser tests." in lines
    assert "019a9100-0000-7000-8000-0000000000c2: errored: The sandbox refused the command." in lines


def test_collab_agent_tool_call_plain_string_status_shows(tmp_path: Path) -> None:
    item = collab_item(agents_states={"019a9100-0000-7000-8000-0000000000c1": "running"})
    _, _, result = _tool_pair(tmp_path, item)
    assert "019a9100-0000-7000-8000-0000000000c1: running" in _result_text(result).splitlines()


# ---------------------------------------------------------------------------
# R3/R4: SubAgentActivity
# ---------------------------------------------------------------------------


def test_subagent_activity_name_is_subagent(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, subagent_item())
    assert use["name"] == "subagent"


def test_subagent_activity_input_holds_kind_path_thread_and_model(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, subagent_item())
    assert use["input"]["kind"] == "started"
    assert use["input"]["agent_path"] == "/root/explorer"
    assert use["input"]["agent_thread_id"] == "019a9100-0000-7000-8000-0000000000c1"
    assert use["input"]["model"] == "gpt-5.5-codex"


def test_subagent_activity_result_holds_the_kind_and_the_path(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, subagent_item(kind="completed"))
    text = _result_text(result)
    assert "completed" in text
    assert "/root/explorer" in text


# ---------------------------------------------------------------------------
# R3/R4: ImageView
# ---------------------------------------------------------------------------


def test_image_view_name_is_view_image(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, image_view_item())
    assert use["name"] == "view_image"


def test_image_view_input_is_the_path(tmp_path: Path) -> None:
    item = image_view_item()
    _, use, _ = _tool_pair(tmp_path, item)
    assert use["input"] == {"path": item["path"]}


@pytest.mark.parametrize(
    "path",
    [
        "file:///home/fixture/proj-items/shot.png",
        "/home/fixture/proj-items/shot.png",
        "C:\\Users\\fixture\\proj-items\\shot.png",
    ],
)
def test_image_view_result_is_the_image_label_with_the_file_name(tmp_path: Path, path: str) -> None:
    _, _, result = _tool_pair(tmp_path, image_view_item(path=path))
    assert _result_text(result) == "[Image: shot.png]"


def test_image_view_unquotes_a_file_uri_before_it_takes_the_file_name(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, image_view_item(path="file:///home/fixture/proj-items/my%20pic.png"))
    assert _result_text(result) == "[Image: my pic.png]"


def test_image_view_never_opens_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_open = builtins.open
    target = tmp_path / "secret.png"
    target.write_bytes(b"\x89PNG fixture")

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        assert Path(str(file)).name != "secret.png", "the projection opened an ImageView path"
        return real_open(file, *args, **kwargs)

    path = cf.write_rollout(
        tmp_path / f"rollout-2026-11-19T10-00-00-{TID}.jsonl",
        _paginated(cf.item_completed(_s(4), TID, TURN, image_view_item(path=target.as_uri()))),
    )
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    records = read_rollout(path).records
    monkeypatch.setattr(builtins, "open", guarded_open)
    conv = project_rollout(records)
    _, result = _pair(_item_message(conv, "item-view"))
    assert _result_text(result) == "[Image: secret.png]"


# ---------------------------------------------------------------------------
# R3/R4: Extension
# ---------------------------------------------------------------------------


def test_web_search_extension_name_is_web_search(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, web_search_item())
    assert use["name"] == "web_search"


def test_web_search_extension_input_holds_the_query(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, web_search_item())
    assert use["input"]["query"] == "rust serde flatten"


def test_web_search_extension_result_holds_each_result_url(tmp_path: Path) -> None:
    _, _, result = _tool_pair(tmp_path, web_search_item())
    text = _result_text(result)
    assert "https://serde.example/attr-flatten" in text
    assert "https://serde.example/container-attrs" in text


def test_web_search_extension_without_results_has_an_empty_result(tmp_path: Path) -> None:
    item = web_search_item()
    del item["results"]
    _, _, result = _tool_pair(tmp_path, item)
    assert _result_text(result) == ""


def test_sleep_extension_name_is_sleep(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, sleep_item())
    assert use["name"] == "sleep"


def test_sleep_extension_input_holds_the_duration(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, sleep_item())
    assert use["input"]["durationMs"] == 1500


def test_image_generation_extension_name_is_image_generation(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, image_gen_item())
    assert use["name"] == "image_generation"


def test_image_generation_extension_input_holds_the_revised_prompt(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, image_gen_item())
    assert use["input"]["revisedPrompt"] == "A blue square on a white page"


def test_image_generation_extension_result_is_a_label_and_never_the_inline_data(tmp_path: Path) -> None:
    conv, _, result = _tool_pair(tmp_path, image_gen_item())
    assert "[Image: blue-square.png]" in _result_text(result)
    assert BASE64_IMAGE not in json.dumps(conv, default=str)


def test_image_generation_extension_without_a_saved_path_is_a_plain_image_label(tmp_path: Path) -> None:
    item = image_gen_item()
    del item["savedPath"]
    _, _, result = _tool_pair(tmp_path, item)
    assert "[Image]" in _result_text(result)


def test_image_generation_extension_with_a_file_id_is_a_label(tmp_path: Path) -> None:
    item = image_gen_item(fileId="file-fixture-1", result="")
    del item["savedPath"]
    _, _, result = _tool_pair(tmp_path, item)
    assert "[Image]" in _result_text(result)


# ---------------------------------------------------------------------------
# Q3: an Extension with an unknown kind
# ---------------------------------------------------------------------------


def test_unknown_extension_kind_is_a_tool_pair_named_by_the_kind(tmp_path: Path) -> None:
    _, use, result = _tool_pair(tmp_path, unknown_extension_item())
    assert use["name"] == "holo.projector"
    assert result["tool_use_id"] == "item-holo"


def test_unknown_extension_kind_input_holds_the_other_fields(tmp_path: Path) -> None:
    _, use, _ = _tool_pair(tmp_path, unknown_extension_item())
    assert use["input"]["beamColor"] == "teal"


def test_unknown_extension_kind_adds_one_warning_with_the_kind(tmp_path: Path) -> None:
    conv = _project_item(tmp_path, unknown_extension_item())
    assert conv["warnings"] == ["unknown Codex extension kind: holo.projector"]


def test_unknown_extension_kind_never_shows_the_unsupported_label(tmp_path: Path) -> None:
    conv = _project_item(tmp_path, unknown_extension_item())
    assert "Unsupported Codex item" not in json.dumps(conv, default=str)
    assert conv["incomplete"] is False


@pytest.mark.parametrize("kind", ["bad kind / with a space", "x" * 65, ""])
def test_unknown_extension_kind_with_an_unsafe_name_warns_without_the_name(tmp_path: Path, kind: str) -> None:
    conv = _project_item(tmp_path, unknown_extension_item(kind=kind))
    assert conv["warnings"] == ["unknown Codex extension kind"]


@pytest.mark.parametrize("kind", [None, 7, ["web.search"]])
def test_extension_without_a_string_kind_is_named_extension(tmp_path: Path, kind: Any) -> None:
    item = unknown_extension_item(kind=kind)
    if kind is None:
        del item["kind"]
    conv, use, _ = _tool_pair(tmp_path, item)
    assert use["name"] == "extension"
    assert conv["warnings"] == ["unknown Codex extension kind"]


# ---------------------------------------------------------------------------
# R6: a missing optional field never breaks the projection
# ---------------------------------------------------------------------------

MINIMAL_ITEMS = [
    {"type": "McpToolCall", "id": "item-min"},
    {"type": "FileChange", "id": "item-min"},
    {"type": "CollabAgentToolCall", "id": "item-min"},
    {"type": "SubAgentActivity", "id": "item-min"},
    {"type": "ImageView", "id": "item-min"},
    {"type": "Extension", "kind": "web.search", "id": "item-min"},
    {"type": "Extension", "kind": "clock.sleep", "id": "item-min"},
    {"type": "Extension", "kind": "image_gen.generation", "id": "item-min"},
]


@pytest.mark.parametrize("item", MINIMAL_ITEMS, ids=lambda i: i.get("kind") or i["type"])
def test_minimal_item_still_gives_the_tool_pair(tmp_path: Path, item: dict[str, Any]) -> None:
    conv, use, result = _tool_pair(tmp_path, item)
    assert result["tool_use_id"] == use["id"] == "item-min"
    assert isinstance(use["name"], str) and use["name"]
    assert conv["warnings"] == []


@pytest.mark.parametrize(
    "item",
    [
        mcp_item(arguments=None, result={"content": None}),
        mcp_item(result={"content": ["not a block", 3]}),
        mcp_item(error="not a dict"),
        file_change_item(changes=[{"path": "a.py", "kind": "add"}]),
        file_change_item(changes=None, stdout=None, stderr=None),
        collab_item(agents_states=None, receiver_thread_ids=None),
        collab_item(agents_states={"019a9100-0000-7000-8000-0000000000c1": {"completed": None}}),
        collab_item(agents_states=["not", "a", "dict"]),
        subagent_item(agent_path=None, model=None),
        image_view_item(path=None),  # type: ignore[arg-type]
        web_search_item(results=[None, "plain text", {"title": "no url"}]),
        image_gen_item(savedPath=None, revisedPrompt=None, result=None),
    ],
    ids=[
        "mcp-null-args", "mcp-odd-content", "mcp-odd-error", "patch-list", "patch-nulls",
        "collab-nulls", "collab-null-final", "collab-list-states", "sub-nulls", "view-null-path",
        "web-odd-results", "imagegen-nulls",
    ],
)
def test_odd_field_values_never_crash_and_keep_the_tool_pair(tmp_path: Path, item: dict[str, Any]) -> None:
    _, use, result = _tool_pair(tmp_path, item)
    assert result["tool_use_id"] == use["id"]
    assert isinstance(use["input"], dict)


def test_odd_inter_agent_communication_never_crashes(tmp_path: Path) -> None:
    record = cf.rollout_line(_s(4), "inter_agent_communication", {"content": None, "author": 5})
    conv = _project_lines(tmp_path, _paginated(record))
    (msg,) = _agent_messages(conv)
    _pair(msg)
    assert conv["incomplete"] is False


def test_the_projection_stays_json_serializable(tmp_path: Path) -> None:
    items = [make() for make in (*SIX_TYPES.values(), *EXTENSIONS.values())]
    items.append(unknown_extension_item())
    lines = _paginated(
        *(cf.item_completed(_s(4 + i), TID, TURN, {**it, "id": f"{it['id']}-{i}"}) for i, it in enumerate(items)),
        inter_agent(_s(40), comm_id="comm-1"),
    )
    conv = _project_lines(tmp_path, lines)
    json.dumps(conv)  # no default=: every value must be plain JSON
    assert len(conv["chat_messages"]) == 2 + len(items) + 1


# ---------------------------------------------------------------------------
# R7: the shape report
# ---------------------------------------------------------------------------


@pytest.fixture
def report_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A fake Codex home with one rollout that holds every N12 type and record kind."""
    from backend import config

    patch_home(monkeypatch, tmp_path / "user").mkdir()
    codex, data = tmp_path / "codex-home", tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data))
    config.get_settings.cache_clear()
    items = [make() for make in (*SIX_TYPES.values(), *EXTENSIONS.values())]
    lines = _paginated(
        *(cf.item_completed(_s(4 + i), TID, TURN, {**it, "id": f"{it['id']}-{i}"}) for i, it in enumerate(items)),
        *(cf.rollout_line(_s(30), k, p) for k, p in NEW_RECORD_KINDS.items()),
        inter_agent(_s(31), comm_id="comm-1"),
    )
    cf.write_rollout(cf.rollout_path(codex, T0, TID), lines)
    yield codex, data
    config.get_settings.cache_clear()


def _report(report_env: tuple[Path, Path]) -> dict[str, Any]:
    from cli.codex_report import build_report

    return build_report(*report_env)


def _nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if v}


def test_extension_is_a_known_item_type_of_the_report() -> None:
    from cli.codex_report import KNOWN_ITEM_TYPES

    assert "Extension" in KNOWN_ITEM_TYPES


def test_the_report_counts_none_of_the_n12_item_types_as_unknown(report_env: tuple[Path, Path]) -> None:
    report = _report(report_env)
    assert report["item_types"]["Extension"] == 4  # web.search is in SIX_TYPES and EXTENSIONS
    assert _nonzero(report["unknown_item_types"]) == {}


def test_the_report_counts_none_of_the_0_170_record_kinds_as_unknown(report_env: tuple[Path, Path]) -> None:
    report = _report(report_env)
    assert report["record_types"]["inter_agent_communication"] == 1
    assert _nonzero(report["unknown_record_types"]) == {}


def test_the_report_counts_no_unsupported_item_for_the_n12_types(report_env: tuple[Path, Path]) -> None:
    assert _report(report_env)["warnings"].get("unsupported_item", 0) == 0


def test_the_report_counts_no_incomplete_thread_for_the_n12_types(report_env: tuple[Path, Path]) -> None:
    assert _nonzero(_report(report_env)["incomplete"]["by_reason"]) == {}


def test_the_report_counts_an_unknown_extension_kind_as_other(
    report_env: tuple[Path, Path], tmp_path: Path
) -> None:
    codex, _ = report_env
    tid = "019a9100-0000-7000-8000-0000000000dd"
    lines = _paginated(cf.item_completed(_s(4), tid, TURN, unknown_extension_item()), tid=tid)
    cf.write_rollout(cf.rollout_path(codex, T0, tid), lines)
    report = _report(report_env)
    assert report["warnings"]["other"] == 1
    assert report["warnings"].get("unsupported_item", 0) == 0
    assert "holo.projector" not in json.dumps(report)


# ---------------------------------------------------------------------------
# R8: the projection version
# ---------------------------------------------------------------------------


def test_codex_projection_version_is_2() -> None:
    from backend.sources import codex

    assert codex.CODEX_PROJECTION_VERSION == 2
