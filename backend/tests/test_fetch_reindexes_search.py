"""Every fetch route must re-index search before it reports success.

Pre-fix bug (found 2026-09-23): the sidebar Refresh saved a new Desktop
conversation, and the Conversation List showed it at once. A search for
a word in that conversation still found nothing, and a repeat of the
search also found nothing. The fetch routes never touched the search
index. Only the watcher's backstop poll (every 10 minutes) indexed new
Desktop files, because the watcher observes the Claude Code and Cowork
directories but not the Desktop conversations directory. If the
watcher was down, the new conversation never became searchable.

The fix runs the search-index drift pass after each fetch saves its
files, before the route reports success. These tests pin that wiring
for the three routes that write conversations:

* ``GET /api/fetch/refresh`` (the sidebar Refresh button).
* ``GET /api/fetch/start`` (the Details modal buttons).
* ``POST /api/fetch/conversation/{uuid}`` (a single re-fetch).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_ORG = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


def _v2_creds() -> dict:
    return {
        "schema_version": 2,
        "session_key": "sk-test",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-09-23T00:00:00+00:00",
        "orgs": [
            {"uuid": _ORG, "name": None, "capabilities": [], "seen_in_response": False}
        ],
        "primary_org_id": _ORG,
        "legacy_migration_target": _ORG,
        "org_id": _ORG,
    }


class _ReadyIndex:
    """Stands in for a built ``SearchIndex``."""

    def is_ready(self) -> bool:
        return True


@pytest.fixture
def drift_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every drift pass instead of touching a real index."""
    calls: list[Any] = []

    def fake_update(store: Any, *, index: Any = None) -> int:
        calls.append(index)
        return 1

    monkeypatch.setattr("backend.search_index.get_search_index", lambda: _ReadyIndex())
    monkeypatch.setattr("backend.search_index.update_drifted_files", fake_update)
    return calls


@pytest.fixture
def fetch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Valid credentials plus tmp output dirs, and no network."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds()))
    out = tmp_path / "conversations"
    out.mkdir()
    monkeypatch.setattr("backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds)
    monkeypatch.setattr("backend.routers.fetch.DEFAULT_OUTPUT_DIR", out)
    monkeypatch.setattr("backend.routers.fetch.DEFAULT_FILES_DIR", tmp_path / "files")

    async def fail_capture(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("capture must not run with valid credentials")

    monkeypatch.setattr("backend.routers.fetch.capture_credentials", fail_capture)
    conv = {"uuid": "c-1", "name": "Hermes assistance", "updated_at": "2026-09-23T20:15:00Z"}
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        lambda self, *a, **k: [conv],
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation",
        lambda self, uuid, *a, **k: dict(conv, chat_messages=[]),
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_conversation",
        lambda self, c, *a, **k: None,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index",
        lambda self, *a, **k: None,
    )
    return out


def _event_types(text: str) -> list[str]:
    return [
        json.loads(line[len("data: "):])["type"]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def test_refresh_reindexes_search(
    client: TestClient, fetch_env: Path, drift_calls: list[Any]
) -> None:
    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    assert "complete" in _event_types(response.text), response.text
    assert len(drift_calls) == 1, "Refresh must run one search drift pass"


def test_fetch_start_reindexes_search(
    client: TestClient, fetch_env: Path, drift_calls: list[Any]
) -> None:
    response = client.get("/api/fetch/start?incremental=true")
    assert response.status_code == 200
    assert "complete" in _event_types(response.text), response.text
    assert len(drift_calls) == 1, "/fetch/start must run one search drift pass"


def test_single_refetch_reindexes_search(
    client: TestClient, fetch_env: Path, drift_calls: list[Any]
) -> None:
    response = client.post("/api/fetch/conversation/c-1")
    assert response.status_code == 200, response.text
    assert len(drift_calls) == 1, "a single re-fetch must run one search drift pass"


def test_reindex_skips_while_initial_build_runs(
    client: TestClient, fetch_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifespan build owns the index until it is ready."""
    calls: list[Any] = []

    class _Building:
        def is_ready(self) -> bool:
            return False

    monkeypatch.setattr("backend.search_index.get_search_index", lambda: _Building())
    monkeypatch.setattr(
        "backend.search_index.update_drifted_files",
        lambda store, *, index=None: calls.append(index) or 0,
    )
    response = client.get("/api/fetch/refresh?incremental=true")
    assert "complete" in _event_types(response.text), response.text
    assert calls == []


def test_reindex_failure_does_not_fail_the_fetch(
    client: TestClient, fetch_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversations are on disk. A search error must not hide that."""

    def boom(store: Any, *, index: Any = None) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr("backend.search_index.get_search_index", lambda: _ReadyIndex())
    monkeypatch.setattr("backend.search_index.update_drifted_files", boom)
    response = client.get("/api/fetch/refresh?incremental=true")
    types = _event_types(response.text)
    assert "complete" in types and "error" not in types, response.text
