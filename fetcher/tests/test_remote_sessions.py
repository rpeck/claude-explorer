"""Remote Cowork sessions (cloud tasks): listing, archiving, incremental sync.

Regression for the 2026-10-14 work-laptop report: an Enterprise account
whose history is entirely Cowork *tasks* fetched 0 conversations, because
``chat_conversations`` never lists tasks. See ``fetcher/remote_sessions.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fetcher.remote_sessions import RemoteCoworkFetcher
from fetcher.tests.ccr_fake import (
    FakeSession,
    FakeSessionsApi,
    assistant_event,
    conversation_events,
    iso,
    user_event,
)

ORG = "11111111-1111-4111-8111-111111111111"


# The fetcher writes UTF-8. Read it as UTF-8 too: without an encoding,
# Windows reads with cp1252 and garbles any non-ASCII title.
def _audit(out: Path, sid: str) -> list[dict]:
    path = out / "cowork-remote" / ORG / f"remote_{sid}" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sidecar(out: Path, sid: str) -> dict:
    path = out / "cowork-remote" / ORG / f"remote_{sid}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fetcher(out: Path, incremental: bool = True) -> RemoteCoworkFetcher:
    return RemoteCoworkFetcher(lambda *a, **k: None, out, incremental=incremental, delay=0)


def test_full_sync_writes_every_page_chronologically_without_transport_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 80 turns x 4 events = 320 events: four pages of 100.
    session = FakeSession("cse_01Long", "Editor configuration", conversation_events(1, 80))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)

    result = _fetcher(tmp_path).sync_org(ORG)

    assert (result.listed, result.fetched, result.unchanged, result.failed) == (1, 1, 0, [])
    assert len(api.event_requests("cse_01Long")) == 4
    lines = _audit(tmp_path, "cse_01Long")
    # control_request and tool_progress carry no conversation content.
    assert {line["type"] for line in lines} == {"user", "assistant"}
    assert len(lines) == 160
    seqs = [line["_sequence_num"] for line in lines]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert lines[0]["message"]["content"].startswith("question 0")
    assert lines[-1]["message"]["content"][0]["text"] == "answer 79"
    # The reader maps _audit_timestamp to the message timestamp.
    assert lines[0]["_audit_timestamp"] == iso(2)


def test_sidecar_carries_what_the_reader_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = FakeSession(
        "cse_01Side", "⚡ Weekly report draft", conversation_events(1, 2),
        created_at=iso(1), updated_at=iso(90), model="claude-fable-5-1",
    )
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)

    _fetcher(tmp_path).sync_org(ORG)

    side = _sidecar(tmp_path, "cse_01Side")
    assert side["sessionId"] == "cse_01Side"
    assert side["title"] == "⚡ Weekly report draft"
    assert side["model"] == "claude-fable-5-1"
    assert side["cwd"] == "/Users/someone/Source/a-project-with-a-long-name"
    assert side["isArchived"] is False
    assert side["remote"] is True
    assert side["apiUpdatedAt"] == iso(90)
    # Epoch milliseconds, like local Cowork sidecars.
    assert side["lastActivityAt"] - side["createdAt"] == 89 * 60 * 1000


def test_session_list_pagination_is_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = [
        FakeSession(f"cse_{i:02d}", f"Daily summary {i}", conversation_events(1, 1))
        for i in range(7)
    ]
    FakeSessionsApi({ORG: sessions}, list_page_size=3).install(monkeypatch)

    result = _fetcher(tmp_path).sync_org(ORG)

    assert result.listed == 7 and result.fetched == 7
    assert len(list((tmp_path / "cowork-remote" / ORG).glob("remote_*/audit.jsonl"))) == 7


def test_unchanged_session_is_not_requested_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = FakeSession("cse_01Same", "Meeting notes", conversation_events(1, 3))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    before = _audit(tmp_path, "cse_01Same")
    api.requests.clear()

    result = _fetcher(tmp_path).sync_org(ORG)

    assert (result.fetched, result.unchanged) == (0, 1)
    assert api.event_requests("cse_01Same") == []
    assert _audit(tmp_path, "cse_01Same") == before


def test_updated_session_appends_only_new_events_and_stops_paging_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = conversation_events(1, 75)  # seq 1..300
    session = FakeSession("cse_01Grow", "Codebase review", events, updated_at=iso(300))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    assert len(_audit(tmp_path, "cse_01Grow")) == 150

    session.events = events + [user_event(301, "one more thing"), assistant_event(302, "done")]
    session.updated_at = iso(302)
    api.requests.clear()

    result = _fetcher(tmp_path).sync_org(ORG)

    assert result.fetched == 1
    # Newest-first paging reaches the stored seq on the first page.
    assert len(api.event_requests("cse_01Grow")) == 1
    lines = _audit(tmp_path, "cse_01Grow")
    assert len(lines) == 152
    assert [line["_sequence_num"] for line in lines[-2:]] == [301, 302]
    assert _sidecar(tmp_path, "cse_01Grow")["apiUpdatedAt"] == iso(302)


def test_stale_sidecar_after_a_crash_does_not_duplicate_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The append point comes from audit.jsonl, not from the sidecar."""
    session = FakeSession("cse_01Crash", "build.sh linker error", conversation_events(1, 5))
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    sidecar = tmp_path / "cowork-remote" / ORG / "remote_cse_01Crash.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["apiUpdatedAt"] = "2000-01-01T00:00:00Z"  # as if the sidecar write was lost
    sidecar.write_text(json.dumps(data), encoding="utf-8")

    _fetcher(tmp_path).sync_org(ORG)

    lines = _audit(tmp_path, "cse_01Crash")
    seqs = [line["_sequence_num"] for line in lines]
    assert len(seqs) == len(set(seqs)) == 10


def test_full_refresh_rewrites_instead_of_appending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = FakeSession("cse_01Full", "API client and plugin catalog", conversation_events(1, 4))
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)

    result = _fetcher(tmp_path, incremental=False).sync_org(ORG)

    assert result.fetched == 1
    assert len(_audit(tmp_path, "cse_01Full")) == 8


def test_one_failing_session_does_not_stop_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = FakeSession("cse_01Good", "Daily summary", conversation_events(1, 2))
    bad = FakeSession("cse_01Bad", "Broken", conversation_events(1, 2))
    FakeSessionsApi({ORG: [bad, good]}, broken_sessions={"cse_01Bad"}).install(monkeypatch)

    result = _fetcher(tmp_path).sync_org(ORG)

    assert result.fetched == 1 and result.failed == ["cse_01Bad"]
    assert len(_audit(tmp_path, "cse_01Good")) == 4
    assert not (tmp_path / "cowork-remote" / ORG / "remote_cse_01Bad").exists()


def test_cli_fetch_run_archives_tasks_after_chats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``claude-explorer fetch`` (``ClaudeFetcher.run``) includes the tasks."""
    from fetcher.bulk_fetch import ClaudeFetcher

    session = FakeSession("cse_01Cli", "Daily summary", conversation_events(1, 2))
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    monkeypatch.setattr(ClaudeFetcher, "fetch_conversation_list", lambda self: [])
    fetcher = ClaudeFetcher(
        "sk-test-fake", [{"uuid": ORG, "name": "Analytical Engines"}], ORG, tmp_path, delay=0
    )

    fetcher.run()

    assert len(_audit(tmp_path, "cse_01Cli")) == 4


def test_cli_fetch_run_skips_an_org_without_sessions_api_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fetcher.bulk_fetch import ClaudeFetcher

    FakeSessionsApi(list_status={ORG: 403}).install(monkeypatch)
    monkeypatch.setattr(ClaudeFetcher, "fetch_conversation_list", lambda self: [])
    fetcher = ClaudeFetcher("sk-test-fake", [{"uuid": ORG, "name": "Personal"}], ORG, tmp_path, delay=0)

    fetcher.run()  # must not raise

    assert not (tmp_path / "cowork-remote").exists()


# ------------------------------------------------- PR #6 review, 2026-10-19


def _scripted(monkeypatch: pytest.MonkeyPatch, pages: dict) -> list[str]:
    """Answer each request from ``pages`` (keyed by URL substring); record URLs."""
    calls: list[str] = []

    def fake(self, url: str, org_uuid: str) -> dict:  # noqa: ARG001
        calls.append(url)
        if len(calls) > 50:
            raise AssertionError("pagination did not stop")
        for key, body in pages.items():
            if key in url:
                return body
        return {"data": []}

    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", fake)
    return calls


def test_a_repeating_session_cursor_ends_the_listing(tmp_path: Path, monkeypatch) -> None:
    """A cursor that never advances once kept Refresh busy forever."""
    page = {"data": [{"id": "cse_01A", "updated_at": iso(1)}], "next_cursor": "same"}
    calls = _scripted(monkeypatch, {"/v1/code/sessions?": page})

    sessions = _fetcher(tmp_path).list_sessions(ORG)

    assert [s["id"] for s in sessions] == ["cse_01A"]
    assert len(calls) <= 2


def test_a_page_with_nothing_new_ends_the_listing(tmp_path: Path, monkeypatch) -> None:
    """A cursor that changes but returns the same page must stop too."""
    counter = {"n": 0}

    def fake(self, url: str, org_uuid: str) -> dict:  # noqa: ARG001
        counter["n"] += 1
        if counter["n"] > 50:
            raise AssertionError("pagination did not stop")
        return {"data": [{"id": "cse_01A"}], "next_cursor": f"c{counter['n']}"}

    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", fake)

    assert [s["id"] for s in _fetcher(tmp_path).list_sessions(ORG)] == ["cse_01A"]
    assert counter["n"] <= 2


def test_a_repeating_event_cursor_ends_the_event_fetch(tmp_path: Path, monkeypatch) -> None:
    page = {"data": [user_event(2, "hello")], "next_cursor": "2"}
    calls = _scripted(monkeypatch, {"/events?": page})

    events = _fetcher(tmp_path).fetch_events_after(ORG, "cse_01A", None)

    assert [int(e["sequence_num"]) for e in events] == [2]  # the API sends strings
    assert len(calls) <= 2


def test_pagination_has_a_hard_page_cap(tmp_path: Path, monkeypatch) -> None:
    """The last guard: even always-new pages stop at MAX_PAGES."""
    from fetcher import remote_sessions

    monkeypatch.setattr(remote_sessions, "MAX_PAGES", 3)
    counter = {"n": 0}

    def fake(self, url: str, org_uuid: str) -> dict:  # noqa: ARG001
        counter["n"] += 1
        return {"data": [{"id": f"cse_{counter['n']:03d}"}], "next_cursor": f"c{counter['n']}"}

    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", fake)

    assert len(_fetcher(tmp_path).list_sessions(ORG)) == 3
    assert counter["n"] == 3


@pytest.mark.parametrize(
    "bad_id", ["x/../../../../escaped", "..", "a\\..\\b", "cse_a.json", "", "a" * 129, "cse 01"]
)
def test_an_unsafe_session_id_writes_nothing_outside_the_archive(
    tmp_path: Path, monkeypatch, bad_id: str
) -> None:
    """Session ids come from the API and become folder names. A hostile id
    such as ``x/../../escaped`` once wrote outside the archive."""
    page = {"data": [{"id": bad_id, "title": "bad"}, {"id": "cse_01Good", "title": "good"}]}
    good_events = {"data": [user_event(1, "hi")]}
    _scripted(monkeypatch, {"/v1/code/sessions?": page, "cse_01Good/events": good_events})
    out = tmp_path / "out"
    out.mkdir()

    result = _fetcher(out).sync_org(ORG)

    assert result.fetched == 1  # the good session still archives
    written = {p.relative_to(out).parts[0] for p in out.rglob("*")}
    assert written == {"cowork-remote"}, sorted(written)
    assert not list(tmp_path.glob("escaped*"))


def test_session_paths_refuse_unsafe_ids(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    with pytest.raises(ValueError):
        fetcher._session_paths(ORG, "../x")
    with pytest.raises(ValueError):
        fetcher._session_paths("../org", "cse_01A")
    session_dir, sidecar = fetcher._session_paths(ORG, "cse_01A")
    assert sidecar.name == "remote_cse_01A.json"
    assert session_dir.name == "remote_cse_01A"
