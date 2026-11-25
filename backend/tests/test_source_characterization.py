"""Characterization goldens for the web API: list, viewer, search, Markdown.

They record today's outputs for the three sources, so the source adapter
refactor (N1) can prove that it changes nothing. See
``backend/tests/goldens/corpus.py`` for the corpus and the normalization
rules. ``UPDATE_GOLDENS=1`` rewrites the goldens.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import search as search_mod
from backend import search_index as si
from backend.main import app
from backend.tests.goldens.corpus import (
    CONVERSATION_IDS,
    CONVERSATIONS,
    SEARCH_TOKENS,
    SOURCES,
    Corpus,
    build_corpus,
    check_golden,
    normalize,
)

#: The list queries: golden key -> query parameters.
LIST_QUERIES: dict[str, dict[str, str]] = {
    "default": {},
    "show_archived": {"show_archived": "true"},
    "sort_name_asc": {"sort": "name", "sort_order": "asc"},
    "sort_project": {"sort": "project"},
    **{f"source_{s}": {"source": s} for s in SOURCES},
}


@pytest.fixture
def corpus(tmp_path, monkeypatch) -> Corpus:
    return build_corpus(tmp_path, monkeypatch)


@pytest.fixture
def api() -> TestClient:
    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def _get_json(api: TestClient, corpus: Corpus, url: str, params: dict | None = None):
    r = api.get(url, params=params or {})
    assert r.status_code == 200, (url, params, r.text)
    return normalize(r.json(), corpus.root)


def test_corpus_has_every_source(corpus, api):
    """Guard for the goldens: a fixture that fails to load would record an
    empty list as the truth."""
    rows = _get_json(api, corpus, "/api/conversations", {"show_archived": "true"})
    assert sorted((r["source"], r["uuid"]) for r in rows) == sorted(
        [
            ("CLAUDE_AI", "0f415a45-9c62-8671-d4ad-53b84acb7e1a"),
            ("CLAUDE_AI", "a3c33811-ab27-c195-6938-54121b8673e4"),
            ("CLAUDE_AI", "c8da94ab-641d-bf28-d4a6-cba9bc72468e"),
            ("CLAUDE_CODE", "435e56ed-50ba-21c0-3495-0e8749b20543"),
            # Issue #90: the real Code Mode recordings.
            ("CLAUDE_CODE", "2339aab4-744c-4556-95b8-5ee5552c1b85"),
            ("CLAUDE_CODE", "b007d327-80c4-46ea-b737-53d84b30c624"),
            ("CLAUDE_COWORK", "aaaa1111-2222-3333-4444-555566667777"),
            ("CLAUDE_COWORK", "session_01FIXTUREREMOTE000000000001"),
        ]
    )


def test_list_golden(corpus, api):
    out = {
        key: _get_json(api, corpus, "/api/conversations", params)
        for key, params in LIST_QUERIES.items()
    }
    check_golden("api/list.json", out)


@pytest.mark.parametrize(("stem", "uuid"), CONVERSATIONS, ids=CONVERSATION_IDS)
def test_viewer_golden(corpus, api, stem, uuid):
    detail = _get_json(api, corpus, f"/api/conversations/{uuid}")
    tree = _get_json(api, corpus, f"/api/conversations/{uuid}/tree")
    check_golden(f"api/viewer/{stem}.json", {"detail": detail, "tree": tree})


@pytest.mark.parametrize(("stem", "uuid"), CONVERSATIONS, ids=CONVERSATION_IDS)
def test_markdown_export_golden(corpus, api, stem, uuid):
    for include_tools in ("true", "false"):
        r = api.get(
            f"/api/conversations/{uuid}/export/markdown",
            params={"include_tools": include_tools},
        )
        assert r.status_code == 200, r.text
        body = normalize(r.content.decode("utf-8"), corpus.root)
        suffix = "" if include_tools == "true" else "-no-tools"
        check_golden(f"api/markdown/{stem}{suffix}.md", body)


def _search_queries() -> dict[str, dict[str, str]]:
    queries = {f"q_{t}": {"q": t} for t in SEARCH_TOKENS}
    # "fixture" is in a title or a body of every source.
    queries["q_fixture"] = {"q": "fixture"}
    queries.update({f"q_fixture_{s}": {"q": "fixture", "source": s} for s in SOURCES})
    queries["q_fixture_full"] = {"q": "fixture", "context_size": "full"}
    return queries


def test_search_fts_golden(corpus, api, monkeypatch):
    """The FTS path. The linear scan raises, so a silent fallback fails."""

    def _no_linear(*_a, **_k):
        raise AssertionError("search fell back to the linear scan")

    monkeypatch.setattr(search_mod, "_search_via_linear_scan", _no_linear)
    assert si.get_search_index() is corpus.index
    out = {
        key: _get_json(api, corpus, "/api/search", params)
        for key, params in _search_queries().items()
    }
    check_golden("api/search-fts.json", out)


def test_search_linear_scan_golden(corpus, api, monkeypatch):
    """The linear-scan fallback, with no index."""
    monkeypatch.setattr(si, "get_search_index", lambda: None)

    def _no_index(*_a, **_k):
        raise AssertionError("search used the index")

    for name in ("_search_via_index", "_search_via_index_fast", "_search_via_index_fast_full"):
        monkeypatch.setattr(search_mod, name, _no_index)
    out = _get_json(api, corpus, "/api/search", {"q": "fixture"})
    check_golden("api/search-linear.json", out)
