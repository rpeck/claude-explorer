"""Issue #90, #80 option B: the search tells whether the archive holds Code Mode runs.

The Search Pane names "hidden Code Mode runs" on an empty result whenever the
archive holds at least one run, so the user knows that hidden content might
match and can widen the search. The search response carries
``has_code_runs`` for that:

* With the index ready, one cheap check answers it: ``EXISTS`` over the
  ``conversations`` table, whose ``has_code_runs`` column the index writes
  with each conversation (``SCHEMA_VERSION`` 18).
* Before the index is ready, a scan of the conversations stops at the first
  run, so the answer does not change during the first index build.
* The answer is for the whole archive, not for the query or its scope.

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
PATHS = ("fts-snippet", "fts-full", "linear")
NO_MATCH = "qqzzuunomatch"


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_runs: bool) -> Any:
    """One ordinary Claude Code session, plus the Code Mode session when ``with_runs``. Return the index."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache

    root = tmp_path / "corpus"
    (root / "data").mkdir(parents=True)
    plain = "claude/projects/-fixture-project"
    shutil.copytree(FIXTURES_DIR / plain, root / plain)
    if with_runs:
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


@pytest.fixture(params=[True, False], ids=["with-runs", "without-runs"])
def corpus(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, bool]]:
    from backend.store import ConversationStore
    from backend.tests.test_search_all_sources import close_e2e_corpus

    idx = _build(tmp_path, monkeypatch, request.param)
    try:
        yield ConversationStore(), request.param
    finally:
        close_e2e_corpus(idx)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("code", [False, True], ids=["code-off", "code-on"])
def test_the_response_says_whether_the_archive_holds_runs(
    corpus: tuple[Any, bool], path: str, code: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.search import search_conversations
    from backend.tests.test_search_all_sources import use_path

    store, with_runs = corpus
    with monkeypatch.context() as m:
        resp = search_conversations(store, NO_MATCH, context_size=use_path(path, m), include_code_mode=code)
    assert resp.results == []
    assert resp.has_code_runs is with_runs


@pytest.mark.parametrize("path", PATHS)
def test_the_answer_ignores_the_scope(corpus: tuple[Any, bool], path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.search import search_conversations
    from backend.tests.test_search_all_sources import use_path

    store, with_runs = corpus
    with monkeypatch.context() as m:
        resp = search_conversations(
            store, NO_MATCH, context_size=use_path(path, m), conversation_uuid="00000000-not-a-session")
    assert resp.has_code_runs is with_runs


def test_the_index_answers_with_one_check_and_follows_a_delete(corpus: tuple[Any, bool]) -> None:
    from backend import search_index as si

    _, with_runs = corpus
    idx = si._search_index
    assert idx.has_code_runs() is with_runs
    if with_runs:
        session_file = next(Path(si.default_index_path()).parent.glob(f"claude/projects/*/{fake.SESSION}.jsonl"))
        agent_file = next(Path(si.default_index_path()).parent.glob(f"claude/projects/*/{fake.SESSION}/subagents/*.jsonl"))
        idx.delete_by_path(session_file)
        idx.delete_by_path(agent_file)
        assert idx.has_code_runs() is False


def test_the_search_api_returns_has_code_runs(corpus: tuple[Any, bool]) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    _, with_runs = corpus
    r = TestClient(app).get("/api/search", params={"q": NO_MATCH})
    assert r.status_code == 200
    assert r.json()["has_code_runs"] is with_runs


def test_the_conversations_table_has_the_column_at_schema_18(corpus: tuple[Any, bool]) -> None:
    from backend import search_index as si

    assert si.SCHEMA_VERSION == 18
    cols = {r[1] for r in si._search_index._get_read_conn().execute("PRAGMA table_info(conversations)")}
    assert "has_code_runs" in cols
