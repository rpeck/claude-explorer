"""Issue #90: search looks inside Code Mode runs only when asked.

* ``include_code_mode`` (default false) is independent of
  ``include_tool_calls``: a run's script, inner calls, returned value, and
  stdout match only with it on, whatever the tools flag is.
* A Code Mode call with no link is an ordinary tool call: it follows
  ``include_tool_calls`` only.
* Every path agrees: FTS snippet, FTS full, linear.
* The index has a ``body_code`` column, ``SCHEMA_VERSION`` 17 (#96 bumped it again).

All data is synthetic (``cc_code_mode_fake``).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests import cc_code_mode_fake as fake

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
W = fake.WORDS
PATHS = ("fts-snippet", "fts-full", "linear")
COMBOS = [(t, c) for t in (False, True) for c in (False, True)]
COMBO_IDS = [f"tools-{'on' if t else 'off'}-code-{'on' if c else 'off'}" for t, c in COMBOS]
# Not "subagent_code": search does not index subagent files at all (a gap that is older than #90).
RUN_WORDS = ("code", "inner_input", "inner_output", "returned", "stdout", "background_result")


def build_code_mode_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The Code Mode session and one ordinary Claude Code session, indexed. Return the index.

    A small tree on purpose: each case builds it, and a copy of the whole e2e
    fixture tree per case cost over a minute on the serial Windows ARM64 run.
    """
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache

    root = tmp_path / "corpus"
    (root / "data").mkdir(parents=True)
    plain = "claude/projects/-fixture-project"
    shutil.copytree(FIXTURES_DIR / plain, root / plain)
    fake.write_session(root / "claude")
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(root / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app-missing"))
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

    idx = build_code_mode_corpus(tmp_path, monkeypatch)
    try:
        yield ConversationStore()
    finally:
        close_e2e_corpus(idx)


def _search(store: Any, q: str, path: str, tools: bool, code: bool, mp: pytest.MonkeyPatch) -> Any:
    from backend.search import search_conversations
    from backend.tests.test_search_all_sources import use_path

    with mp.context() as m:
        return search_conversations(
            store, q, context_size=use_path(path, m), include_tool_calls=tools, include_code_mode=code)


def _hits(resp: Any) -> set[str]:
    return {r.conversation_uuid for r in resp.results if any(m.message_uuid != "title" for m in r.matching_messages)}


@pytest.mark.parametrize(("tools", "code"), COMBOS, ids=COMBO_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind", RUN_WORDS)
def test_a_run_word_is_found_only_with_code_mode_on(
    corpus: Any, kind: str, path: str, tools: bool, code: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = _hits(_search(corpus, W[kind], path, tools, code, monkeypatch))
    want = {fake.SESSION} if code else set()
    assert hits == want, f"{W[kind]} tools={tools} code={code}: {hits}"


@pytest.mark.parametrize(("tools", "code"), COMBOS, ids=COMBO_IDS)
@pytest.mark.parametrize("path", PATHS)
def test_message_text_is_found_with_every_combination(
    corpus: Any, path: str, tools: bool, code: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _hits(_search(corpus, W["reply"], path, tools, code, monkeypatch)) == {fake.SESSION}


@pytest.mark.parametrize(("tools", "code"), COMBOS, ids=COMBO_IDS)
@pytest.mark.parametrize("path", PATHS)
def test_an_unlinked_code_mode_call_follows_the_tools_flag_only(
    corpus: Any, path: str, tools: bool, code: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = _hits(_search(corpus, W["legacy_output"], path, tools, code, monkeypatch))
    assert hits == ({fake.SESSION} if tools else set())


@pytest.mark.parametrize("path", PATHS)
def test_the_three_paths_count_the_same_messages(corpus: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, W["code"], path, False, True, monkeypatch)
    assert resp.total_messages_matched == resp.returned_messages == 1


def test_a_snippet_of_a_run_hit_shows_the_word(corpus: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _search(corpus, W["inner_output"], "fts-snippet", False, True, monkeypatch)
    (result,) = resp.results
    (match,) = [m for m in result.matching_messages if m.message_uuid != "title"]
    assert W["inner_output"] in str(match.model_dump())


def test_the_search_api_takes_include_code_mode(corpus: Any) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    on = client.get("/api/search", params={"q": W["code"], "include_code_mode": "true"})
    off = client.get("/api/search", params={"q": W["code"]})
    assert on.status_code == off.status_code == 200
    assert [r["conversation_uuid"] for r in on.json()["results"]] == [fake.SESSION]
    assert off.json()["results"] == []


def test_the_index_has_a_body_code_column_at_schema_16(corpus: Any) -> None:
    from backend import search_index as si

    assert si.SCHEMA_VERSION == 17
    cols = {r[1] for r in si._search_index._get_read_conn().execute("PRAGMA table_info(messages)")}
    assert "body_code" in cols


def test_the_code_mode_text_of_a_message_holds_every_run_part() -> None:
    from backend.search_text import _extract_code_mode_text

    msg = {"content": [{"type": "code_run", "id": "r1", "name": "exec", "code_run": {
        "code": "alpha()", "result": "beta", "stdout": "gamma", "stderr": "delta", "error": "epsilon",
        "refused": "zeta", "inner_calls": [{"name": "shell", "input": {"command": "eta"}, "result": "theta"}],
    }}, {"type": "text", "text": "iota"}]}
    text = _extract_code_mode_text(msg)
    for w in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "shell", "eta", "theta"):
        assert w in text
    assert "iota" not in text
