"""Every fetch route accepts a v2 credentials file without ``org_id``.

The v2 schema keeps ``org_id`` only as a legacy mirror of
``primary_org_id``, to remove later (``fetcher/credentials.py``). The three
routes that build a ``ClaudeFetcher`` each had their own copy of the
credentials conversion, and each copy required that mirror: without it,
Refresh, Fetch New, and a single-conversation re-fetch all said "Invalid
credentials file". ``claude-explorer fetch`` accepted the same file. The
same kind of drift between copies caused issue #1. All the callers now
use ``fetcher.credentials.fetcher_credentials``.
"""

from __future__ import annotations

import asyncio

import pytest

ORGS = [
    {"uuid": "org-A", "name": "Personal", "capabilities": ["chat"], "seen_in_response": True},
    {"uuid": "org-B", "name": "Work", "capabilities": ["chat"], "seen_in_response": True},
]


def _v2_without_mirror() -> dict:
    return {
        "schema_version": 2,
        "session_key": "sk-test",
        "cf_bm": "bm",
        "cf_clearance": "clear",
        "captured_at": "2026-10-21T00:00:00Z",
        "orgs": ORGS,
        "primary_org_id": "org-B",
        "legacy_migration_target": None,
    }


class _Constructed(Exception):
    """Raised by the stub right after it records its arguments."""


@pytest.fixture
def constructed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list[dict]:
    from backend.routers import fetch as fetch_router

    calls: list[dict] = []

    class _StubFetcher:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            raise _Constructed

    monkeypatch.setattr(fetch_router, "load_credentials", lambda _path: _v2_without_mirror())
    monkeypatch.setattr(fetch_router, "ClaudeFetcher", _StubFetcher)
    monkeypatch.setattr(fetch_router, "fetch_output_dir", lambda _v=tmp_path / "conversations": _v)
    monkeypatch.setattr(fetch_router, "fetch_files_dir", lambda _v=tmp_path / "files": _v)
    return calls


def _assert_v2_arguments(calls: list[dict]) -> None:
    assert calls, "the route never built a ClaudeFetcher (it refused the credentials)"
    kwargs = calls[0]
    assert kwargs["session_key"] == "sk-test"
    assert kwargs["orgs"] == ORGS
    assert kwargs["primary_org_id"] == "org-B"
    assert kwargs["cf_bm"] == "bm"
    assert kwargs["cf_clearance"] == "clear"


async def _drain(agen) -> list:
    frames = []
    try:
        async for frame in agen:
            frames.append(frame)
    except _Constructed:
        pass
    return frames


def test_fetch_start_stream_accepts_v2_without_org_id(constructed) -> None:
    from backend.routers import fetch as fetch_router

    frames = asyncio.run(_drain(fetch_router.fetch_conversations_stream()))
    assert not any("Invalid credentials" in str(f) for f in frames), frames
    _assert_v2_arguments(constructed)


def test_refresh_fetch_phase_accepts_v2_without_org_id(constructed) -> None:
    from backend.routers import fetch as fetch_router

    frames = asyncio.run(_drain(fetch_router._fetch_phase_stream(incremental=True)))
    assert not any("Invalid credentials" in str(f) for f in frames), frames
    _assert_v2_arguments(constructed)


def test_single_conversation_refetch_accepts_v2_without_org_id(constructed, client) -> None:
    with pytest.raises(_Constructed):
        client.post("/api/fetch/conversation/some-uuid")
    _assert_v2_arguments(constructed)
