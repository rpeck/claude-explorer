"""Remote Cowork tasks reach the user: fetch SSE paths, store, search index.

Regression for the 2026-10-14 work-laptop report. The account's history is
entirely Cowork tasks run in Anthropic's cloud; ``chat_conversations``
returns ``[]``, so Refresh reported success with 0 conversations and the
sidebar stayed empty. These tests pin the user-observable contract:

* Refresh (``/api/fetch/refresh``) and Fetch New (``/api/fetch/start``)
  archive the tasks and count them in the ``complete`` message.
* The archived tasks list under the Claude Cowork source and open with
  their messages in order.
* The search index enumerates them, so they are searchable after a fetch.
* An org without sessions-API access is skipped, never an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.store import ConversationStore
from fetcher.tests.ccr_fake import (
    FakeSession,
    FakeSessionsApi,
    conversation_events,
    iso,
)

ORG = "11111111-1111-4111-8111-111111111111"
PERSONAL = "22222222-2222-4222-8222-222222222222"


def _creds() -> dict:
    return {
        "schema_version": 2,
        "session_key": "sk-test-fake-key",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-10-13T15:47:00+00:00",
        "orgs": [
            {"uuid": ORG, "name": "Analytical Engines", "capabilities": ["chat"], "seen_in_response": True},
            {"uuid": PERSONAL, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True},
        ],
        "primary_org_id": ORG,
        "legacy_migration_target": ORG,
        "org_id": ORG,
    }


def _frames(body: str) -> list[dict]:
    out = []
    for chunk in body.split("\n\n"):
        line = chunk.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds()))
    out = tmp_path / "conversations"
    out.mkdir()
    monkeypatch.setattr("backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds)
    monkeypatch.setattr("backend.routers.fetch.fetch_output_dir", lambda _v=out: _v)
    monkeypatch.setattr("backend.routers.fetch.fetch_files_dir", lambda _v=tmp_path / "files": _v)
    # The chat API has nothing for this account: the reported situation.
    monkeypatch.setattr("backend.routers.fetch.ClaudeFetcher.fetch_conversation_list", lambda self: [])
    monkeypatch.setattr("backend.routers.fetch.asyncio.sleep", _no_sleep)
    return {"out": out, "tmp": tmp_path}


async def _no_sleep(_seconds: float) -> None:
    return None


def _tasks() -> list[FakeSession]:
    return [
        FakeSession(
            "cse_01ExampleB_long", "Editor configuration",
            conversation_events(1, 30), updated_at=iso(200),
        ),
        FakeSession(
            "cse_01ExampleA_short", "Daily summary",
            conversation_events(1, 3), updated_at=iso(100), model="claude-fable-5-1",
        ),
    ]


def test_refresh_archives_cowork_tasks_when_the_chat_list_is_empty(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi({ORG: _tasks(), PERSONAL: []}).install(monkeypatch)

    r = client.get("/api/fetch/refresh?incremental=true")

    assert r.status_code == 200
    events = _frames(r.text)
    assert [e["type"] for e in events if e["type"] == "error"] == []
    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["message"] == (
        "Fetched 0 conversations and 2 Cowork tasks across 2 workspace(s)."
    )
    assert complete["cowork_tasks_fetched"] == 2
    progress = [e["message"] for e in events if e["type"] == "progress"]
    assert "[Analytical Engines] Fetching Cowork task: Editor configuration" in progress

    store = ConversationStore(data_dir=env["out"], cowork_root=env["tmp"] / "no-local-cowork")
    listed = store.list_conversations(source="CLAUDE_COWORK")
    assert {c.name for c in listed} == {"Editor configuration", "Daily summary"}
    assert {c.uuid for c in store.list_conversations(source="all")} >= {
        "cse_01ExampleB_long", "cse_01ExampleA_short",
    }
    detail = store.get_conversation("cse_01ExampleB_long")
    assert detail is not None and detail.source == "CLAUDE_COWORK"
    assert len(detail.messages) == 60
    assert [m.sender for m in detail.messages[:2]] == ["human", "assistant"]
    assert detail.messages[0].text.startswith("question 0")
    assert detail.messages[-1].text == "answer 29"


def test_second_refresh_reports_no_new_tasks(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeSessionsApi({ORG: _tasks()}).install(monkeypatch)
    client.get("/api/fetch/refresh?incremental=true")
    api.requests.clear()

    r = client.get("/api/fetch/refresh?incremental=true")

    complete = _frames(r.text)[-1]
    assert complete["message"] == "Fetched 0 conversations across 2 workspace(s)."
    assert complete["cowork_tasks_fetched"] == 0
    assert not any("/events" in u for u in api.requests)


def test_org_without_sessions_api_access_is_skipped_quietly(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi({ORG: _tasks()}, list_status={PERSONAL: 403}).install(monkeypatch)

    r = client.get("/api/fetch/refresh?incremental=true")

    events = _frames(r.text)
    assert "error" not in [e["type"] for e in events]
    assert not any("Personal" in e.get("message", "") and "Cowork" in e.get("message", "") for e in events)
    assert events[-1]["cowork_tasks_fetched"] == 2


def test_tasks_api_outage_is_reported_but_the_fetch_still_completes(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi({ORG: _tasks()}, list_status={ORG: 500, PERSONAL: 500}).install(monkeypatch)

    r = client.get("/api/fetch/refresh?incremental=true")

    events = _frames(r.text)
    assert events[-1]["type"] == "complete"
    assert any(
        e["type"] == "progress" and e["message"].startswith("[Analytical Engines] Could not list Cowork tasks")
        for e in events
    )


def test_fetch_new_in_details_modal_also_archives_tasks(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi({ORG: _tasks()}).install(monkeypatch)
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index", lambda self, c, **kw: None
    )

    r = client.get("/api/fetch/start?incremental=true")

    complete = _frames(r.text)[-1]
    assert complete == {
        "type": "complete",
        "message": "Fetched 0 conversations and 2 Cowork tasks successfully.",
        "cowork_tasks_fetched": 2,
        "cowork_tasks_failed": 0,
        "current": 0,
        "total": 0,
    }


def test_search_index_enumerates_and_resolves_remote_tasks(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.search_index import _enumerate_conversation_paths, _file_path_for_conv

    FakeSessionsApi({ORG: _tasks()}).install(monkeypatch)
    client.get("/api/fetch/refresh?incremental=true")

    store = ConversationStore(data_dir=env["out"], cowork_root=env["tmp"] / "no-local-cowork")
    paths = _enumerate_conversation_paths(store)
    remote = sorted(p for p, src in paths if src == "CLAUDE_COWORK")
    assert [p.parent.name for p in remote] == [
        "remote_cse_01ExampleA_short", "remote_cse_01ExampleB_long",
    ]
    resolved = _file_path_for_conv(
        {"uuid": "cse_01ExampleB_long", "source": "CLAUDE_COWORK"},
        env["out"], env["tmp"] / "no-claude",
    )
    assert resolved == remote[1]


def test_refresh_redownloads_a_conversation_the_server_updated(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Incremental Refresh compares ``updated_at``, not just presence on disk."""
    org_dir = env["out"] / "by-org" / ORG
    org_dir.mkdir(parents=True)
    (org_dir / "conv-1.json").write_text(json.dumps({
        "uuid": "conv-1", "name": "Synology NAS", "updated_at": "2026-09-01T00:00:00Z",
        "chat_messages": [],
    }))
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        lambda self: [
            {"uuid": "conv-1", "name": "Synology NAS", "updated_at": "2026-10-14T00:00:00Z"}
        ] if self.current_org["uuid"] == ORG else [],
    )
    fetched: list[str] = []

    def fake_fetch(self, uuid: str) -> dict:
        fetched.append(uuid)
        return {"uuid": uuid, "name": "Synology NAS", "updated_at": "2026-10-14T00:00:00Z",
                "chat_messages": []}

    monkeypatch.setattr("backend.routers.fetch.ClaudeFetcher.fetch_conversation", fake_fetch)

    r = client.get("/api/fetch/refresh?incremental=true")

    assert fetched == ["conv-1"]
    assert _frames(r.text)[-1]["message"] == "Fetched 1 conversations across 2 workspace(s)."


# ---------------------------------------------------- failed tasks are shown
#
# PR #6 review (2026-10-19): a task whose events call kept failing was
# counted in totals["failed"] but never reported. The progress frame showed
# only "Fetching i/N...", and the complete message said "successfully", so
# the task stayed missing, Refresh after Refresh, with no sign of it.


def test_refresh_names_a_task_that_failed(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi({ORG: _tasks()}, broken_sessions={"cse_01ExampleA_short"}).install(monkeypatch)

    r = client.get("/api/fetch/refresh?incremental=true")

    complete = _frames(r.text)[-1]
    assert complete["type"] == "complete"
    assert complete["cowork_tasks_fetched"] == 1
    assert complete["cowork_tasks_failed"] == 1
    assert complete["message"] == (
        "Fetched 0 conversations and 1 Cowork task across 2 workspace(s). "
        "1 Cowork task could not be downloaded (Daily summary). "
        "The next Refresh tries it again."
    )


def test_fetch_new_does_not_report_success_when_a_task_failed(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSessionsApi(
        {ORG: _tasks()}, broken_sessions={"cse_01ExampleA_short", "cse_01ExampleB_long"}
    ).install(monkeypatch)
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index", lambda self, c, **kw: None
    )

    r = client.get("/api/fetch/start?incremental=true")

    complete = _frames(r.text)[-1]
    assert complete["cowork_tasks_fetched"] == 0
    assert complete["cowork_tasks_failed"] == 2
    assert "successfully" not in complete["message"]
    assert complete["message"].startswith("No new conversations to fetch. 2 Cowork tasks could not")
    assert "Editor configuration" in complete["message"] and "Daily summary" in complete["message"]


def test_the_failure_note_lists_at_most_three_names() -> None:
    from backend.routers.fetch import _remote_failures

    totals = {"failed": 5, "failed_titles": ["A", "B", "C", "D", "E"]}
    assert _remote_failures(totals) == (
        " 5 Cowork tasks could not be downloaded (A, B, C and 2 more). "
        "The next Refresh tries them again."
    )
    assert _remote_failures({"failed": 0, "failed_titles": []}) == ""


# PR #8 review (2026-10-21): a task with nothing new to read was counted as
# fetched on every Refresh ("and 1 Cowork task"), and its events were all
# downloaded again each time.


def test_refresh_does_not_count_a_task_whose_only_change_is_its_title(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _tasks()
    api = FakeSessionsApi({ORG: tasks}).install(monkeypatch)
    client.get("/api/fetch/refresh?incremental=true")
    tasks[1].title = "Daily summary (renamed)"
    tasks[1].updated_at = iso(300)
    api.requests.clear()

    r = client.get("/api/fetch/refresh?incremental=true")

    complete = _frames(r.text)[-1]
    assert complete["message"] == "Fetched 0 conversations across 2 workspace(s)."
    assert complete["cowork_tasks_fetched"] == 0
    store = ConversationStore(data_dir=env["out"], cowork_root=env["tmp"] / "no-local-cowork")
    names = {c.name for c in store.list_conversations(source="CLAUDE_COWORK")}
    assert "Daily summary (renamed)" in names


def test_refresh_skips_a_task_with_only_transport_events(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fetcher.tests.ccr_fake import noise_event

    quiet = FakeSession(
        "cse_01Quiet", "Waiting for input",
        [noise_event(1, "control_request"), noise_event(2, "keep_alive")],
    )
    api = FakeSessionsApi({ORG: [quiet]}).install(monkeypatch)
    first = _frames(client.get("/api/fetch/refresh?incremental=true").text)[-1]
    api.requests.clear()

    second = _frames(client.get("/api/fetch/refresh?incremental=true").text)[-1]

    assert first["cowork_tasks_fetched"] == 0
    assert second["cowork_tasks_fetched"] == 0
    assert not any("/events" in u for u in api.requests)


def test_cowork_progress_frames_say_which_phase_they_belong_to(
    client: TestClient, env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat phase counts across workspaces; the Cowork phase counts from
    1 in each workspace. Without a phase field the toast seemed to jump back
    from "Fetching 120/120" to "Fetching 1/18" (PR #8 review, 2026-10-21)."""
    FakeSessionsApi(
        {ORG: _tasks()}, list_status={PERSONAL: 500}, broken_sessions={"cse_01ExampleA_short"}
    ).install(monkeypatch)

    events = _frames(client.get("/api/fetch/refresh?incremental=true").text)

    cowork = [
        e for e in events if e["type"] == "progress" and "Cowork task" in e.get("message", "")
    ]
    # Two task frames, one failure frame, one "could not list" frame.
    assert len(cowork) == 4, [e["message"] for e in cowork]
    assert all(e.get("phase") == "cowork" for e in cowork), cowork
    assert [(e["current"], e["total"]) for e in cowork if "Fetching Cowork task" in e["message"]] == [
        (1, 2), (2, 2),
    ]
    others = [e for e in events if e not in cowork]
    assert all("phase" not in e for e in others), others
