"""C1 (issue #84): Codex tool calls that have no ``item_completed`` item.

In paginated history the projection builds the messages from
``item_completed`` items and skips ``response_item`` records. Codex writes no
item for some tool calls (a freeform ``apply_patch`` that fails before
``emitter.begin``, the outer code-mode ``exec``, an unknown tool, a hook
block), so their input and output were not in Explorer at all: the viewer
did not show them and search did not find them.

The rule (fixed by the orchestrator, wave 9 brief, C1):

* In paginated mode, a ``response_item`` tool call (``function_call``,
  ``custom_tool_call``, ``local_shell_call``) whose ``call_id`` equals no
  ``item_completed`` item's ``id`` in the rollout becomes a tool call: a
  ``tool_use`` with its name and input, and a ``tool_result`` with the
  matching output (by ``call_id``), as the legacy mode builds them.
* A call that matches an item by id adds nothing.
* It sits where its ``response_item`` record is in the rollout.
* An output without its call, or a call without its output, shows what exists.
* It is a tool call like any other: Show Tools hides and shows it, and search
  finds its input and output only with tools on.
* ``CODEX_PROJECTION_VERSION`` goes up by one (3 -> 4).
* Issue #90 changed one rule: the outer code-mode ``exec`` is a
  ``code_run`` block that holds its nested command
  (``test_codex_code_mode.py``), so it is not an orphan tool call here.
* Compaction, rollback, and ``history_base`` apply to these calls as to items.

All data is synthetic (``codex_fake.orphan_tool_calls_lines``); no test reads
a real ``~/.codex``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as cf

TID = cf.THREAD_ORPHAN_CALLS
W = cf.ORPHAN_WORDS

#: Each orphan call or output: (call id, tool name or None, input word or None, output word or None).
ORPHANS = (
    (cf.ORPHAN_PATCH, "apply_patch", W["patch_input"], W["patch_output"]),
    (cf.ORPHAN_UNKNOWN, "frobnicate", W["unknown_input"], W["unknown_output"]),
    (cf.ORPHAN_LONE_OUTPUT, None, None, W["lone_output"]),
    (cf.ORPHAN_NO_OUTPUT, "shell", W["no_output_input"], None),
)
ORPHAN_IDS = [o[0] for o in ORPHANS]

#: Each word that is only in the input or output of an orphan, and the call id
#: of the message that holds it. Words of item-based calls are found today.
TOOL_WORDS = (
    *((o[2], o[0]) for o in ORPHANS if o[2]),
    *((o[3], o[0]) for o in ORPHANS if o[3]),
)
TOOL_WORD_IDS = [w for w, _ in TOOL_WORDS]

PATHS = ("fts-snippet", "fts-full", "linear")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(tmp_path: Path, lines: list[dict[str, Any] | str], **kwargs: Any) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    path = cf.write_rollout(tmp_path / f"rollout-2026-11-19T11-00-00-{TID}.jsonl", lines)
    read = read_rollout(path)
    return project_rollout(read.records, damaged_lines=read.damaged_lines, **kwargs)


@pytest.fixture
def conv(tmp_path: Path) -> dict[str, Any]:
    return _project(tmp_path, cf.orphan_tool_calls_lines())


def _tool_ids(msg: dict[str, Any]) -> list[str]:
    """The call ids that a message's tool blocks name."""
    return [
        b.get("id") if b.get("type") in ("tool_use", "code_run") else b.get("tool_use_id")
        for b in msg["content"]
        if b.get("type") in ("tool_use", "tool_result", "code_run")
    ]


def _by_call(conv: dict[str, Any], call_id: str) -> dict[str, Any]:
    """The one message whose tool blocks name ``call_id``."""
    found = [m for m in conv["chat_messages"] if call_id in _tool_ids(m)]
    assert len(found) == 1, f"{call_id}: {len(found)} messages hold it, want 1"
    return found[0]


def _result_text(block: dict[str, Any]) -> str:
    return "\n".join(c.get("text", "") for c in block.get("content") or [] if isinstance(c, dict))


def _key(msg: dict[str, Any]) -> str:
    """A tool message by its first call id; any other message by its text."""
    ids = _tool_ids(msg)
    return ids[0] if ids else f"text:{msg['text']}"


def _s(n: int) -> datetime:
    return cf.ORPHAN_STARTED + timedelta(seconds=n)


# ---------------------------------------------------------------------------
# The viewer messages (the projection)
# ---------------------------------------------------------------------------


def test_a_failed_freeform_apply_patch_is_a_tool_use_and_its_tool_result(conv: dict[str, Any]) -> None:
    msg = _by_call(conv, cf.ORPHAN_PATCH)
    assert [b["type"] for b in msg["content"]] == ["tool_use", "tool_result"]
    use, result = msg["content"]
    assert use["id"] == cf.ORPHAN_PATCH
    assert use["name"] == "apply_patch"
    assert isinstance(use["input"], dict)
    assert W["patch_input"] in json.dumps(use["input"])
    assert result["tool_use_id"] == cf.ORPHAN_PATCH
    assert "apply_patch verification failed" in _result_text(result)
    assert W["patch_output"] in _result_text(result)


def test_the_outer_code_mode_exec_call_is_a_code_run_that_holds_its_nested_command(conv: dict[str, Any]) -> None:
    # Issue #90: the nested item runs while the cell is alive, so it links to it.
    (block,) = _by_call(conv, cf.ORPHAN_EXEC)["content"]
    assert (block["type"], block["name"]) == ("code_run", "exec")
    run = block["code_run"]
    assert W["exec_input"] in run["code"]
    assert W["exec_output"] in run["result"]
    (call,) = run["inner_calls"]
    assert (call["id"], call["name"]) == (cf.NESTED_EXEC_ITEM, "shell")
    assert W["nested_output"] in call["result"]


def test_an_unknown_function_call_has_its_decoded_arguments_and_the_unsupported_output(
    conv: dict[str, Any],
) -> None:
    use, result = _by_call(conv, cf.ORPHAN_UNKNOWN)["content"]
    assert use["name"] == "frobnicate"
    assert use["input"] == {"level": W["unknown_input"]}
    assert _result_text(result) == f"unsupported call: frobnicate ({W['unknown_output']})"


def test_a_call_without_its_output_is_a_tool_use_alone(conv: dict[str, Any]) -> None:
    msg = _by_call(conv, cf.ORPHAN_NO_OUTPUT)
    assert [b["type"] for b in msg["content"]] == ["tool_use"]
    assert msg["content"][0]["name"] == "shell"
    assert W["no_output_input"] in json.dumps(msg["content"][0]["input"])


def test_an_output_without_its_call_is_a_tool_result_alone(conv: dict[str, Any]) -> None:
    msg = _by_call(conv, cf.ORPHAN_LONE_OUTPUT)
    assert [b["type"] for b in msg["content"]] == ["tool_result"]
    assert msg["content"][0]["tool_use_id"] == cf.ORPHAN_LONE_OUTPUT
    assert W["lone_output"] in _result_text(msg["content"][0])


def test_every_tool_call_shows_exactly_once(conv: dict[str, Any]) -> None:
    ids = [cf.ORPHAN_PATCH, cf.MATCHED_SHELL, cf.MATCHED_PATCH, cf.ORPHAN_UNKNOWN, cf.ORPHAN_NO_OUTPUT]
    uses = [b["id"] for m in conv["chat_messages"] for b in m["content"] if b.get("type") == "tool_use"]
    assert sorted(uses) == sorted(ids)


def test_a_matched_call_keeps_its_item_and_adds_nothing(conv: dict[str, Any]) -> None:
    # The FileChange item wins over the custom_tool_call: its input is the changes.
    use, result = _by_call(conv, cf.MATCHED_PATCH)["content"]
    assert set(use["input"]) == {"changes"}
    assert W["matched_patch_output"] in _result_text(result)
    assert len(conv["chat_messages"]) == 9


def test_each_message_sits_where_its_first_record_is_in_the_rollout(conv: dict[str, Any]) -> None:
    assert [_key(m) for m in conv["chat_messages"]] == [
        "text:Fix the lexer tokens.",
        cf.ORPHAN_PATCH,
        cf.ORPHAN_EXEC,
        cf.MATCHED_SHELL,
        cf.MATCHED_PATCH,
        cf.ORPHAN_UNKNOWN,
        cf.ORPHAN_LONE_OUTPUT,
        cf.ORPHAN_NO_OUTPUT,
        f"text:The lexer is fixed ({W['message']}).",
    ]


def test_the_parent_chain_runs_through_the_orphan_calls(conv: dict[str, Any]) -> None:
    msgs = conv["chat_messages"]
    assert [m["parent_message_uuid"] for m in msgs] == [None, *(m["uuid"] for m in msgs[:-1])]
    assert _by_call(conv, cf.ORPHAN_PATCH)["parent_message_uuid"] == msgs[0]["uuid"]


@pytest.mark.parametrize("call_id", ORPHAN_IDS)
def test_an_orphan_call_has_no_text_so_show_tools_hides_it(conv: dict[str, Any], call_id: str) -> None:
    msg = _by_call(conv, call_id)
    assert not [b for b in msg["content"] if b.get("type") == "text"]
    assert msg["text"] == ""


@pytest.mark.parametrize("call_id", ORPHAN_IDS)
def test_an_orphan_call_is_an_assistant_message_with_the_turn_model(conv: dict[str, Any], call_id: str) -> None:
    msg = _by_call(conv, call_id)
    assert msg["sender"] == "assistant"
    assert msg["model"] == cf.ORPHAN_MODEL


def test_an_orphan_call_starts_at_its_call_and_ends_at_its_output(conv: dict[str, Any]) -> None:
    msg = _by_call(conv, cf.ORPHAN_PATCH)
    assert (msg["created_at"], msg["updated_at"]) == (cf.ts(_s(3)), cf.ts(_s(4)))
    lone = _by_call(conv, cf.ORPHAN_LONE_OUTPUT)
    assert lone["created_at"] == lone["updated_at"] == cf.ts(_s(14))


def test_the_message_ids_are_unique_and_the_same_on_every_projection(tmp_path: Path) -> None:
    first = _project(tmp_path / "a", cf.orphan_tool_calls_lines())
    second = _project(tmp_path / "b", cf.orphan_tool_calls_lines())
    ids = [m["uuid"] for m in first["chat_messages"]]
    assert len(set(ids)) == len(ids) == 9
    assert ids == [m["uuid"] for m in second["chat_messages"]]


def test_an_orphan_message_id_is_keyed_on_its_turn_and_call_id(conv: dict[str, Any]) -> None:
    from backend.codex.projection import message_uuid

    for call_id in ORPHAN_IDS:
        assert _by_call(conv, call_id)["uuid"] == message_uuid(TID, turn_id=cf.ORPHAN_TURN, item_id=call_id)


def test_an_output_without_its_call_adds_the_legacy_warning(conv: dict[str, Any]) -> None:
    assert conv["warnings"] == [f"tool output without a call: {cf.ORPHAN_LONE_OUTPUT}"]


def test_orphan_calls_keep_the_session_complete(conv: dict[str, Any]) -> None:
    assert (conv["incomplete"], conv["incomplete_reasons"]) == (False, [])
    assert not [w for w in conv["warnings"] if "nsupported" in w]
    assert _by_call(conv, cf.ORPHAN_PATCH)


# ---------------------------------------------------------------------------
# Slice 2 history rules: rollback, compaction, history_base
# ---------------------------------------------------------------------------


def _turn_with_orphan(turn_id: str, call_id: str, n: int, tid: str = TID) -> list[dict[str, Any]]:
    cwd = "/home/fixture/proj-omega"
    return [
        cf.turn_context(_s(n), turn_id, cwd=cwd, model=cf.ORPHAN_MODEL),
        cf.item_completed(_s(n + 1), tid, turn_id, cf.user_message_item(f"{turn_id}-u", f"Question {turn_id}.")),
        cf.response_item(_s(n + 2), cf.custom_tool_call(call_id, "apply_patch", f"bad patch {call_id}")),
        cf.response_item(_s(n + 3), cf.custom_tool_call_output(call_id, f"invalid patch input {call_id}")),
        cf.item_completed(_s(n + 4), tid, turn_id, cf.agent_message_item(f"{turn_id}-r", f"Answer {turn_id}.")),
    ]


def _all_tool_ids(conv: dict[str, Any]) -> list[str]:
    return [i for m in conv["chat_messages"] for i in dict.fromkeys(_tool_ids(m))]


def test_a_rollback_hides_the_orphan_calls_of_the_dropped_turn(tmp_path: Path) -> None:
    lines = [
        cf.session_meta(_s(0), TID, cwd="/home/fixture/proj-omega", history_mode="paginated"),
        *_turn_with_orphan("turn-r1", "call_r1", 10),
        *_turn_with_orphan("turn-r2", "call_r2", 20),
        cf.rollout_line(_s(30), "event_msg", {"type": "thread_rolled_back", "num_turns": 1}),
    ]
    conv = _project(tmp_path, lines)
    assert _all_tool_ids(conv) == ["call_r1"]
    assert [m["text"] for m in conv["chat_messages"] if m["text"]] == ["Question turn-r1.", "Answer turn-r1."]


def test_an_output_in_a_later_turn_still_attaches_to_its_call(tmp_path: Path) -> None:
    cwd = "/home/fixture/proj-omega"
    lines = [
        cf.session_meta(_s(0), TID, cwd=cwd, history_mode="paginated"),
        cf.turn_context(_s(1), "turn-l1", cwd=cwd, model=cf.ORPHAN_MODEL),
        cf.response_item(_s(2), cf.custom_tool_call("call_late", "apply_patch", "late patch")),
        cf.turn_context(_s(3), "turn-l2", cwd=cwd, model=cf.ORPHAN_MODEL),
        cf.response_item(_s(4), cf.custom_tool_call_output("call_late", "late output")),
    ]
    conv = _project(tmp_path, lines)
    msg = _by_call(conv, "call_late")
    assert [b["type"] for b in msg["content"]] == ["tool_use", "tool_result"]
    assert _result_text(msg["content"][1]) == "late output"
    assert len(conv["chat_messages"]) == 1


def test_a_compaction_keeps_the_earlier_orphan_and_adds_no_call_from_its_replacement_history(
    tmp_path: Path,
) -> None:
    # The replacement history is model context: its tool call is never a turn.
    replaced = cf.custom_tool_call("call_in_replacement", "apply_patch", "replacement patch")
    lines = [
        cf.session_meta(_s(0), TID, cwd="/home/fixture/proj-omega", history_mode="paginated"),
        *_turn_with_orphan("turn-c1", "call_c1", 10),
        cf.rollout_line(_s(16), "compacted", {
            "message": "Summary of turn c1.",
            "replacement_history": [{"item": replaced, "metadata": None}],
        }),
        cf.item_completed(_s(17), TID, "turn-c1", {"type": "ContextCompaction", "id": "item-c1-compact"}),
    ]
    conv = _project(tmp_path, lines)
    assert _all_tool_ids(conv) == ["call_c1"]
    assert len(conv["compact_markers"]) == 1


def test_a_fork_inherits_the_orphan_calls_of_its_history_base_prefix_once(tmp_path: Path) -> None:
    parent_id = "019aa100-0000-7000-8000-0000000000c2"
    parent = [
        cf.session_meta(_s(0), parent_id, cwd="/home/fixture/proj-omega", history_mode="paginated"),
        *_turn_with_orphan("turn-p1", "call_p1", 10, tid=parent_id),
        *_turn_with_orphan("turn-p2", "call_p2", 20, tid=parent_id),
    ]
    for ordinal, line in enumerate(parent):
        line["ordinal"] = ordinal
    cut = 6  # the parent's first turn: session_meta plus five lines
    fork_meta = cf.session_meta(_s(40), TID, cwd="/home/fixture/fork", history_mode="paginated")
    fork_meta["payload"]["forked_from_id"] = parent_id
    fork_meta["payload"]["history_base"] = {"thread_id": parent_id, "end_ordinal_exclusive": cut, "end_byte_offset": 64 * cut}
    fork_meta["ordinal"] = cut
    fork = [fork_meta, *_turn_with_orphan("turn-f1", "call_f1", 50)]
    for ordinal, line in enumerate(fork[1:], start=cut + 1):
        line["ordinal"] = ordinal

    from backend.codex.rollout import read_rollout

    parent_path = cf.write_rollout(tmp_path / "parent" / f"rollout-2026-11-19T11-00-00-{parent_id}.jsonl", parent)
    parent_records = read_rollout(parent_path).records
    conv = _project(tmp_path, fork, load_base=lambda rid: parent_records if rid == parent_id else None)
    assert conv["incomplete"] is False, conv["incomplete_reasons"]
    assert _all_tool_ids(conv) == ["call_p1", "call_f1"]


# ---------------------------------------------------------------------------
# The archive, the store, the API, search, and MCP
# ---------------------------------------------------------------------------


@dataclass
class Env:
    codex: Path
    store: Any


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """A Codex home with only the orphan rollout, copied into the archive,
    with the index built over the archive as the process singleton."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache
    from backend.tests._platform_home import patch_home
    from backend.write_gate import run_archive_syncs
    from mcp_server import server

    home = patch_home(monkeypatch, tmp_path / "home")
    codex = home / ".codex"
    cf.write_orphan_tool_calls(codex)
    data_dir = tmp_path / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    (tmp_path / "claude").mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(tmp_path / "desktop-app"))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(si, "default_index_path", lambda: tmp_path / "explorer" / "search-index.sqlite")
    monkeypatch.setattr(server, "_settings", None)
    monkeypatch.setattr(server, "_store", None)
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    si.reset_search_index_for_tests()
    server.reset_archive_sync_for_tests()

    run_archive_syncs(ConversationStore(), "cli", 10)
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield Env(codex=codex, store=ConversationStore())
    finally:
        idx.close()
        si.reset_search_index_for_tests()
        server.reset_archive_sync_for_tests()
        invalidate_detail_dict_cache()
        clear_cache()
        config.get_settings.cache_clear()


def _store_blocks(env: Env) -> list[Any]:
    conversation = env.store.get_conversation(TID)
    assert conversation is not None
    return [b for m in conversation.messages for b in m.content]


def test_the_store_conversation_holds_each_orphan_call(env: Env) -> None:
    blocks = _store_blocks(env)
    uses = {b.id: b for b in blocks if b.type == "tool_use"}
    results = {b.tool_use_id for b in blocks if b.type == "tool_result"}
    assert {cf.ORPHAN_PATCH, cf.ORPHAN_UNKNOWN, cf.ORPHAN_NO_OUTPUT} <= set(uses)
    assert {cf.ORPHAN_PATCH, cf.ORPHAN_UNKNOWN, cf.ORPHAN_LONE_OUTPUT} <= results
    assert uses[cf.ORPHAN_PATCH].name == "apply_patch"


def test_the_conversation_api_returns_the_orphan_calls(env: Env) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    r = TestClient(app).get(f"/api/conversations/{TID}")
    assert r.status_code == 200, r.text
    body = json.dumps(r.json())
    for word in (W["patch_input"], W["patch_output"], W["exec_output"], W["lone_output"], W["no_output_input"]):
        assert word in body, word


def _use_path(path: str, monkeypatch: pytest.MonkeyPatch) -> str:
    from backend.tests.test_search_all_sources import use_path

    return use_path(path, monkeypatch)


def _hits(store: Any, word: str, context_size: str, tools: bool) -> dict[str, set[str]]:
    from backend.search import search_conversations

    resp = search_conversations(store, word, source="CODEX", context_size=context_size, include_tool_calls=tools)
    return {r.conversation_uuid: {m.message_uuid for m in r.matching_messages} for r in resp.results}


def _uuid_of_call(env: Env, call_id: str) -> str:
    conversation = env.store.get_conversation(TID)
    (found,) = [
        m.uuid for m in conversation.messages
        if any(b.id == call_id or b.tool_use_id == call_id for b in m.content)
    ]
    return found


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("word", "call_id"), TOOL_WORDS, ids=TOOL_WORD_IDS)
def test_a_tool_word_is_found_in_its_one_message_with_tools_on(
    env: Env, word: str, call_id: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = _uuid_of_call(env, call_id)
    hits = _hits(env.store, word, _use_path(path, monkeypatch), True)
    assert hits == {TID: {want}}, f"{word!r} on {path} with tools on"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("word", "call_id"), TOOL_WORDS, ids=TOOL_WORD_IDS)
def test_a_tool_word_is_not_found_with_tools_off(
    env: Env, word: str, call_id: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _uuid_of_call(env, call_id)  # the message exists, so "no hit" is the rule and not a gap
    hits = _hits(env.store, word, _use_path(path, monkeypatch), False)
    assert hits == {}, f"{word!r} on {path} with tools off"


@pytest.mark.parametrize("tools", [True, False], ids=["tools-on", "tools-off"])
def test_the_search_api_follows_the_tools_setting_for_an_orphan_output(env: Env, tools: bool) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    params = {"q": W["patch_output"], "source": "CODEX", "include_tool_calls": str(tools).lower()}
    r = TestClient(app).get("/api/search", params=params)
    assert r.status_code == 200, r.text
    got = {res["conversation_uuid"] for res in r.json()["results"]}
    assert got == ({TID} if tools else set())
    assert _uuid_of_call(env, cf.ORPHAN_PATCH)


def _mcp(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def test_mcp_get_messages_with_tool_calls_names_each_orphan_call(env: Env) -> None:
    msgs = _mcp("get_messages", conversation_id=TID, include_tool_calls=True)
    uses = [b for m in msgs for b in m.get("content", []) if b["type"] == "tool_use"]
    names = {b.get("id"): b["name"] for b in uses}
    assert names[cf.ORPHAN_PATCH] == "apply_patch"
    assert names[cf.ORPHAN_UNKNOWN] == "frobnicate"
    assert names[cf.ORPHAN_NO_OUTPUT] == "shell"


def test_mcp_get_messages_with_tool_results_holds_each_orphan_input_and_output(env: Env) -> None:
    text = json.dumps(_mcp("get_messages", conversation_id=TID, include_tool_results=True))
    for _, _, inp, out in ORPHANS:
        for word in (inp, out):
            if word:
                assert word in text, word


def test_mcp_get_messages_without_tools_gives_the_orphan_calls_no_text(env: Env) -> None:
    msgs = _mcp("get_messages", conversation_id=TID)
    assert len(msgs) == 9
    text = json.dumps(msgs)
    for word, _ in TOOL_WORDS:
        assert word not in text, word
    assert W["message"] in text


# ---------------------------------------------------------------------------
# The projection version
# ---------------------------------------------------------------------------


def test_codex_projection_version_is_5() -> None:
    # 3 for N10's history; 4 for the tool calls with no item (C1); 5 for code mode (#90).
    from backend.sources import codex

    assert codex.CODEX_PROJECTION_VERSION == 5
