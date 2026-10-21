"""In-memory fake of the claude.ai Claude Code sessions API.

Models the two endpoints ``fetcher.remote_sessions`` uses, with the paging
behavior recorded from the live API on 2026-10-14:

* ``GET /v1/code/sessions?tags=cowork-remote&limit=N[&cursor=C]``:
  sessions newest-updated first; ``next_cursor`` is opaque and present
  only when more sessions follow.
* ``GET /v1/code/sessions/<id>/events?limit=N[&cursor=SEQ]``: events
  NEWEST FIRST; ``cursor=SEQ`` returns events with a lower
  ``sequence_num``; the last page has no ``next_cursor``.

Install it with ``FakeSessionsApi.install(monkeypatch)``, which replaces
``RemoteCoworkFetcher._get_json`` (the module's only network boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from fetcher import remote_sessions


class FakeHTTPError(Exception):
    """Mimics curl_cffi's HTTPError: carries ``.response.status_code``."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP Error {status}: ")
        self.response = type("Resp", (), {"status_code": status})()


BASE = datetime(2026, 10, 6, 19, 0, tzinfo=timezone.utc)


def iso(minutes: int) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def user_event(seq: int, text: str, *, source: str = "client") -> dict:
    return {
        "sequence_num": str(seq),
        "created_at": iso(seq),
        "event_type": "user",
        "source": source,
        "payload": {
            "type": "user",
            "uuid": f"u-{seq}",
            "session_id": "worker-session",
            "parent_tool_use_id": None,
            "message": {"role": "user", "content": text},
        },
    }


def assistant_event(seq: int, text: str, msg_id: str | None = None) -> dict:
    return {
        "sequence_num": str(seq),
        "created_at": iso(seq),
        "event_type": "assistant",
        "source": "worker",
        "payload": {
            "type": "assistant",
            "uuid": f"a-{seq}",
            "session_id": "worker-session",
            "parent_tool_use_id": None,
            "message": {
                "id": msg_id or f"msg_{seq}",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": text}],
            },
        },
    }


def noise_event(seq: int, kind: str) -> dict:
    return {
        "sequence_num": str(seq),
        "created_at": iso(seq),
        "event_type": kind,
        "source": "client",
        "payload": {"type": kind, "uuid": f"n-{seq}", "request": {"servers": {}}},
    }


def conversation_events(start_seq: int, turns: int) -> list[dict]:
    """``turns`` user/assistant pairs with transport noise between them."""
    events: list[dict] = []
    seq = start_seq
    for t in range(turns):
        events.append(noise_event(seq, "control_request"))
        events.append(user_event(seq + 1, f"question {t} " + "x" * 40))
        events.append(noise_event(seq + 2, "tool_progress"))
        events.append(assistant_event(seq + 3, f"answer {t}"))
        seq += 4
    return events


@dataclass
class FakeSession:
    id: str
    title: str
    events: list[dict]
    updated_at: str = field(default_factory=lambda: iso(0))
    created_at: str = field(default_factory=lambda: iso(0))
    status: str = "active"
    model: str = "claude-opus-5-5"
    folder: str | None = "/Users/someone/Source/a-project-with-a-long-name"
    #: The live API can advance ``last_event_at`` (new events) without
    #: touching ``updated_at``. ``None`` means "same as ``updated_at``".
    last_event_at: str | None = None

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_at": self.last_event_at or self.updated_at,
            "environment_kind": "anthropic_cloud",
            "tags": ["cowork-remote", "product:cowork-remote"],
            "config": {"model": self.model, "origin": "desktop_app"},
            "client_metadata": {
                "remote_cowork": {
                    "userSelectedFolders": (
                        [{"path": self.folder, "deviceName": "laptop"}] if self.folder else []
                    )
                }
            },
        }


class FakeSessionsApi:
    def __init__(
        self,
        sessions: dict[str, list[FakeSession]] | None = None,
        list_status: dict[str, int] | None = None,
        broken_sessions: set[str] | None = None,
        list_page_size: int | None = None,
    ) -> None:
        #: org uuid -> sessions (newest first, as the API returns them)
        self.sessions = sessions or {}
        #: org uuid -> HTTP status the list call fails with
        self.list_status = list_status or {}
        #: session ids whose events call fails with 500
        self.broken_sessions = broken_sessions or set()
        self.list_page_size = list_page_size
        self.requests: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeSessionsApi":
        api = self

        def _get_json(_self, url: str, org_uuid: str) -> dict:
            return api.handle(url, org_uuid)

        monkeypatch.setattr(remote_sessions.RemoteCoworkFetcher, "_get_json", _get_json)
        return self

    def event_requests(self, session_id: str) -> list[str]:
        return [u for u in self.requests if f"/sessions/{session_id}/events" in u]

    def handle(self, url: str, org_uuid: str) -> dict:
        self.requests.append(url)
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        limit = int(q.get("limit", "100"))
        path = parts.path
        if path.endswith("/v1/code/sessions"):
            if org_uuid in self.list_status:
                raise FakeHTTPError(self.list_status[org_uuid])
            assert q.get("tags") == "cowork-remote", url
            items = [s.summary() for s in self.sessions.get(org_uuid, [])]
            size = self.list_page_size or limit
            start = int(q.get("cursor", "0"))
            page = items[start : start + size]
            body: dict = {"data": page, "resume_token": "rt"}
            if start + size < len(items):
                body["next_cursor"] = str(start + size)
            return body
        if "/events" in path:
            sid = path.split("/sessions/")[1].split("/")[0]
            if sid in self.broken_sessions:
                raise FakeHTTPError(500)
            session = next(
                s for ss in self.sessions.values() for s in ss if s.id == sid
            )
            newest_first = sorted(
                session.events, key=lambda e: int(e["sequence_num"]), reverse=True
            )
            if "cursor" in q:
                newest_first = [
                    e for e in newest_first if int(e["sequence_num"]) < int(q["cursor"])
                ]
            page = newest_first[:limit]
            body = {"data": page}
            if len(newest_first) > limit:
                body["next_cursor"] = page[-1]["sequence_num"]
            return body
        raise AssertionError(f"unexpected URL {url}")
