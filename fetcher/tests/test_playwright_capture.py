"""Tests for `fetcher.playwright_capture` helpers.

Currently covers the council C3 finding: ``get_orgs``'s URL-fallback
``except Exception: pass`` swallowed parse failures silently. Even at
debug level, surfacing the failure helps operators diagnose Claude API
URL-shape changes (which would otherwise present as "capture returned
no orgs" with no breadcrumb).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.playwright_capture import get_orgs


class _BoomURL:
    """Property that raises when accessed — simulates a torn Page object."""

    def __get__(self, instance, owner):
        raise RuntimeError("simulated page.url access failure")


class _BoomPage:
    """Minimal Page-like object whose ``url`` raises and whose ``request.get``
    also raises so we exercise the URL-fallback ``except`` branch."""

    def __init__(self) -> None:
        self.request = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("api down")))

    url = _BoomURL()


@pytest.mark.asyncio
async def test_get_orgs_url_fallback_failure_logs_debug(caplog) -> None:
    """C3: when the URL-fallback parse fails, get_orgs emits a debug record
    instead of swallowing silently. Behavior (empty-list return) preserved."""
    page = _BoomPage()

    with caplog.at_level(logging.DEBUG, logger="fetcher.playwright_capture"):
        result = await get_orgs(page)  # type: ignore[arg-type]

    # Behavior preserved: empty list when no orgs resolvable.
    assert result == []

    # Diagnostic added: a debug record naming the URL-fallback failure
    # exists. We don't pin the exact wording, only the diagnostic class.
    debug_msgs = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.DEBUG
    ]
    assert any("URL" in m and "fallback" in m for m in debug_msgs), (
        f"Expected a debug record naming the URL-fallback failure; got: {debug_msgs!r}"
    )


@pytest.mark.asyncio
async def test_get_orgs_successful_path_emits_no_fallback_debug(caplog) -> None:
    """Bidirectional negative: when the API path succeeds, we MUST NOT
    fire the URL-fallback debug record (otherwise it becomes noise)."""

    async def _json():
        return [
            {
                "uuid": "11111111-1111-1111-1111-111111111111",
                "name": "OrgA",
                "capabilities": ["chat"],
            }
        ]

    response = MagicMock()
    response.ok = True
    response.json = _json
    page = MagicMock()
    page.request.get = AsyncMock(return_value=response)

    with caplog.at_level(logging.DEBUG, logger="fetcher.playwright_capture"):
        result = await get_orgs(page)

    assert len(result) == 1
    assert result[0]["uuid"] == "11111111-1111-1111-1111-111111111111"

    # No fallback noise on the happy path.
    debug_msgs = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.DEBUG and "URL" in rec.getMessage() and "fallback" in rec.getMessage()
    ]
    assert debug_msgs == [], (
        f"URL-fallback debug record fired on successful API path: {debug_msgs!r}"
    )
