"""Issue #96: search finds text inside agent transcripts.

* Each agent file is its own indexed source with its own replace and delete;
  the drift pass and the watcher cover ``subagents/`` files.
* A hit carries the locator ``(conversation_uuid = session uuid, agent_id,
  message_uuid)`` and ``agent_description``; ``conversation_name`` is the
  session name. Agents give no title hits.
* Show Tools, Show Code Mode, and Show Compactions apply as for a session.
* Every path agrees: FTS snippet, FTS full, linear.
* Scope filters apply to an agent through its session uuid.
* ``SCHEMA_VERSION`` is 17.

The tree is ``test_subagent_discovery.write_tree``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_subagent_discovery import (
    CUR_AGENT,
    CUR_DESCRIPTION,
    CUR_MESSAGE,
    CWD,
    LEGACY_AGENT,
    LEGACY_MESSAGE,
    OTHER_LEGACY_AGENT,
    OTHER_SESSION,
    PROJECT,
    REAL_AGENT,
    REAL_AGENT_CODE_WORD,
    REAL_DESCRIPTION,
    REAL_SESSION,
    SESSION,
    WORDS,
    _small_agent,
    close_tree,
    use_tree,
)

PATHS = ("fts-snippet", "fts-full", "linear")
#: (include_tool_calls, include_code_mode, include_compactions)
COMBOS = [(t, c, k) for t in (False, True) for c in (False, True) for k in (False, True)]
COMBO_IDS = [f"tools-{int(t)}-code-{int(c)}-compact-{int(k)}" for t, c, k in COMBOS]


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The tree, indexed as the process singleton. Yield the store."""
    from backend import search_index as si
    from backend.store import ConversationStore

    root = use_tree(tmp_path, monkeypatch)
    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield ConversationStore()
    finally:
        idx.close()
        close_tree()


def _search(store: Any, q: str, path: str, mp: pytest.MonkeyPatch, *, tools: bool = True,
            code: bool = True, compact: bool = True, **scope: Any) -> Any:
    from backend.search import search_conversations
    from backend.tests.test_search_all_sources import use_path

    with mp.context() as m:
        return search_conversations(
            store, q, context_size=use_path(path, m), include_tool_calls=tools,
            include_code_mode=code, include_compactions=compact, **scope)


def _locators(resp: Any) -> set[tuple[str, str | None, str]]:
    """Every message hit as (session uuid, agent id, message uuid); title hits left out."""
    return {
        (r.conversation_uuid, r.agent_id, m.message_uuid)
        for r in resp.results for m in r.matching_messages if m.message_uuid != "title"
    }


def _agents(resp: Any) -> set[tuple[str, str | None]]:
    return {(s, a) for s, a, _ in _locators(resp)}


def _agents_are_indexed(store: Any, mp: pytest.MonkeyPatch) -> None:
    """A precondition of each test whose answer can be "no hit": the agents are searched at all."""
    resp = _search(store, WORDS["text"], "linear", mp)
    assert [r.agent_id for r in resp.results] == [CUR_AGENT]


# ---------------------------------------------------------------------------
# The locator and the result shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
def test_a_word_only_an_agent_wrote_gives_the_full_locator(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, WORDS["text"], path, monkeypatch)
    assert _locators(resp) == {(SESSION, CUR_AGENT, CUR_MESSAGE["text"])}


@pytest.mark.parametrize("path", PATHS)
def test_an_agent_hit_carries_the_description_and_the_session_name(
    corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (result,) = _search(corpus, WORDS["text"], path, monkeypatch).results
    assert result.agent_description == CUR_DESCRIPTION
    session = next(c for c in corpus.list_conversations() if c.uuid == SESSION)
    assert result.conversation_name == session.name


@pytest.mark.parametrize("path", PATHS)
def test_a_legacy_agent_is_searched(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, WORDS["legacy"], path, monkeypatch)
    assert _locators(resp) == {(SESSION, LEGACY_AGENT, LEGACY_MESSAGE)}
    (result,) = resp.results
    assert result.agent_description is None


@pytest.mark.parametrize("path", PATHS)
def test_a_legacy_agent_belongs_to_its_session_id(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _agents(_search(corpus, WORDS["other"], path, monkeypatch)) == {(OTHER_SESSION, OTHER_LEGACY_AGENT)}


@pytest.mark.parametrize("path", PATHS)
def test_the_real_subagent_code_mode_run_is_found_with_code_mode_on(
    corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    on = _search(corpus, REAL_AGENT_CODE_WORD, path, monkeypatch, code=True)
    off = _search(corpus, REAL_AGENT_CODE_WORD, path, monkeypatch, code=False)
    assert _agents(on) == {(REAL_SESSION, REAL_AGENT)}
    assert {r.agent_description for r in on.results} == {REAL_DESCRIPTION}
    assert _agents(off) == set()


@pytest.mark.parametrize("path", PATHS)
def test_a_session_hit_has_no_agent_id(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, WORDS["session"], path, monkeypatch)
    assert _agents(resp) == {(SESSION, None)}
    assert all(r.agent_description is None for r in resp.results)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind", ["internal", "escape", "no_owner"])
def test_files_that_are_not_members_are_never_searched(
    corpus: Any, path: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _agents_are_indexed(corpus, monkeypatch)
    assert _search(corpus, WORDS[kind], path, monkeypatch).results == []


@pytest.mark.parametrize("path", PATHS)
def test_an_agent_and_its_session_are_separate_rows(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A word in the session and in its agent: two rows with one session uuid."""
    resp = _search(corpus, WORDS["shared"], path, monkeypatch)
    assert _locators(resp) == {(SESSION, None, f"{SESSION[-4:]}-t1"), (SESSION, CUR_AGENT, CUR_MESSAGE["text"])}
    assert len(resp.results) == 2


@pytest.mark.parametrize("path", PATHS)
def test_agents_give_no_title_hits(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _agents_are_indexed(corpus, monkeypatch)
    resp = _search(corpus, "Synthetic", path, monkeypatch)
    for r in resp.results:
        if r.agent_id is not None:
            assert all(m.message_uuid != "title" for m in r.matching_messages), r


@pytest.mark.parametrize("path", PATHS)
def test_the_three_paths_count_the_same_agent_messages(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, WORDS["text"], path, monkeypatch)
    assert resp.total_messages_matched == resp.returned_messages == 1


def test_the_three_paths_give_the_same_results(corpus: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def every_hit(path: str) -> set[tuple[str, str | None, str]]:
        out: set[tuple[str, str | None, str]] = set()
        for k in ("text", "tool", "code", "compact", "legacy", "other", "session", "shared"):
            out |= _locators(_search(corpus, WORDS[k], path, monkeypatch))
        return out

    seen = {p: every_hit(p) for p in PATHS}
    assert seen["fts-snippet"] == seen["fts-full"] == seen["linear"]
    assert len(seen["linear"]) == 8


# ---------------------------------------------------------------------------
# The toggles
# ---------------------------------------------------------------------------


def _want(kind: str, tools: bool, code: bool, compact: bool) -> bool:
    return {"text": True, "tool": tools, "code": code, "compact": compact}[kind]


@pytest.mark.parametrize(("tools", "code", "compact"), COMBOS, ids=COMBO_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind", ["text", "tool", "code", "compact"])
def test_each_toggle_applies_inside_an_agent(
    corpus: Any, kind: str, path: str, tools: bool, code: bool, compact: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _agents_are_indexed(corpus, monkeypatch)
    resp = _search(corpus, WORDS[kind], path, monkeypatch, tools=tools, code=code, compact=compact)
    want = {(SESSION, CUR_AGENT, CUR_MESSAGE[kind])} if _want(kind, tools, code, compact) else set()
    assert _locators(resp) == want, f"{kind} tools={tools} code={code} compact={compact}"


# ---------------------------------------------------------------------------
# Scope filters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("scope", "found"), [
    ({"conversation_uuid": SESSION}, True),
    ({"conversation_uuid": OTHER_SESSION}, False),
    ({"project_path": CWD}, True),
    ({"project_path": "/home/fixture/elsewhere"}, False),
    ({"conversation_uuids": {SESSION}}, True),
    ({"conversation_uuids": {OTHER_SESSION}}, False),
    ({"bookmarks": {SESSION}}, True),
    ({"bookmarks": {OTHER_SESSION}}, False),
    ({"source": "CLAUDE_CODE"}, True),
    ({"source": "CLAUDE_AI"}, False),
], ids=["pin-own", "pin-other", "project-own", "project-other", "uuids-own", "uuids-other",
        "bookmarks-own", "bookmarks-other", "source-cc", "source-desktop"])
def test_scope_filters_apply_through_the_session(
    corpus: Any, path: str, scope: dict[str, Any], found: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _agents_are_indexed(corpus, monkeypatch)
    resp = _search(corpus, WORDS["text"], path, monkeypatch, **scope)
    assert _agents(resp) == ({(SESSION, CUR_AGENT)} if found else set())


# ---------------------------------------------------------------------------
# The HTTP route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("context_size", ["snippet", "full"])
def test_the_search_api_returns_the_agent_fields(corpus: Any, context_size: str) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    body = TestClient(app).get("/api/search", params={"q": WORDS["text"], "context_size": context_size}).json()
    (result,) = body["results"]
    assert result["conversation_uuid"] == SESSION
    assert result["agent_id"] == CUR_AGENT
    assert result["agent_description"] == CUR_DESCRIPTION
    assert [m["message_uuid"] for m in result["matching_messages"]] == [CUR_MESSAGE["text"]]


def test_a_session_result_on_the_wire_has_agent_id_null(corpus: Any) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    body = TestClient(app).get("/api/search", params={"q": WORDS["session"]}).json()
    assert [(r["conversation_uuid"], r["agent_id"]) for r in body["results"]] == [(SESSION, None)]


def test_the_search_index_is_at_schema_17() -> None:
    from backend import search_index as si

    assert si.SCHEMA_VERSION == 17


# ---------------------------------------------------------------------------
# Index upkeep: replace, delete, drift, and the watcher
# ---------------------------------------------------------------------------


def _agent_dir(tmp_path: Path) -> Path:
    return tmp_path / "corpus" / "claude" / "projects" / PROJECT / SESSION / "subagents"


def _drift(store: Any) -> None:
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    clear_cache()
    invalidate_detail_dict_cache()
    si.update_drifted_files(store, index=si._search_index)


def _fts(store: Any, q: str, mp: pytest.MonkeyPatch) -> set[tuple[str, str | None, str]]:
    return _locators(_search(store, q, "fts-snippet", mp))


def _bump_mtime(path: Path) -> None:
    import os

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_a_new_agent_file_is_indexed_by_the_drift_pass(
    corpus: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.tests import cc_code_mode_fake as fake

    assert _fts(corpus, "sanewagentword", monkeypatch) == set()
    fake.write_lines(_agent_dir(tmp_path) / "agent-anew000000000096.jsonl",
                     _small_agent("sw", "sanewagentword", SESSION, "anew000000000096"))
    _drift(corpus)
    assert _fts(corpus, "sanewagentword", monkeypatch) == {(SESSION, "anew000000000096", "sw-t1")}


def test_a_changed_agent_file_replaces_only_its_own_rows(
    corpus: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _agent_dir(tmp_path) / f"agent-{CUR_AGENT}.jsonl"
    text = path.read_text().replace(WORDS["text"], "sareplacedword")
    path.write_text(text)
    _bump_mtime(path)
    _drift(corpus)
    assert _fts(corpus, WORDS["text"], monkeypatch) == set()
    assert _fts(corpus, "sareplacedword", monkeypatch) == {(SESSION, CUR_AGENT, CUR_MESSAGE["text"])}
    # The session and the other agents keep their rows.
    assert _fts(corpus, WORDS["session"], monkeypatch) == {(SESSION, None, f"{SESSION[-4:]}-u1")}
    assert _fts(corpus, WORDS["legacy"], monkeypatch) == {(SESSION, LEGACY_AGENT, LEGACY_MESSAGE)}


def test_a_deleted_agent_file_drops_only_its_own_rows(
    corpus: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _fts(corpus, WORDS["text"], monkeypatch)
    (_agent_dir(tmp_path) / f"agent-{CUR_AGENT}.jsonl").unlink()
    _drift(corpus)
    assert _fts(corpus, WORDS["text"], monkeypatch) == set()
    assert _fts(corpus, WORDS["session"], monkeypatch)
    assert _fts(corpus, WORDS["legacy"], monkeypatch)


def test_a_changed_session_file_keeps_its_agent_rows(
    corpus: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = tmp_path / "corpus" / "claude" / "projects" / PROJECT / f"{SESSION}.jsonl"
    session.write_text(session.read_text().replace(WORDS["session"], "sasessionnewword"))
    _bump_mtime(session)
    _drift(corpus)
    assert _fts(corpus, "sasessionnewword", monkeypatch)
    assert _fts(corpus, WORDS["text"], monkeypatch) == {(SESSION, CUR_AGENT, CUR_MESSAGE["text"])}


def test_an_unchanged_tree_reindexes_nothing(corpus: Any) -> None:
    from backend import search_index as si

    assert f"agent-{CUR_AGENT}.jsonl" in {p.name for p in si._search_index.list_indexed_paths()}
    assert si.update_drifted_files(corpus, index=si._search_index) == 0


def test_the_indexed_paths_hold_each_agent_file(corpus: Any, tmp_path: Path) -> None:
    from backend import search_index as si

    names = {p.name for p in si._search_index.list_indexed_paths()}
    assert {f"agent-{CUR_AGENT}.jsonl", f"agent-{LEGACY_AGENT}.jsonl", f"agent-{REAL_AGENT}.jsonl"} <= names
    assert not any("acompact" in n or "aside_question" in n for n in names)


def test_a_watcher_event_for_a_subagent_file_reaches_the_index(
    corpus: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The projects observer is recursive and matches ``*.jsonl``; its drift pass must index the file."""
    from types import SimpleNamespace

    from backend import cc_watcher
    from backend.tests import cc_code_mode_fake as fake

    monkeypatch.setattr(cc_watcher, "_schedule_drift", lambda _path: _drift(corpus))
    path = fake.write_lines(_agent_dir(tmp_path) / "agent-awatch0000000096.jsonl",
                            _small_agent("sx", "sawatchword", SESSION, "awatch0000000096"))
    handler = cc_watcher._build_projects_event_handler()
    handler.on_created(SimpleNamespace(is_directory=False, src_path=str(path)))
    assert _fts(corpus, "sawatchword", monkeypatch) == {(SESSION, "awatch0000000096", "sx-t1")}
