"""Issue #96: ``GET /api/conversations/{uuid}/subagents/{agent_id}``.

* The body is a ``ConversationDetail``: ``uuid`` is the session uuid, and
  ``agent_id`` and ``agent_description`` are set (plan #46 §4.5, Step 1.2).
* ``agent_id`` must match ``^[A-Za-z0-9_-]{1,64}$``, else 422.
* The route finds the agent only through discovery, never by a path built
  from the request: an unknown id, an internal run, an agent of another
  session, and a symlink outside the project give 404.
* Code Mode runs fold as in a session.
* The export routes take ``agent_id`` and export the agent transcript.

The tree is ``test_subagent_discovery.write_tree``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_subagent_discovery import (
    ASIDE_RUN,
    BAD_META_AGENT,
    COMPACT_RUN,
    CUR_AGENT,
    CUR_DESCRIPTION,
    CUR_MESSAGE,
    ESCAPE_AGENT,
    GARBAGE_AGENT,
    LEGACY_AGENT,
    LEGACY_MESSAGE,
    NO_OWNER_LEGACY_AGENT,
    OTHER_LEGACY_AGENT,
    OTHER_SESSION,
    PROJECT,
    REAL_AGENT,
    REAL_AGENT_REPLY_UUID,
    REAL_DESCRIPTION,
    REAL_SESSION,
    SESSION,
    WORDS,
    close_tree,
    use_tree,
)

#: The first user message of the real parent session; never in the agent payload.
REAL_PARENT_FIRST = "d99f0627-dd79-4418-a2bb-f450ac2edc10"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from backend.main import app

    use_tree(tmp_path, monkeypatch)
    try:
        yield TestClient(app)
    finally:
        close_tree()


def _get(client: Any, session: str, agent: str) -> Any:
    return client.get(f"/api/conversations/{session}/subagents/{agent}")


def _detail(client: Any, session: str, agent: str) -> Any:
    from backend.models import ConversationDetail

    resp = _get(client, session, agent)
    assert resp.status_code == 200, resp.text
    # The route returns OrjsonResponse: response_model proves nothing, so parse the real body.
    return ConversationDetail.model_validate(resp.json())


def _route_works(client: Any) -> None:
    """A precondition of each negative test: a 404 must come from the lookup, not a missing route."""
    assert _get(client, SESSION, CUR_AGENT).status_code == 200


def _blocks(detail: Any) -> list[Any]:
    return [b for m in detail.messages for b in m.content]


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


def test_the_real_agent_is_a_conversation_detail_with_the_session_uuid(client: Any) -> None:
    detail = _detail(client, REAL_SESSION, REAL_AGENT)
    assert detail.uuid == REAL_SESSION
    assert detail.agent_id == REAL_AGENT
    assert detail.agent_description == REAL_DESCRIPTION
    assert detail.source == "CLAUDE_CODE"


def test_the_name_is_the_description(client: Any) -> None:
    assert _detail(client, REAL_SESSION, REAL_AGENT).name == REAL_DESCRIPTION


def test_without_a_description_the_name_is_agent_and_the_id(client: Any) -> None:
    detail = _detail(client, SESSION, LEGACY_AGENT)
    assert detail.agent_description is None
    assert detail.name == f"Agent {LEGACY_AGENT}"


def test_the_messages_are_the_agent_transcript_not_the_session(client: Any) -> None:
    uuids = [m.uuid for m in _detail(client, REAL_SESSION, REAL_AGENT).messages]
    assert REAL_AGENT_REPLY_UUID in uuids
    assert REAL_PARENT_FIRST not in uuids


def test_the_code_mode_run_folds_as_in_a_session(client: Any) -> None:
    runs = [b for b in _blocks(_detail(client, REAL_SESSION, REAL_AGENT)) if b.type == "code_run"]
    assert len(runs) == 1
    assert runs[0].code_run is not None and runs[0].code_run.result == "app.py\nutil.py"
    assert [c.name for c in runs[0].code_run.inner_calls] == ["Bash"]


def test_the_tool_calls_and_the_compaction_are_in_the_payload(client: Any) -> None:
    """The client applies Show Tools and Show Compactions, as for a session."""
    detail = _detail(client, SESSION, CUR_AGENT)
    assert detail.agent_description == CUR_DESCRIPTION
    assert any(b.type == "tool_use" and b.name == "Bash" for b in _blocks(detail))
    assert [m.message_uuid for m in detail.compact_markers] == [CUR_MESSAGE["compact"]]


def test_the_project_is_the_session_project(client: Any) -> None:
    agent = _detail(client, SESSION, CUR_AGENT)
    session = client.get(f"/api/conversations/{SESSION}").json()
    assert agent.project_path == session["project_path"]


def test_the_file_path_is_the_agent_file(client: Any) -> None:
    path = Path(_detail(client, SESSION, CUR_AGENT).file_path or "")
    assert path.name == f"agent-{CUR_AGENT}.jsonl"
    assert path.parent.name == "subagents"


def test_a_legacy_agent_opens(client: Any) -> None:
    uuids = [m.uuid for m in _detail(client, SESSION, LEGACY_AGENT).messages]
    assert LEGACY_MESSAGE in uuids


def test_an_agent_with_a_bad_meta_file_opens(client: Any) -> None:
    assert _detail(client, SESSION, BAD_META_AGENT).agent_description is None


def test_the_session_payload_has_no_agent_id(client: Any) -> None:
    _route_works(client)
    body = client.get(f"/api/conversations/{REAL_SESSION}").json()
    assert body.get("agent_id") is None
    assert REAL_PARENT_FIRST in [m["uuid"] for m in body["messages"]]


def test_the_session_payload_has_no_agent_messages(client: Any) -> None:
    _route_works(client)
    body = client.get(f"/api/conversations/{REAL_SESSION}").json()
    assert REAL_AGENT_REPLY_UUID not in [m["uuid"] for m in body["messages"]]


# ---------------------------------------------------------------------------
# subagents_found on the session detail (the entry point on the session page)
# ---------------------------------------------------------------------------


def test_the_session_detail_lists_its_agents_with_descriptions(client: Any) -> None:
    body = client.get(f"/api/conversations/{REAL_SESSION}").json()
    assert body["subagents_found"] == [{"agent_id": REAL_AGENT, "agent_description": REAL_DESCRIPTION}]


def test_subagents_found_holds_both_layouts_and_no_internal_runs(client: Any) -> None:
    found = client.get(f"/api/conversations/{SESSION}").json()["subagents_found"]
    ids = [f["agent_id"] for f in found]
    assert {CUR_AGENT, LEGACY_AGENT, BAD_META_AGENT} <= set(ids)
    assert not {COMPACT_RUN, ASIDE_RUN, ESCAPE_AGENT, OTHER_LEGACY_AGENT, NO_OWNER_LEGACY_AGENT} & set(ids)
    by_id = {f["agent_id"]: f["agent_description"] for f in found}
    assert by_id[CUR_AGENT] == CUR_DESCRIPTION
    assert by_id[LEGACY_AGENT] is None


def test_subagents_found_is_the_discovery_order(client: Any) -> None:
    ids = [f["agent_id"] for f in client.get(f"/api/conversations/{SESSION}").json()["subagents_found"]]
    assert ids == sorted(ids)


def test_another_session_lists_only_its_own_legacy_agent(client: Any) -> None:
    body = client.get(f"/api/conversations/{OTHER_SESSION}").json()
    assert [f["agent_id"] for f in body["subagents_found"]] == [OTHER_LEGACY_AGENT]


def test_subagents_found_is_never_on_list_rows(client: Any) -> None:
    _route_works(client)
    rows = client.get("/api/conversations").json()
    assert rows and all("subagents_found" not in r for r in rows)


def test_every_subagents_found_entry_opens(client: Any) -> None:
    for found in client.get(f"/api/conversations/{SESSION}").json()["subagents_found"]:
        if found["agent_id"] == GARBAGE_AGENT:
            continue  # no rows: listing it, and its status, are the coder's choice
        assert _get(client, SESSION, found["agent_id"]).status_code == 200, found


# ---------------------------------------------------------------------------
# Input safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a.b", "a" * 65, "a$b", "a%20b", "...", "a%2Eb", "%C3%A9"],
                         ids=["dot", "too-long", "dollar", "space", "dots", "encoded-dot", "non-ascii"])
def test_an_invalid_agent_id_is_422(client: Any, bad: str) -> None:
    assert _get(client, SESSION, bad).status_code == 422


def test_the_longest_valid_id_reaches_the_lookup(client: Any) -> None:
    _route_works(client)
    assert _get(client, SESSION, "a" * 64).status_code == 404


@pytest.mark.parametrize("agent", ["aunknown0000096", COMPACT_RUN, ASIDE_RUN, ESCAPE_AGENT,
                                   OTHER_LEGACY_AGENT, NO_OWNER_LEGACY_AGENT],
                         ids=["unknown", "compact-run", "aside-run", "symlink-outside", "other-session",
                              "no-owner"])
def test_an_agent_that_discovery_does_not_list_is_404(client: Any, agent: str) -> None:
    _route_works(client)
    assert _get(client, SESSION, agent).status_code == 404


def test_an_agent_of_another_session_is_404_under_this_session(client: Any) -> None:
    _route_works(client)
    assert _get(client, REAL_SESSION, CUR_AGENT).status_code == 404


def test_an_unknown_session_is_404(client: Any) -> None:
    _route_works(client)
    assert _get(client, "00000000-0000-4000-8000-000000000000", CUR_AGENT).status_code == 404


def test_the_symlink_target_text_never_reaches_the_client(client: Any) -> None:
    _route_works(client)
    resp = _get(client, SESSION, ESCAPE_AGENT)
    assert WORDS["escape"] not in resp.text


def test_a_file_named_after_the_request_is_not_read_without_discovery(
    client: Any, tmp_path: Path
) -> None:
    """A file in the project dir that is not an agent of this session (no
    ``agent-`` prefix) is never served, even when its stem matches the id."""
    _route_works(client)
    proj = tmp_path / "corpus" / "claude" / "projects" / PROJECT
    (proj / "aplain000000096.jsonl").write_text((proj / f"agent-{LEGACY_AGENT}.jsonl").read_text())
    assert _get(client, SESSION, "aplain000000096").status_code == 404


def test_a_traversal_target_through_the_raw_asgi_stack_is_rejected(client: Any) -> None:
    """The exact request target is sent without client normalization (plan #46 route tests)."""
    _route_works(client)
    import asyncio

    from backend.main import app

    sent: list[int] = []

    async def run() -> None:
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": f"/api/conversations/{SESSION}/subagents/../../{SESSION}",
            "raw_path": f"/api/conversations/{SESSION}/subagents/%2E%2E%2F%2E%2E%2F{SESSION}".encode(),
            "query_string": b"", "headers": [(b"host", b"localhost")], "client": ("127.0.0.1", 1),
            "server": ("localhost", 80), "root_path": "",
        }

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                sent.append(message["status"])

        await app(scope, receive, send)

    asyncio.run(run())
    assert sent and sent[0] in (404, 422)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["markdown", "markdown-bundle", "pdf"])
def test_an_export_with_agent_id_exports_the_agent(client: Any, kind: str) -> None:
    if kind == "pdf":
        pytest.importorskip("weasyprint")
    resp = client.get(f"/api/conversations/{SESSION}/export/{kind}", params={"agent_id": CUR_AGENT})
    assert resp.status_code == 200, resp.text
    assert CUR_AGENT in resp.headers.get("content-disposition", "")


def test_the_markdown_export_of_an_agent_holds_its_text_not_the_session(client: Any) -> None:
    resp = client.get(f"/api/conversations/{SESSION}/export/markdown", params={"agent_id": CUR_AGENT})
    assert resp.status_code == 200, resp.text
    assert WORDS["text"] in resp.text
    assert WORDS["session"] not in resp.text


def test_an_export_follows_the_code_mode_flag_for_an_agent(client: Any) -> None:
    url = f"/api/conversations/{REAL_SESSION}/export/markdown"
    on = client.get(url, params={"agent_id": REAL_AGENT, "include_code_mode": "true"})
    off = client.get(url, params={"agent_id": REAL_AGENT, "include_code_mode": "false"})
    assert on.status_code == off.status_code == 200
    assert "nothrow" in on.text and "nothrow" not in off.text


@pytest.mark.parametrize(("agent", "status"), [("a.b", 422), ("aunknown0000096", 404), (ESCAPE_AGENT, 404)])
def test_an_export_checks_the_agent_id_like_the_route(client: Any, agent: str, status: int) -> None:
    resp = client.get(f"/api/conversations/{SESSION}/export/markdown", params={"agent_id": agent})
    assert resp.status_code == status


def test_an_export_without_agent_id_is_the_session(client: Any) -> None:
    _route_works(client)
    resp = client.get(f"/api/conversations/{SESSION}/export/markdown")
    assert resp.status_code == 200
    assert WORDS["session"] in resp.text and WORDS["text"] not in resp.text
