"""Shared fixtures for the fetcher test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_remote_cowork_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stub the remote Cowork sessions API to "no sessions" by default.

    ``ClaudeFetcher.run`` ends by archiving remote Cowork tasks
    (``fetcher.remote_sessions``). Tests that patch only the chat methods
    would otherwise send real requests to claude.ai with fake credentials.
    Tests of the remote step patch ``RemoteCoworkFetcher._get_json`` again;
    the later patch wins.
    """
    from fetcher import remote_sessions

    monkeypatch.setattr(
        remote_sessions.RemoteCoworkFetcher,
        "_get_json",
        lambda self, url, org_uuid: {"data": []},
    )
    yield
