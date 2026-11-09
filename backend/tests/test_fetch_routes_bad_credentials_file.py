"""Every fetch route refuses a credentials file that is not usable, cleanly.

The three routes that build a ``ClaudeFetcher`` (Refresh, Fetch New, and a
single-conversation re-fetch) read the file with a plain ``json.load`` copy
in ``fetcher.bulk_fetch``. That copy returned any JSON value, so a file
whose root was a list or a string reached ``fetcher_credentials`` and
raised ``AttributeError``:

- Fetch New ended with "Fetch failed: 'list' object has no attribute
  'get'", which does not tell the user to run capture.
- The fetch phase of Refresh raised the ``AttributeError`` out of the
  generator.
- The single-conversation re-fetch returned 500.

The UTF-16 cases (Windows PowerShell 5.1 ``Out-File``) passed before the
fix, because each route catches any exception from the loader. They stay
as guards: the strict loader must not let a decode error through.

The routes now use ``fetcher.credentials.load_credentials``, the one
loader (issue #1, "related drift").
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

BAD_FILES = [
    pytest.param(b"[1, 2, 3]", id="list-root"),
    pytest.param(b'"a-pasted-session-key"', id="string-root"),
    pytest.param(json.dumps({"schema_version": 2}).encode("utf-16"), id="utf-16"),
]


@pytest.fixture
def bad_creds(
    request: pytest.FixtureRequest,
    _isolated_credentials_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    from backend.routers import fetch as fetch_router

    def _no_fetcher(**_kwargs):
        raise AssertionError("no fetch may start from a bad credentials file")

    monkeypatch.setattr(fetch_router, "ClaudeFetcher", _no_fetcher)
    _isolated_credentials_path.write_bytes(request.param)
    return _isolated_credentials_path


async def _drain(agen) -> list:
    return [frame async for frame in agen]


@pytest.mark.parametrize("bad_creds", BAD_FILES, indirect=True)
def test_fetch_start_stream_reports_a_bad_file_as_an_error_event(bad_creds: Path) -> None:
    from backend.routers import fetch as fetch_router

    frames = asyncio.run(_drain(fetch_router.fetch_conversations_stream()))

    events = [json.loads(f.removeprefix("data: ")) for f in frames]
    assert events, "the stream ended without an event"
    assert events[-1]["type"] == "error", events
    assert "capture" in events[-1]["message"], events


@pytest.mark.parametrize("bad_creds", BAD_FILES, indirect=True)
def test_refresh_fetch_phase_reports_a_bad_file_as_fatal(bad_creds: Path) -> None:
    from backend.routers import fetch as fetch_router

    results = asyncio.run(_drain(fetch_router._fetch_phase_stream(incremental=True)))

    assert results, "the fetch phase ended without a result"
    kind, message = results[-1]
    assert kind == "fatal", results
    assert "credentials" in message.lower(), results


@pytest.mark.parametrize("bad_creds", BAD_FILES, indirect=True)
def test_single_conversation_refetch_reports_a_bad_file_as_400(bad_creds: Path, client) -> None:
    r = client.post("/api/fetch/conversation/some-uuid")

    assert r.status_code == 400, r.text
    assert "capture" in r.json()["detail"], r.text
