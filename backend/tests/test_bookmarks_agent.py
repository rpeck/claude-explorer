"""Issue #96 addendum (PR #97): the agent id in bookmarks (SA-BOOKMARKS.md item 1).

* ``Bookmark`` and ``BookmarkCreate`` gain ``agent_id`` and
  ``agent_description`` (a snapshot, max 500 characters), both None by default.
* A set ``agent_id`` matches ``^[A-Za-z0-9_-]{1,64}$``, else 422. The route
  does not look the agent up: a bookmark may outlive its file.
* A bookmarks file without the fields loads unchanged; a file with them still
  loads in code that does not know them.
* The same message on the session and on an agent are two bookmarks.
* The corrupt-config gate applies as before.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.xfail(strict=True, reason="#97 agent bookmarks not implemented yet")

SESSION = "5a6e0000-0000-4000-8000-000000000096"
AGENT = "a5a0000000000096c"
DESCRIPTION = "Synthetic search agent"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Path]]:
    from fastapi.testclient import TestClient

    from backend.main import app

    path = tmp_path / "bookmarks.json"
    monkeypatch.setenv("CLAUDE_EXPLORER_BOOKMARKS_FILE", str(path))
    yield TestClient(app), path


def _payload(**extra: Any) -> dict[str, Any]:
    return {"conversation_id": SESSION, "message_uuid": "sa-t1", "source": "claude_code",
            "snippet": "I looked at the files", "note": "", **extra}


def _post_agent(client: Any) -> dict[str, Any]:
    r = client.post("/api/bookmarks", json=_payload(agent_id=AGENT, agent_description=DESCRIPTION))
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bookmark_keeps_the_agent_id_and_description(client: tuple[Any, Path]) -> None:
    c, path = client
    body = _post_agent(c)
    assert (body["agent_id"], body["agent_description"]) == (AGENT, DESCRIPTION)
    (listed,) = c.get("/api/bookmarks").json()["bookmarks"]
    assert (listed["agent_id"], listed["agent_description"]) == (AGENT, DESCRIPTION)
    (on_disk,) = json.loads(path.read_text())["bookmarks"]
    assert (on_disk["agent_id"], on_disk["agent_description"]) == (AGENT, DESCRIPTION)


def test_a_session_bookmark_has_both_fields_null(client: tuple[Any, Path]) -> None:
    c, _ = client
    r = c.post("/api/bookmarks", json=_payload())
    assert r.status_code == 201
    assert (r.json()["agent_id"], r.json()["agent_description"]) == (None, None)


def test_an_explicit_null_agent_id_is_a_session_bookmark(client: tuple[Any, Path]) -> None:
    c, _ = client
    r = c.post("/api/bookmarks", json=_payload(agent_id=None, agent_description=None))
    assert r.status_code == 201, r.text
    assert r.json()["agent_id"] is None


@pytest.mark.parametrize("bad", ["", "a.b", "a" * 65, "a b", "../x", "a/b", "é"],
                         ids=["empty", "dot", "too-long", "space", "traversal", "slash", "non-ascii"])
def test_an_invalid_agent_id_is_422_and_nothing_is_written(client: tuple[Any, Path], bad: str) -> None:
    c, path = client
    _post_agent(c)  # a valid agent bookmark works, so the 422 comes from the id check
    r = c.post("/api/bookmarks", json=_payload(agent_id=bad))
    assert r.status_code == 422
    assert len(json.loads(path.read_text())["bookmarks"]) == 1


def test_the_longest_valid_agent_id_is_accepted(client: tuple[Any, Path]) -> None:
    c, _ = client
    r = c.post("/api/bookmarks", json=_payload(agent_id="a" * 64))
    assert r.status_code == 201, r.text
    assert r.json()["agent_id"] == "a" * 64


def test_an_agent_description_over_500_characters_is_422(client: tuple[Any, Path]) -> None:
    c, _ = client
    assert c.post("/api/bookmarks", json=_payload(agent_id=AGENT, agent_description="x" * 500)).status_code == 201
    assert c.post("/api/bookmarks", json=_payload(agent_id=AGENT, agent_description="x" * 501)).status_code == 422


def test_the_route_does_not_look_the_agent_up(client: tuple[Any, Path]) -> None:
    """No session or agent file exists in this test: a bookmark may outlive its file."""
    c, _ = client
    r = c.post("/api/bookmarks", json=_payload(conversation_id="no-such-session", agent_id="anoagent0000"))
    assert r.status_code == 201, r.text
    assert r.json()["agent_id"] == "anoagent0000"


def test_the_same_message_on_the_session_and_on_an_agent_are_two_bookmarks(client: tuple[Any, Path]) -> None:
    c, _ = client
    assert c.post("/api/bookmarks", json=_payload()).status_code == 201
    _post_agent(c)
    rows = c.get("/api/bookmarks").json()["bookmarks"]
    assert sorted((r["message_uuid"], r["agent_id"] or "") for r in rows) == [("sa-t1", ""), ("sa-t1", AGENT)]


def test_an_old_file_without_the_fields_loads_unchanged(client: tuple[Any, Path]) -> None:
    c, path = client
    old = {"id": "bm-old", "conversation_id": SESSION, "message_uuid": "m1", "source": "claude_code",
           "created_at": "2026-04-01T10:00:00+00:00", "note": "n", "snippet": "s"}
    path.write_text(json.dumps({"bookmarks": [old]}))
    (listed,) = c.get("/api/bookmarks").json()["bookmarks"]
    assert {k: listed[k] for k in old} == old
    assert (listed["agent_id"], listed["agent_description"]) == (None, None)


def test_a_file_with_the_fields_loads_in_code_that_does_not_know_them(client: tuple[Any, Path]) -> None:
    """The on-disk shape only adds keys: the model of the last release still reads it."""
    c, path = client
    _post_agent(c)

    class OldBookmark(BaseModel):  # backend/routers/bookmarks.py before #97
        id: str
        conversation_id: str
        message_uuid: str
        source: Literal["claude_desktop", "claude_code"]
        created_at: str
        note: str = ""
        snippet: str = ""

    (item,) = json.loads(path.read_text())["bookmarks"]
    old = OldBookmark(**item)
    assert (old.conversation_id, old.message_uuid) == (SESSION, "sa-t1")
    assert item["agent_id"] == AGENT


def test_a_note_edit_keeps_the_agent_fields(client: tuple[Any, Path]) -> None:
    c, _ = client
    created = _post_agent(c)
    r = c.patch(f"/api/bookmarks/{created['id']}", json={"note": "check this"})
    assert r.status_code == 200
    assert (r.json()["note"], r.json()["agent_id"], r.json()["agent_description"]) == ("check this", AGENT, DESCRIPTION)


def test_the_corrupt_config_gate_refuses_an_agent_bookmark(
    client: tuple[Any, Path], tmp_path: Path
) -> None:
    from backend import config
    from backend.main import app

    c, path = client
    _post_agent(c)
    corrupt = config.Settings(data_dir=tmp_path / "data", claude_dir=tmp_path / "claude",
                              claude_desktop_app_dir=tmp_path / "app", config_corrupt_reason="config.json: bad")
    app.dependency_overrides[config.get_settings] = lambda: corrupt
    try:
        r = c.post("/api/bookmarks", json=_payload(agent_id=AGENT, message_uuid="sa-u1"))
    finally:
        app.dependency_overrides.pop(config.get_settings, None)
    assert r.status_code == 503
    assert len(json.loads(path.read_text())["bookmarks"]) == 1
