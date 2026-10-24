"""Self-tests for the P0 conftest fixtures.

Each fixture introduced in ``backend/tests/conftest.py`` per the
``PLANS/2026.05.08 BACKEND TEST PLAN.md`` P0 section gets a small test
that proves the fixture does what it claims.

Bidirectional verification is light here \u2014 these are scaffolding-checks,
not contract-checks. The downstream P2 tests are where bidirectional
falsification matters.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.tests.conftest import collect_sse_data_events

# ---------------------------------------------------------------------------
# isolated_data_dir
# ---------------------------------------------------------------------------


def test_isolated_data_dir_yields_data_subdirectory(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """Fixture must yield ``<tmp_path>/data``, not ``tmp_path`` itself.

    The subdirectory layout is load-bearing: ``preferences.py:_resolve_path``
    uses ``data_dir.parent / "preferences.json"``. If the fixture yielded
    ``tmp_path``, the prefs file would resolve to ``tmp_path.parent``,
    which is the pytest tmp root \u2014 not isolated.
    """

    assert isolated_data_dir == tmp_path / "data"
    assert isolated_data_dir.is_dir()
    assert isolated_data_dir.parent == tmp_path


def test_isolated_data_dir_sets_env_var(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CLAUDE_EXPLORER_DATA_DIR`` env var must point at the data dir."""

    import os

    assert os.environ["CLAUDE_EXPLORER_DATA_DIR"] == str(isolated_data_dir)
    # CLAUDE_DIR also pinned to a tmp subdir.
    assert os.environ["CLAUDE_DIR"].startswith(str(isolated_data_dir.parent))


def test_isolated_data_dir_clears_lru_cache(isolated_data_dir: Path) -> None:
    """``get_settings()`` must reflect the fixture's env, not a stale cache."""

    from backend import config

    settings = config.get_settings()
    assert settings.data_dir == isolated_data_dir


# ---------------------------------------------------------------------------
# fastapi_app + real_async_client
# ---------------------------------------------------------------------------


async def test_real_async_client_serves_get_config(
    real_async_client: httpx.AsyncClient,
) -> None:
    """The async client must successfully serve a real route."""

    resp = await real_async_client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    # /api/config returns a dict; assert it's at least non-empty + dict-shaped.
    assert isinstance(body, dict)
    assert body  # non-empty


# ---------------------------------------------------------------------------
# collect_sse_data_events
# ---------------------------------------------------------------------------


class _FakeSSEResponse:
    """Minimal stand-in for ``httpx.Response`` exposing ``aiter_lines``.

    Lines are yielded one at a time with optional inter-line delays so we
    can simulate slow streams in the timeout test.
    """

    def __init__(self, lines: list[str], *, delay: float = 0.0) -> None:
        self._lines = list(lines)
        self._delay = delay

    async def aiter_lines(self):
        for line in self._lines:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield line


async def test_collect_sse_data_events_skips_comments_and_yields_data() -> None:
    """Verify comment-skip + data-frame parsing on a realistic mixed stream."""

    resp = _FakeSSEResponse(
        [
            ": ping",  # comment keep-alive (skipped)
            "data: " + json.dumps({"type": "start", "total": 3}),
            "",  # blank separator (skipped)
            ": another ping",
            "data: " + json.dumps({"type": "progress", "current": 1, "total": 3}),
            "data: " + json.dumps({"type": "complete", "current": 3, "total": 3}),
        ]
    )

    events: list[tuple[str, dict[str, Any]]] = []
    async for etype, payload in collect_sse_data_events(resp, timeout=2.0):
        events.append((etype, payload))

    assert [e[0] for e in events] == ["start", "progress", "complete"]
    assert events[0][1]["total"] == 3
    assert events[-1][1]["current"] == 3


async def test_collect_sse_data_events_stops_on_terminator() -> None:
    """Iteration must stop on the first ``stop_on`` type, even if more frames follow."""

    resp = _FakeSSEResponse(
        [
            "data: " + json.dumps({"type": "start"}),
            "data: " + json.dumps({"type": "error", "message": "boom"}),
            # Frames after the terminator must not be yielded:
            "data: " + json.dumps({"type": "progress", "current": 99}),
        ]
    )

    events: list[str] = []
    async for etype, _payload in collect_sse_data_events(resp, timeout=2.0):
        events.append(etype)

    assert events == ["start", "error"]


async def test_collect_sse_data_events_times_out_on_hung_stream() -> None:
    """A stream that never reaches ``stop_on`` must raise TimeoutError fast."""

    async def _hung_lines():
        # Yield no useful frames forever; sleep so we don't busy-loop.
        while True:
            await asyncio.sleep(10)
            yield ""  # never reached within the test budget

    class _HungResponse:
        def aiter_lines(self):
            return _hung_lines()

    resp = _HungResponse()
    with pytest.raises(TimeoutError):
        async for _ in collect_sse_data_events(resp, timeout=0.2):
            pass


async def test_collect_sse_data_events_propagates_json_errors() -> None:
    """Malformed ``data:`` payloads must surface the JSON error, not silently skip."""

    resp = _FakeSSEResponse(
        [
            "data: not-valid-json",
        ]
    )

    with pytest.raises(json.JSONDecodeError):
        async for _ in collect_sse_data_events(resp, timeout=2.0):
            pass


# ---------------------------------------------------------------------------
# legacy_v1_prefs
# ---------------------------------------------------------------------------


def test_legacy_v1_prefs_writes_to_expected_path(
    legacy_v1_prefs: Path, isolated_data_dir: Path
) -> None:
    """Path must be ``<isolated_data_dir>.parent / "preferences.json"``."""

    assert legacy_v1_prefs == isolated_data_dir.parent / "preferences.json"
    assert legacy_v1_prefs.is_file()


def test_legacy_v1_prefs_has_legacy_markers(legacy_v1_prefs: Path) -> None:
    """Blob must have legacy markers and lack v2 markers, per CLAUDE-TESTING \u00a75.5."""

    blob = json.loads(legacy_v1_prefs.read_text())
    data = blob["data"]

    # Legacy v1 markers PRESENT:
    assert "activeFilterIds" in data
    assert isinstance(data["savedFilters"], list)
    atom = data["savedFilters"][0]
    assert "polarity" in atom
    assert atom["polarity"] == "exclude"
    assert "pinned" in atom
    assert atom["pinned"] is True

    # v2 markers ABSENT (negative-space assertion per CLAUDE-TESTING \u00a75.4):
    assert "behavior" not in atom
    assert "_migratedV2" not in data


# ---------------------------------------------------------------------------
# _isolated_credentials_path
# ---------------------------------------------------------------------------


def test_isolated_credentials_path_patches_all_three_targets(
    _isolated_credentials_path: Path, tmp_path: Path
) -> None:
    """All THREE module-level ``DEFAULT_CREDENTIALS_PATH`` bindings must be patched."""

    expected = tmp_path / "credentials.json"
    assert _isolated_credentials_path == expected

    import backend.routers.fetch as fetch_mod
    import fetcher.bulk_fetch as bulk_fetch_mod
    import fetcher.credentials as credentials_mod

    assert fetch_mod.DEFAULT_CREDENTIALS_PATH == expected
    assert bulk_fetch_mod.DEFAULT_CREDENTIALS_PATH == expected
    assert credentials_mod.DEFAULT_CREDENTIALS_PATH == expected


# ---------------------------------------------------------------------------
# reset_refresh_flag (autouse)
#
# Test the fixture's lifecycle directly rather than relying on inter-test
# ordering (which would break under pytest-randomly or pytest-xdist).
# ---------------------------------------------------------------------------


def test_reset_refresh_flag_clears_on_setup_and_teardown() -> None:
    """Drive the fixture's body generator manually; assert both reset points.

    Tests the fixture's lifecycle directly without relying on inter-test
    ordering (which would break under ``pytest-randomly`` or
    ``pytest-xdist``). The fixture body is exposed as a plain generator
    helper (``_reset_refresh_flag_body``) so we can ``next()`` it without
    pytest's "do not call fixtures directly" guard tripping.

    The autouse fixture must:

    1. Set ``_refresh_in_progress = False`` BEFORE the test body runs
       (in case a prior test crashed and leaked ``True``).
    2. Set it back to ``False`` AFTER the test body, even if the test
       set it to ``True`` mid-run.
    """

    import backend.routers.fetch as fetch_mod
    from backend.tests.conftest import _reset_refresh_flag_body

    # Pre-poison the flag to simulate a leak from a prior test.
    fetch_mod._refresh_in_progress = True

    # Drive the generator: setup phase.
    gen = _reset_refresh_flag_body()
    next(gen)  # run the pre-yield setup
    assert fetch_mod._refresh_in_progress is False, (
        "Fixture must reset the flag BEFORE the test body runs"
    )

    # Simulate a test that crashes mid-stream and leaves the flag True.
    fetch_mod._refresh_in_progress = True

    # Drive the generator: teardown phase.
    with pytest.raises(StopIteration):
        next(gen)
    assert fetch_mod._refresh_in_progress is False, (
        "Fixture must reset the flag on teardown even if the test left it True"
    )
