"""Tests for /api/search scope filters: conversation_uuid, project_path, bookmarks.

Manual finding 2026-05-04: full-text search must be scopable to a single
conversation, a project (set of CC sessions sharing project_path), or a
set of bookmarked conversations. The default remains unscoped (subject
only to the existing `source` filter).

Backend filter, not client post-filter. Tool_use payloads are huge;
sending them across the wire only to discard is wasteful and breaks
ranking.
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app


def _conv(uuid: str, name: str, *, project_path: str | None = None, source: str = "CLAUDE_AI", text: str = "needle in haystack"):
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": "m1",
        "project_path": project_path,
        "source": source,
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "text": text,
                "content": [{"type": "text", "text": text}],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }


@pytest.fixture
def scope_data_dir(tmp_path, monkeypatch):
    """Three CC conversations: two in projectA, one in projectB. All match 'needle'."""
    convs = [
        _conv("conv-a1", "ProjectA session 1", project_path="/work/projectA"),
        _conv("conv-a2", "ProjectA session 2", project_path="/work/projectA"),
        _conv("conv-b1", "ProjectB session", project_path="/work/projectB"),
    ]
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    for c in convs:
        (by_org / f"{c['uuid']}.json").write_text(json.dumps(c))
    # Empty claude_dir so the CC reader doesn't pick up the user's real
    # ~/.claude/projects sessions.
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()
    yield tmp_path
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_search_unscoped_returns_all_three(scope_data_dir):
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle"})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == ["conv-a1", "conv-a2", "conv-b1"]


def test_search_conversation_uuid_returns_one(scope_data_dir):
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "conversation_uuid": "conv-a2"})
    assert r.status_code == 200
    items = r.json()["results"]
    assert [i["conversation_uuid"] for i in items] == ["conv-a2"]


def test_search_project_path_returns_two(scope_data_dir):
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "project_path": "/work/projectA"})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == ["conv-a1", "conv-a2"]


def test_search_bookmarks_csv_filters_to_set(scope_data_dir):
    client = TestClient(app)
    # Comma-separated UUID list; only conv-a1 and conv-b1 should match.
    r = client.get(
        "/api/search",
        params={"q": "needle", "bookmarks": "conv-a1,conv-b1"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == ["conv-a1", "conv-b1"]


def test_search_conversation_uuid_overrides_project_path(scope_data_dir):
    """If both are passed, conversation_uuid is the more specific filter; it wins."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "needle", "conversation_uuid": "conv-b1", "project_path": "/work/projectA"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == ["conv-b1"]


def test_search_unknown_conversation_uuid_returns_empty(scope_data_dir):
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "conversation_uuid": "does-not-exist"})
    assert r.status_code == 200
    assert r.json()["results"] == []
