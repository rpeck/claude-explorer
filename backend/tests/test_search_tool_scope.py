"""Issue #84: search looks inside tool content only when asked.

The user's rule (2026-11-20): with ``include_tool_calls=False`` a search
matches titles and the text of user and assistant messages only. With
``True`` it also matches tool calls and tool output. Thinking and
reasoning are never searched.

One truth, every path, every source. For each source (Claude Desktop,
Claude Code, local Cowork, remote Cowork, Codex) the corpus holds one
session with a word that is only in:

* the message text (``text``);
* a tool call input (``call``);
* a tool output (``output``);
* a thinking or reasoning block (``think``).

The FTS paths (snippet and full) and the linear path return the same
conversations, the same messages, and the same counts.

Count rule for titles (rule A): a title match is not a message match.
The title entry (``message_uuid == "title"``) stays in
``matching_messages``, but ``total_messages_matched`` and
``returned_messages`` never count it.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as fake

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

ORG = "00000000-1111-2222-3333-444444444444"
REMOTE_ORG = "00000000-1111-2222-3333-555555555555"

#: (case id, source, conversation uuid, word tag)
SOURCES = (
    ("desktop", "CLAUDE_AI", "0f415a45-9c62-8671-d4ad-53b84acb7e1a", "dsk"),
    ("claude-code", "CLAUDE_CODE", "435e56ed-50ba-21c0-3495-0e8749b20543", "ccx"),
    ("cowork-local", "CLAUDE_COWORK", "cccc2222-3333-4444-5555-666677778888", "cwl"),
    ("cowork-remote", "CLAUDE_COWORK", "session_01E2EREMOTE00000000000001", "cwr"),
    ("codex", "CODEX", fake.THREAD_LEGACY, "cdx"),
)
SOURCE_IDS = [s[0] for s in SOURCES]

#: Sources whose title the test can set apart from every message.
TITLE_SOURCES = [s for s in SOURCES if s[0] in ("desktop", "codex")]
TITLE_SOURCE_IDS = [s[0] for s in TITLE_SOURCES]

PATHS = ("fts-snippet", "fts-full", "linear")
TOOLS = (True, False)
TOOLS_IDS = ["tools-on", "tools-off"]
KINDS = ("text", "call", "output")


def word(tag: str, kind: str) -> str:
    """One unique token per source and kind, for example ``dskcallword``."""
    return f"{tag}{kind}word"


# ---------------------------------------------------------------------------
# Corpus: the e2e fixture tree, with one session per source replaced
# ---------------------------------------------------------------------------


def _desktop_conv(tag: str, conv_uuid: str) -> dict[str, Any]:
    def m(uuid: str, sender: str, content: list[dict[str, Any]], minute: int) -> dict[str, Any]:
        text = "\n".join(b["text"] for b in content if b.get("type") == "text")
        when = f"2026-04-01T10:{minute:02d}:00Z"
        return {
            "uuid": uuid, "sender": sender, "text": text, "content": content,
            "created_at": when, "updated_at": when, "attachments": [], "files": [],
            "files_v2": [], "truncated": False,
            "parent_message_uuid": None if minute == 0 else f"{tag}-m{minute - 1}",
        }

    messages = [
        m(f"{tag}-m0", "human", [{"type": "text", "text": f"Explain the {word(tag, 'text')} flag. Also {word(tag, 'titled')}."}], 0),
        m(f"{tag}-m1", "assistant", [
            {"type": "thinking", "thinking": f"I think about {word(tag, 'think')} first."},
            {"type": "text", "text": "I look it up."},
            {"type": "tool_use", "id": f"{tag}-t1", "name": "web_search", "input": {"query": f"find {word(tag, 'call')}"}},
            {"type": "tool_result", "tool_use_id": f"{tag}-t1",
             "content": [{"type": "text", "text": f"Result: {word(tag, 'output')} is a flag."}]},
            {"type": "text", "text": "It turns on the new parser."},
        ], 1),
    ]
    return {
        "uuid": conv_uuid,
        "name": f"Plan {word(tag, 'titled')} and {word(tag, 'titleonly')}",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-04-01T10:30:00Z",
        "is_starred": False,
        "has_branches": False,
        "current_leaf_message_uuid": f"{tag}-m1",
        "organization_id": ORG,
        "organization_name": "Fixture Workspace",
        "chat_messages": messages,
    }


def _sdk_lines(tag: str, session_id: str, *, cowork: bool) -> list[dict[str, Any]]:
    """Claude Code / Cowork rows: a text turn, a thinking block, a tool call,
    and its tool result (a user row, as Claude Code stores it)."""
    t0 = datetime(2026, 10, 7, 11, 0, tzinfo=UTC)

    def row(i: int, type_: str, content: Any, parent: str | None) -> dict[str, Any]:
        when = (t0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        msg: dict[str, Any] = {"role": "user" if type_ == "user" else "assistant", "content": content}
        if type_ == "assistant":
            msg.update({"id": f"msg_{tag}_{i}", "model": "claude-opus-5-5", "type": "message"})
        r: dict[str, Any] = {"type": type_, "uuid": f"{tag}-r{i}", "message": msg}
        if cowork:
            r.update({"session_id": session_id, "parent_tool_use_id": None,
                      "_audit_timestamp": when, "_sequence_num": i + 1})
        else:
            r.update({"sessionId": session_id, "parentUuid": parent, "timestamp": when,
                      "cwd": "/fixture/project", "isSidechain": False, "userType": "external",
                      "version": "2.0.0", "entrypoint": "cli", "gitBranch": "fixture-branch"})
        return r

    return [
        row(0, "user", f"Explain the {word(tag, 'text')} flag.", None),
        row(1, "assistant", [
            {"type": "thinking", "thinking": f"I think about {word(tag, 'think')} first.", "signature": "x"},
            {"type": "text", "text": "I run a search."},
            {"type": "tool_use", "id": f"toolu_{tag}1", "name": "Bash", "input": {"command": f"grep -r {word(tag, 'call')} ."}},
        ], f"{tag}-r0"),
        row(2, "user", [{"type": "tool_result", "tool_use_id": f"toolu_{tag}1",
                         "content": f"src/a.py: {word(tag, 'output')} = 1"}], f"{tag}-r1"),
        row(3, "assistant", [{"type": "text", "text": "The flag turns on the new parser."}], f"{tag}-r2"),
    ]


def _codex_lines(tag: str, thread_id: str) -> list[dict[str, Any]]:
    t0 = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    cwd = "/home/fixture/proj-beta"

    def at(s: int) -> datetime:
        return t0 + timedelta(seconds=s)

    return [
        fake.session_meta(t0, thread_id, cwd=cwd, history_mode="legacy"),
        fake.turn_context(at(1), "turn-s2", cwd=cwd, model="gpt-5.4-codex"),
        fake.response_item(at(2), fake.message("user", fake.input_text(f"Explain the {word(tag, 'text')} flag. Also {word(tag, 'titled')}."))),
        fake.response_item(at(3), {"type": "reasoning", "summary": [
            {"type": "summary_text", "text": f"I think about {word(tag, 'think')} first."}], "encrypted_content": None}),
        fake.response_item(at(4), fake.function_call("call_s2", "shell", {"command": ["grep", "-r", word(tag, "call"), "."]})),
        fake.response_item(at(5), fake.function_call_output("call_s2", f"src/a.py: {word(tag, 'output')} = 1")),
        fake.response_item(at(6), fake.message("assistant", fake.output_text("The flag turns on the new parser."))),
    ]


def _replace_sessions(root: Path) -> None:
    data_dir = root / "data"
    # Claude Desktop.
    tag = "dsk"
    conv_uuid = SOURCES[0][2]
    path = data_dir / "by-org" / ORG / f"{conv_uuid}.json"
    path.write_text(json.dumps(_desktop_conv(tag, conv_uuid)))
    # Claude Code.
    cc_uuid = SOURCES[1][2]
    cc = root / "claude" / "projects" / "-fixture-project" / f"{cc_uuid}.jsonl"
    cc.write_text("".join(json.dumps(r) + "\n" for r in _sdk_lines("ccx", cc_uuid, cowork=False)))
    # Local Cowork.
    local = root / "desktop-app" / "local-agent-mode-sessions" / "d_e2e" / "o_e2e"
    audit = local / "local_cccc2222-3333-4444-5555-666677778888" / "audit.jsonl"
    audit.write_text("".join(json.dumps(r) + "\n" for r in _sdk_lines("cwl", "cli-cccc2222", cowork=True)))
    sidecar = local / "local_cccc2222-3333-4444-5555-666677778888.json"
    meta = json.loads(sidecar.read_text())
    meta["initialMessage"] = f"Explain the {word('cwl', 'text')} flag."
    sidecar.write_text(json.dumps(meta))
    # Remote Cowork.
    remote = data_dir / "cowork-remote" / REMOTE_ORG / "remote_session_01E2EREMOTE00000000000001" / "audit.jsonl"
    remote.write_text("".join(json.dumps(r) + "\n" for r in _sdk_lines("cwr", "worker-session", cowork=True)))
    # Codex (the archive copy).
    codex = data_dir / "codex" / "fixture000000"
    thread_file = codex / "threads" / f"{fake.THREAD_LEGACY}.json"
    thread = json.loads(thread_file.read_text())
    rollout = codex / "rollouts" / f"{thread['current_stem']}.jsonl"
    data = fake.encode_lines(_codex_lines("cdx", fake.THREAD_LEGACY))
    rollout.write_bytes(data)
    thread["rollouts"][-1]["copied_bytes"] = len(data)
    thread["title"] = f"Plan {word('cdx', 'titled')} and {word('cdx', 'titleonly')}"
    thread_file.write_text(json.dumps(thread))


def build_tool_scope_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Copy the e2e fixture tree, replace one session per source with the
    scope sessions, and build the index as the process singleton. Return the
    index. The MCP tests use it too."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache

    root = tmp_path / "corpus"
    shutil.copytree(FIXTURES_DIR / "desktop", root / "data")
    shutil.copytree(FIXTURES_DIR / "claude", root / "claude")
    shutil.copytree(FIXTURES_DIR / "cowork", root / "desktop-app")
    _replace_sessions(root)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(root / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app"))
    monkeypatch.setenv("CODEX_HOME", str(root / "codex-home-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "search-index.sqlite")
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    si.reset_search_index_for_tests()

    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    return idx


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from backend.store import ConversationStore
    from backend.tests.test_search_all_sources import close_e2e_corpus

    idx = build_tool_scope_corpus(tmp_path, monkeypatch)
    try:
        yield ConversationStore()
    finally:
        close_e2e_corpus(idx)


def _use_path(path: str, monkeypatch: pytest.MonkeyPatch) -> str:
    from backend.tests.test_search_all_sources import use_path

    return use_path(path, monkeypatch)


def _search(store: Any, q: str, *, tools: bool, context_size: str, source: str = "all") -> Any:
    from backend.search import search_conversations

    return search_conversations(store, q, source=source, context_size=context_size, include_tool_calls=tools)


def _message_hits(resp: Any) -> dict[str, set[str]]:
    """conversation uuid -> the uuids of the matching messages (no title entry)."""
    out: dict[str, set[str]] = {}
    for r in resp.results:
        out[r.conversation_uuid] = {m.message_uuid for m in r.matching_messages if m.message_uuid != "title"}
    return out


def _path_snapshot(store: Any, q: str, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run one search on one path, in its own monkeypatch scope."""
    with monkeypatch.context() as mp:
        resp = _search(store, q, tools=tools, context_size=_use_path(path, mp))
    return resp


# ---------------------------------------------------------------------------
# Each kind of word, on each path, with tools on and off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_text_word_is_found_with_tools_on_and_off(
    corpus: Any, src: tuple, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = src
    hits = _message_hits(_search(corpus, word(tag, "text"), tools=tools, context_size=_use_path(path, monkeypatch)))
    assert set(hits) == {conv_uuid}, f"{word(tag, 'text')!r}: {sorted(hits)}"
    assert hits[conv_uuid], "the text word must match a message, not only the title"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_tool_call_word_is_found_only_with_tools_on(
    corpus: Any, src: tuple, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = src
    hits = _message_hits(_search(corpus, word(tag, "call"), tools=tools, context_size=_use_path(path, monkeypatch)))
    want = {conv_uuid} if tools else set()
    assert set(hits) == want, f"{word(tag, 'call')!r} include_tool_calls={tools}: {sorted(hits)}"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_tool_output_word_is_found_only_with_tools_on(
    corpus: Any, src: tuple, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = src
    hits = _message_hits(_search(corpus, word(tag, "output"), tools=tools, context_size=_use_path(path, monkeypatch)))
    want = {conv_uuid} if tools else set()
    assert set(hits) == want, f"{word(tag, 'output')!r} include_tool_calls={tools}: {sorted(hits)}"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_thinking_word_is_never_found(
    corpus: Any, src: tuple, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = src[3]
    hits = _message_hits(_search(corpus, word(tag, "think"), tools=tools, context_size=_use_path(path, monkeypatch)))
    assert hits == {}, f"{word(tag, 'think')!r} include_tool_calls={tools}: {sorted(hits)}"


# ---------------------------------------------------------------------------
# Parity between the paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("kind", (*KINDS, "titled"))
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_paths_return_the_same_hits(
    corpus: Any, src: tuple, kind: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = word(src[3], kind)
    got = {
        p: {r.conversation_uuid: {m.message_uuid for m in r.matching_messages}
            for r in _path_snapshot(corpus, q, p, tools, monkeypatch).results}
        for p in PATHS
    }
    assert got["fts-snippet"] == got["linear"], f"{q!r} include_tool_calls={tools}: {got}"
    assert got["fts-full"] == got["linear"], f"{q!r} include_tool_calls={tools}: {got}"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("kind", (*KINDS, "titled"))
@pytest.mark.parametrize("src", SOURCES, ids=SOURCE_IDS)
def test_paths_return_the_same_counts(
    corpus: Any, src: tuple, kind: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = word(src[3], kind)
    got = {}
    for p in PATHS:
        resp = _path_snapshot(corpus, q, p, tools, monkeypatch)
        got[p] = (resp.total_messages_matched, resp.returned_messages)
    assert got["fts-snippet"] == got["linear"], f"{q!r} include_tool_calls={tools}: {got}"
    assert got["fts-full"] == got["linear"], f"{q!r} include_tool_calls={tools}: {got}"


# ---------------------------------------------------------------------------
# Count rule for titles (rule A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", TITLE_SOURCES, ids=TITLE_SOURCE_IDS)
def test_title_match_is_not_counted_as_a_message(
    corpus: Any, src: tuple, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = src
    resp = _search(corpus, word(tag, "titled"), tools=False, context_size=_use_path(path, monkeypatch))
    [result] = [r for r in resp.results if r.conversation_uuid == conv_uuid]
    uuids = [m.message_uuid for m in result.matching_messages]
    assert "title" in uuids, f"the title entry stays in matching_messages: {uuids}"
    assert len([u for u in uuids if u != "title"]) == 1, uuids
    assert (resp.total_messages_matched, resp.returned_messages) == (1, 1), (
        f"a title match is not a message match: total={resp.total_messages_matched} "
        f"returned={resp.returned_messages}"
    )


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("src", TITLE_SOURCES, ids=TITLE_SOURCE_IDS)
def test_title_only_match_counts_zero_messages(
    corpus: Any, src: tuple, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = src
    resp = _search(corpus, word(tag, "titleonly"), tools=False, context_size=_use_path(path, monkeypatch))
    assert [r.conversation_uuid for r in resp.results] == [conv_uuid], "a title-only hit is still a result"
    assert [m.message_uuid for m in resp.results[0].matching_messages] == ["title"]
    assert (resp.total_messages_matched, resp.returned_messages) == (0, 0), (
        f"total={resp.total_messages_matched} returned={resp.returned_messages}"
    )


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------


def _client() -> Any:
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)  # No ``with``: the lifespan (watcher, index build) stays off.


def _api_uuids(body: dict[str, Any]) -> set[str]:
    return {r["conversation_uuid"] for r in body["results"]}


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("kind", KINDS)
def test_api_search_get_honors_include_tool_calls(corpus: Any, kind: str, tools: bool) -> None:
    client = _client()
    for _, _, conv_uuid, tag in SOURCES:
        q = word(tag, kind)
        r = client.get("/api/search", params={"q": q, "include_tool_calls": str(tools).lower()})
        assert r.status_code == 200, r.text
        want = {conv_uuid} if (kind == "text" or tools) else set()
        assert _api_uuids(r.json()) == want, f"GET q={q!r} include_tool_calls={tools}"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("kind", KINDS)
def test_api_search_post_honors_include_tool_calls(corpus: Any, kind: str, tools: bool) -> None:
    client = _client()
    all_uuids = [s[2] for s in SOURCES]
    for _, _, conv_uuid, tag in SOURCES:
        q = word(tag, kind)
        r = client.post("/api/search", json={"q": q, "include_tool_calls": tools, "conversation_uuids": all_uuids})
        assert r.status_code == 200, r.text
        want = {conv_uuid} if (kind == "text" or tools) else set()
        assert _api_uuids(r.json()) == want, f"POST q={q!r} include_tool_calls={tools}"


def test_api_search_default_includes_tool_content(corpus: Any) -> None:
    """External scripts that send no ``include_tool_calls`` keep today's result."""
    client = _client()
    for _, _, conv_uuid, tag in SOURCES:
        for kind in ("call", "output"):
            r = client.get("/api/search", params={"q": word(tag, kind)})
            assert r.status_code == 200, r.text
            assert _api_uuids(r.json()) == {conv_uuid}, f"GET q={word(tag, kind)!r} with no include_tool_calls"
