"""Remote Cowork sessions (cloud tasks): listing, archiving, incremental sync.

Regression for the 2026-10-14 work-laptop report: an Enterprise account
whose history is entirely Cowork *tasks* fetched 0 conversations, because
``chat_conversations`` never lists tasks. See ``fetcher/remote_sessions.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

from fetcher.remote_sessions import RemoteCoworkFetcher
from fetcher.tests.ccr_fake import (
    FakeSession,
    FakeSessionsApi,
    assistant_event,
    conversation_events,
    iso,
    noise_event,
    user_event,
)

ORG = "11111111-1111-4111-8111-111111111111"

#: The real network boundary, saved before the autouse conftest fixture
#: replaces it for every test.
_REAL_GET_JSON = RemoteCoworkFetcher.__dict__["_get_json"]


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


# ------------------------------------------- PR #8 review, 2026-10-21


def test_an_opaque_session_cursor_is_url_encoded(tmp_path: Path, monkeypatch) -> None:
    """A base64-style cursor carries ``+ / = &``. Sent raw, ``+`` reads as a
    space and ``&x=1`` becomes a separate query parameter, so the second
    page was never found."""
    cursor = "ab+c/d==&x=1"
    calls: list[str] = []

    def fake(self, url: str, org_uuid: str) -> dict:  # noqa: ARG001
        calls.append(url)
        if len(calls) > 5:
            raise AssertionError("pagination did not stop")
        q = parse_qs(urlsplit(url).query)
        if "cursor" not in q:
            return {"data": [{"id": "cse_01A"}], "next_cursor": cursor}
        if q["cursor"] == [cursor] and "x" not in q:
            return {"data": [{"id": "cse_01B"}]}
        return {"data": []}

    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", fake)

    sessions = _fetcher(tmp_path).list_sessions(ORG)

    assert [s["id"] for s in sessions] == ["cse_01A", "cse_01B"]
    assert parse_qs(urlsplit(calls[0]).query)["tags"] == ["cowork-remote"]


def test_an_opaque_event_cursor_and_the_session_id_are_url_encoded(
    tmp_path: Path, monkeypatch
) -> None:
    cursor = "ab+c/d==&x=1"
    calls: list[str] = []

    def fake(self, url: str, org_uuid: str) -> dict:  # noqa: ARG001
        calls.append(url)
        if len(calls) > 5:
            raise AssertionError("pagination did not stop")
        q = parse_qs(urlsplit(url).query)
        if "cursor" not in q:
            return {"data": [user_event(2, "second")], "next_cursor": cursor}
        if q["cursor"] == [cursor] and "x" not in q:
            return {"data": [user_event(1, "first")]}
        return {"data": []}

    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", fake)

    events = _fetcher(tmp_path).fetch_events_after(ORG, "cse_01A", None)
    assert [int(e["sequence_num"]) for e in events] == [1, 2]
    assert urlsplit(calls[0]).path == "/v1/code/sessions/cse_01A/events"

    # The id is quoted too, so a "/" can never add a path segment.
    calls.clear()
    _fetcher(tmp_path).fetch_events_after(ORG, "a/b", None)
    assert urlsplit(calls[0]).path == "/v1/code/sessions/a%2Fb/events"


def test_new_events_are_fetched_when_only_last_event_at_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API can add events without changing ``updated_at``. Comparing
    only ``updated_at`` then skipped the session, and the new events were
    never fetched."""
    events = conversation_events(1, 2)  # seq 1..8
    session = FakeSession("cse_01Ev", "Long task", events, updated_at=iso(10))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    assert len(_audit(tmp_path, "cse_01Ev")) == 4

    session.events = events + [user_event(9, "more"), assistant_event(10, "more done")]
    session.last_event_at = iso(500)  # updated_at stays iso(10)
    api.requests.clear()

    result = _fetcher(tmp_path).sync_org(ORG)

    assert (result.fetched, result.unchanged) == (1, 0)
    lines = _audit(tmp_path, "cse_01Ev")
    assert [line["_sequence_num"] for line in lines[-2:]] == [9, 10]
    assert len(lines) == 6
    assert _sidecar(tmp_path, "cse_01Ev")["apiLastEventAt"] == iso(500)

    # And with both timestamps unchanged, the next sync asks for nothing.
    api.requests.clear()
    assert _fetcher(tmp_path).sync_org(ORG).unchanged == 1
    assert api.event_requests("cse_01Ev") == []


def test_a_sidecar_from_an_older_version_is_upgraded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sidecars written before ``apiLastEventAt`` and ``lastSequenceNum``
    existed get one incremental check, then count as unchanged."""
    session = FakeSession("cse_01Old", "Meeting notes", conversation_events(1, 3))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    sidecar = tmp_path / "cowork-remote" / ORG / "remote_cse_01Old.json"
    legacy = json.loads(sidecar.read_text(encoding="utf-8"))
    legacy.pop("apiLastEventAt")
    legacy.pop("lastSequenceNum")
    sidecar.write_text(json.dumps(legacy), encoding="utf-8")
    before = _audit(tmp_path, "cse_01Old")
    api.requests.clear()

    first = _fetcher(tmp_path).sync_org(ORG)

    assert first.fetched == 0  # nothing new was stored
    assert len(api.event_requests("cse_01Old")) == 1  # one page, stops at the known seq
    assert _audit(tmp_path, "cse_01Old") == before
    assert _sidecar(tmp_path, "cse_01Old")["lastSequenceNum"] == 12
    api.requests.clear()

    second = _fetcher(tmp_path).sync_org(ORG)

    assert (second.fetched, second.unchanged) == (0, 1)
    assert api.event_requests("cse_01Old") == []


@pytest.mark.parametrize(
    "events",
    [
        [noise_event(1, "control_request"), noise_event(2, "keep_alive")],
        [],
    ],
    ids=["only-dropped-payload-types", "no-events-yet"],
)
def test_a_session_with_nothing_to_store_is_not_refetched_or_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[dict]
) -> None:
    """Such a session once stayed "changed" forever: every Refresh fetched
    all its events again and counted it as a fetched task."""
    session = FakeSession("cse_01Quiet", "Waiting for input", events)
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)

    first = _fetcher(tmp_path).sync_org(ORG)

    assert first.fetched == 0 and first.failed == []
    assert _sidecar(tmp_path, "cse_01Quiet")["lastSequenceNum"] == (2 if events else None)
    api.requests.clear()

    second = _fetcher(tmp_path).sync_org(ORG)

    assert (second.listed, second.fetched, second.unchanged) == (1, 0, 1)
    assert api.event_requests("cse_01Quiet") == []


def test_a_dropped_only_session_resumes_after_its_last_sequence_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lastSequenceNum`` is also the append point when the audit file has
    no events: the next fetch asks only for newer events."""
    noise = [noise_event(1, "control_request"), noise_event(2, "keep_alive")]
    session = FakeSession("cse_01Late", "Waiting for input", noise, updated_at=iso(2))
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    fetcher = _fetcher(tmp_path)
    fetcher.sync_org(ORG)
    session.events = noise + [user_event(3, "finally a question")]
    session.updated_at = iso(3)

    work, unchanged = fetcher.plan_org(ORG)

    assert unchanged == 0
    assert [(s["id"], after) for s, after in work] == [("cse_01Late", 2)]
    result = fetcher.sync_org(ORG)
    assert result.fetched == 1
    assert [line["_sequence_num"] for line in _audit(tmp_path, "cse_01Late")] == [3]


def test_a_title_only_change_saves_the_title_but_is_not_counted_as_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = FakeSession(
        "cse_01Title", "Old title", conversation_events(1, 2), updated_at=iso(10)
    )
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    session.title = "New title"
    session.updated_at = iso(20)

    result = _fetcher(tmp_path).sync_org(ORG)

    assert (result.fetched, result.unchanged, result.failed) == (0, 1, [])
    side = _sidecar(tmp_path, "cse_01Title")
    assert side["title"] == "New title"
    assert side["apiUpdatedAt"] == iso(20)
    assert len(_audit(tmp_path, "cse_01Title")) == 4


# --------------------------------------------- the real HTTP boundary
#
# Every test above replaces ``RemoteCoworkFetcher._get_json``. These run the
# real one against a fake ``http_get`` (``ClaudeFetcher._get``'s place).


class _FakeResponse:
    """The parts of a curl_cffi response that ``_get_json`` uses."""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self.reason = "Forbidden" if status == 403 else "OK"
        self._body = body or {}

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 400:
            raise CurlHTTPError(f"HTTP Error {self.status_code}: {self.reason}", 0, self)

    def json(self) -> dict:
        return self._body


def _real_get_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RemoteCoworkFetcher, "_get_json", _REAL_GET_JSON)


def test_get_json_sends_the_sessions_api_headers_and_returns_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_get_json(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def http_get(url: str, headers: dict | None = None) -> _FakeResponse:
        calls.append((url, dict(headers or {})))
        return _FakeResponse(200, {"data": [{"id": "cse_01A", "title": "Daily summary"}]})

    fetcher = RemoteCoworkFetcher(http_get, tmp_path, delay=0)

    sessions = fetcher.list_sessions(ORG)

    assert [s["id"] for s in sessions] == ["cse_01A"]
    assert len(calls) == 1
    url, headers = calls[0]
    assert url.startswith("https://claude.ai/v1/code/sessions?")
    assert headers == {
        "anthropic-beta": "ccr-byoc-2025-07-29",
        "anthropic-version": "2023-06-01",
        "x-organization-uuid": ORG,
        "content-type": "application/json",
    }


def test_get_json_raises_an_http_error_that_carries_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fetch router reads ``exc.response.status_code`` to skip an org
    without sessions-API access (401/403/404) quietly."""
    _real_get_json(monkeypatch)
    fetcher = RemoteCoworkFetcher(lambda url, headers=None: _FakeResponse(403), tmp_path, delay=0)

    with pytest.raises(CurlHTTPError) as info:
        fetcher.list_sessions(ORG)

    assert info.value.response.status_code == 403


def test_a_session_whose_events_call_fails_is_recorded_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_get_json(monkeypatch)

    def http_get(url: str, headers: dict | None = None) -> _FakeResponse:  # noqa: ARG001
        if "/events" in url:
            return _FakeResponse(500)
        return _FakeResponse(200, {"data": [{"id": "cse_01A", "title": "Broken"}]})

    result = RemoteCoworkFetcher(http_get, tmp_path, delay=0).sync_org(ORG)

    assert (result.listed, result.fetched, result.failed) == (1, 0, ["cse_01A"])


def test_get_json_without_an_http_client_refuses(tmp_path: Path, monkeypatch) -> None:
    _real_get_json(monkeypatch)
    with pytest.raises(RuntimeError, match="no HTTP client"):
        RemoteCoworkFetcher(None, tmp_path).list_sessions(ORG)


# ----- Workspace names (2026-10-23) ----------------------------------------
# The sidecar recorded only organizationId, so grouping the list by
# workspace showed "Workspace (11111111)" for every remote task.


def test_the_sidecar_records_the_workspace_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSessionsApi({ORG: [FakeSession("cse_01Named", "Notes", conversation_events(1, 2))]}).install(monkeypatch)
    fetcher = RemoteCoworkFetcher(
        lambda *a, **k: None, tmp_path, incremental=True, delay=0, org_names={ORG: "Acme Research"}
    )
    fetcher.sync_org(ORG)
    assert _sidecar(tmp_path, "cse_01Named")["organizationName"] == "Acme Research"


def test_an_unknown_workspace_name_is_recorded_as_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSessionsApi({ORG: [FakeSession("cse_01NoName", "Notes", conversation_events(1, 2))]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    sidecar = _sidecar(tmp_path, "cse_01NoName")
    assert "organizationName" in sidecar and sidecar["organizationName"] is None


def test_a_sidecar_without_the_workspace_name_is_checked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tasks fetched before 2026-10-23 get the name on the next fetch,
    with one cheap incremental check, then count as unchanged."""
    session = FakeSession("cse_01Older", "Notes", conversation_events(1, 3))
    api = FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _fetcher(tmp_path).sync_org(ORG)
    path = tmp_path / "cowork-remote" / ORG / "remote_cse_01Older.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy.pop("organizationName")
    path.write_text(json.dumps(legacy), encoding="utf-8")
    api.requests.clear()

    named = RemoteCoworkFetcher(
        lambda *a, **k: None, tmp_path, incremental=True, delay=0, org_names={ORG: "Acme Research"}
    )
    first = named.sync_org(ORG)
    assert first.fetched == 0
    assert len(api.event_requests("cse_01Older")) == 1
    assert _sidecar(tmp_path, "cse_01Older")["organizationName"] == "Acme Research"
    api.requests.clear()

    second = named.sync_org(ORG)
    assert (second.fetched, second.unchanged) == (0, 1)
    assert api.event_requests("cse_01Older") == []
