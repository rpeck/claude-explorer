"""Every fetch path writes to the data directory that the app reads (2026-10-23).

The app reads conversations from ``Settings.data_dir``, which
``CLAUDE_EXPLORER_DATA_DIR`` or ``config.json`` can move. The fetch routes
and ``claude-explorer fetch`` wrote to the fixed
``~/.claude-explorer/conversations`` instead, so with a custom data
directory a Refresh fetched into a folder that the app never reads, and the
new conversations did not appear. Found in the PR #6 review.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

V2 = {
    "schema_version": 2,
    "session_key": "sk-test",
    "cf_bm": None,
    "cf_clearance": None,
    "captured_at": "2026-10-23T00:00:00Z",
    "orgs": [{"uuid": "org-A", "name": "A", "capabilities": ["chat"], "seen_in_response": True}],
    "primary_org_id": "org-A",
    "legacy_migration_target": None,
}


class _Constructed(Exception):
    pass


@pytest.fixture
def custom_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from backend import config

    data_dir = tmp_path / "my-archive"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    config.get_settings.cache_clear()
    yield data_dir
    config.get_settings.cache_clear()


@pytest.fixture
def constructed(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from backend.routers import fetch as fetch_router

    calls: list[dict] = []

    class _Stub:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            raise _Constructed

    monkeypatch.setattr(fetch_router, "load_credentials", lambda _p: dict(V2))
    monkeypatch.setattr(fetch_router, "ClaudeFetcher", _Stub)
    return calls


async def _drain(agen) -> None:
    try:
        async for _ in agen:
            pass
    except _Constructed:
        pass


def test_files_dir_rule() -> None:
    from backend.config import files_dir_for

    assert files_dir_for(Path("/x/.claude-explorer/conversations")) == Path("/x/.claude-explorer/files")
    assert files_dir_for(Path("/x/my-archive")) == Path("/x/my-archive/files")


def test_fetch_start_writes_to_the_settings_data_dir(custom_data_dir, constructed) -> None:
    from backend.routers import fetch as fetch_router

    asyncio.run(_drain(fetch_router.fetch_conversations_stream()))
    assert constructed[0]["output_dir"] == custom_data_dir
    assert constructed[0]["files_dir"] == custom_data_dir / "files"


def test_refresh_writes_to_the_settings_data_dir(custom_data_dir, constructed) -> None:
    from backend.routers import fetch as fetch_router

    asyncio.run(_drain(fetch_router._fetch_phase_stream(incremental=True)))
    assert constructed[0]["output_dir"] == custom_data_dir
    assert constructed[0]["files_dir"] == custom_data_dir / "files"


def test_single_conversation_refetch_writes_to_the_settings_data_dir(custom_data_dir, constructed, client) -> None:
    with pytest.raises(_Constructed):
        client.post("/api/fetch/conversation/some-uuid")
    assert constructed[0]["output_dir"] == custom_data_dir


def test_fetch_status_reports_the_settings_data_dir(custom_data_dir, client) -> None:
    assert client.get("/api/fetch/status").json()["output_dir"] == str(custom_data_dir)


def test_cli_fetch_defaults_to_the_settings_data_dir(
    custom_data_dir, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from cli.main import main

    seen: dict = {}

    class _Stub:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, **_kwargs):
            return None

    monkeypatch.setattr("fetcher.bulk_fetch.ClaudeFetcher", _Stub)
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(V2))
    result = CliRunner().invoke(main, ["fetch", "--credentials", str(creds), "--limit", "1"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert seen["output_dir"] == custom_data_dir
    assert seen["files_dir"] == custom_data_dir / "files"
