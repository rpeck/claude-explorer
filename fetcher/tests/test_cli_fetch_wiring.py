"""Regression tests for ``claude-explorer fetch`` CLI wiring (Council A-BUG-1).

Pre-fix shipping bug:
    ``cli/main.py:fetch`` (formerly ``fetcher/cli.py:fetch``) constructed
    ``ClaudeFetcher(session_key=..., org_id=..., ...)`` — but
    ``ClaudeFetcher.__init__`` accepts ``orgs: list[dict]`` and
    ``primary_org_id: str`` (post-multi-org migration), with NO ``org_id``
    keyword argument. Every ``claude-explorer fetch`` invocation crashed
    with ``TypeError: ClaudeFetcher.__init__() got an unexpected keyword
    argument 'org_id'``. No test covered this entry point — the equivalent
    correctly-updated logic lived in the duplicate
    ``fetcher.bulk_fetch.main()`` (deleted by Council A-BUG-2) but
    ``cli.py`` was never resynced.

This module pins the wiring with three orthogonal tests so a future drift
between cli.py's fetch command and ClaudeFetcher's constructor surfaces
immediately, BEFORE a user hits it.

Bidirectional discipline:

  * RED ``test_cli_fetch_does_not_pass_org_id_kwarg``: pre-fix, this raises
    ``TypeError`` (the actual shipping bug). Post-fix, the CLI completes
    without that TypeError and ClaudeFetcher is constructed with ``orgs``
    + ``primary_org_id``.
  * GREEN pair ``test_cli_fetch_v2_credentials_passes_orgs_and_primary``:
    a v2 credentials file (with ``orgs`` + ``primary_org_id``) flows the
    multi-org list straight through to the constructor.
  * GREEN pair
    ``test_cli_fetch_session_key_org_id_override_synthesizes_orgs_list``:
    the ``--session-key`` + ``--org-id`` override path builds a synthetic
    single-element ``orgs`` list and uses that org as primary.

  * Boundary ``test_cli_fetch_v1_credentials_upgrades_to_single_org``: a
    legacy v1 file (no ``orgs`` array; flat ``org_id`` scalar) is upgraded
    in-memory to a single-element ``orgs`` list with the v1 org as primary.

All four tests STUB ``ClaudeFetcher`` with a class that captures the
kwargs it receives, so we exercise the cli.py wiring layer without
touching the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from cli.main import main


class _StubFetcher:
    """Captures the ClaudeFetcher constructor call for inspection."""

    last_kwargs: dict[str, Any] = {}
    last_run_kwargs: dict[str, Any] = {}
    last_run_all_orgs_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _StubFetcher.last_kwargs = dict(kwargs)
        # Match the real fetcher's public attribute surface so any
        # downstream `.run()`-side code (e.g. retry_events drain) doesn't
        # crash the test.
        self.retry_events: list[dict] = []

    def run(self, **kwargs: Any) -> None:
        _StubFetcher.last_run_kwargs = dict(kwargs)

    def run_all_orgs(self, **kwargs: Any) -> dict:
        _StubFetcher.last_run_all_orgs_kwargs = dict(kwargs)
        return {"orgs": [], "primary_demoted_from": None, "status": "ok"}


@pytest.fixture(autouse=True)
def _reset_stub_state() -> None:
    _StubFetcher.last_kwargs = {}
    _StubFetcher.last_run_kwargs = {}
    _StubFetcher.last_run_all_orgs_kwargs = {}


@pytest.fixture
def _patch_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real ClaudeFetcher with the capture stub.

    Patched on both ``fetcher.bulk_fetch`` (the source module) and
    ``fetcher.run_fetch`` (the shared helper that does a top-level
    ``from fetcher.bulk_fetch import ClaudeFetcher``). The source-module
    patch covers the lazy-import path in cli.py before Task 3 refactor;
    the run_fetch patch is required after Task 3 moved the block into
    ``run_incremental_fetch`` which may already have the name bound.
    """
    monkeypatch.setattr("fetcher.bulk_fetch.ClaudeFetcher", _StubFetcher)
    import fetcher.run_fetch as _rf  # ensure module is loaded before patching
    monkeypatch.setattr(_rf, "ClaudeFetcher", _StubFetcher)


def _write_v2_creds(path: Path, *, session_key: str, orgs: list[dict], primary: str) -> None:
    payload = {
        "schema_version": 2,
        "session_key": session_key,
        "cf_bm": "cf_bm_value",
        "cf_clearance": "cf_clearance_value",
        "captured_at": "2026-05-21T00:00:00Z",
        "orgs": orgs,
        "primary_org_id": primary,
        "legacy_migration_target": primary,
        "org_id": primary,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _write_v1_creds(path: Path, *, session_key: str, org_id: str) -> None:
    """Legacy v1 shape — no schema_version, flat org_id."""
    payload = {
        "session_key": session_key,
        "org_id": org_id,
        "cf_bm": "cf_bm_value",
        "cf_clearance": "cf_clearance_value",
        "captured_at": "2026-05-21T00:00:00Z",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# RED — the actual shipping bug.
# ---------------------------------------------------------------------------


def test_cli_fetch_does_not_pass_org_id_kwarg(
    tmp_path: Path,
    _patch_fetcher: None,
) -> None:
    """Pre-fix this raises ``TypeError: unexpected keyword argument 'org_id'``.

    Post-fix the CLI completes without crashing and the captured kwargs
    contain ``orgs`` + ``primary_org_id`` (the v2 signature), and do NOT
    contain ``org_id`` (the dead v1 kwarg).
    """
    creds = tmp_path / "credentials.json"
    _write_v2_creds(
        creds,
        session_key="sk-test",
        orgs=[{"uuid": "org-uuid-1", "name": "Primary", "capabilities": ["chat"], "seen_in_response": True}],
        primary="org-uuid-1",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "--credentials", str(creds),
            "--output-dir", str(tmp_path / "out"),
            "--files-dir", str(tmp_path / "files"),
            "--no-download-files",
            "--limit", "1",
        ],
    )

    # Pre-fix this would be exit_code != 0 with TypeError in result.exception.
    # The exact assertion we care about: no TypeError mentioning org_id.
    if result.exception is not None:
        msg = repr(result.exception)
        assert "org_id" not in msg or "unexpected keyword" not in msg, (
            f"ClaudeFetcher constructor still crashing with org_id kwarg: {msg}"
        )
        # Surface unexpected failures for diagnostics.
        raise AssertionError(
            f"CLI fetch crashed unexpectedly: exit_code={result.exit_code}, "
            f"exception={msg}, output={result.output!r}"
        )

    assert result.exit_code == 0, f"CLI fetch failed: {result.output!r}"

    # Verify the constructor saw the v2 signature.
    assert "orgs" in _StubFetcher.last_kwargs, (
        "ClaudeFetcher should be constructed with orgs= kwarg (v2 multi-org signature)"
    )
    assert "primary_org_id" in _StubFetcher.last_kwargs, (
        "ClaudeFetcher should be constructed with primary_org_id= kwarg"
    )
    assert "org_id" not in _StubFetcher.last_kwargs, (
        "ClaudeFetcher should NOT be constructed with the dead org_id= kwarg"
    )


# ---------------------------------------------------------------------------
# GREEN pair — v2 credentials file flows orgs straight through.
# ---------------------------------------------------------------------------


def test_cli_fetch_v2_credentials_passes_orgs_and_primary(
    tmp_path: Path,
    _patch_fetcher: None,
) -> None:
    """A v2 creds file with multiple orgs forwards the full list."""
    creds = tmp_path / "credentials.json"
    orgs = [
        {"uuid": "org-A", "name": "Personal", "capabilities": ["chat"], "seen_in_response": True},
        {"uuid": "org-B", "name": "Work", "capabilities": ["chat"], "seen_in_response": True},
    ]
    _write_v2_creds(creds, session_key="sk-test", orgs=orgs, primary="org-B")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "--credentials", str(creds),
            "--output-dir", str(tmp_path / "out"),
            "--no-download-files",
            "--limit", "1",
        ],
    )

    assert result.exit_code == 0, f"CLI fetch failed: {result.output!r}, exc={result.exception!r}"
    assert _StubFetcher.last_kwargs.get("orgs") == orgs, (
        f"orgs kwarg should equal the v2 file's orgs list, got {_StubFetcher.last_kwargs.get('orgs')!r}"
    )
    assert _StubFetcher.last_kwargs.get("primary_org_id") == "org-B", (
        f"primary_org_id should equal the v2 file's primary, got "
        f"{_StubFetcher.last_kwargs.get('primary_org_id')!r}"
    )
    assert _StubFetcher.last_kwargs.get("session_key") == "sk-test"
    assert _StubFetcher.last_kwargs.get("cf_bm") == "cf_bm_value"
    assert _StubFetcher.last_kwargs.get("cf_clearance") == "cf_clearance_value"


# ---------------------------------------------------------------------------
# GREEN pair — --session-key + --org-id override path synthesizes single-org.
# ---------------------------------------------------------------------------


def test_cli_fetch_session_key_org_id_override_synthesizes_orgs_list(
    tmp_path: Path,
    _patch_fetcher: None,
) -> None:
    """``--session-key sk --org-id uuid`` builds a synthetic 1-element orgs list.

    No credentials file required; primary == the override org id.
    """
    # Use a non-existent credentials path to confirm the override skips file I/O.
    nonexistent = tmp_path / "does-not-exist.json"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "--credentials", str(nonexistent),
            "--session-key", "sk-override",
            "--org-id", "override-org-uuid",
            "--output-dir", str(tmp_path / "out"),
            "--no-download-files",
            "--limit", "1",
        ],
    )

    assert result.exit_code == 0, f"CLI fetch failed: {result.output!r}, exc={result.exception!r}"
    orgs = _StubFetcher.last_kwargs.get("orgs")
    assert isinstance(orgs, list) and len(orgs) == 1, (
        f"Override path should synthesize a single-element orgs list, got {orgs!r}"
    )
    assert orgs[0]["uuid"] == "override-org-uuid"
    assert _StubFetcher.last_kwargs.get("primary_org_id") == "override-org-uuid"
    assert _StubFetcher.last_kwargs.get("session_key") == "sk-override"


# ---------------------------------------------------------------------------
# Boundary — v1 credentials file (legacy flat shape) upgrades in-memory.
# ---------------------------------------------------------------------------


def test_cli_fetch_v1_credentials_upgrades_to_single_org(
    tmp_path: Path,
    _patch_fetcher: None,
) -> None:
    """A v1 file (no schema_version, flat org_id) becomes a 1-element orgs list."""
    creds = tmp_path / "credentials.json"
    _write_v1_creds(creds, session_key="sk-v1", org_id="v1-org-uuid")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "--credentials", str(creds),
            "--output-dir", str(tmp_path / "out"),
            "--no-download-files",
            "--limit", "1",
        ],
    )

    assert result.exit_code == 0, f"CLI fetch failed: {result.output!r}, exc={result.exception!r}"
    orgs = _StubFetcher.last_kwargs.get("orgs")
    assert isinstance(orgs, list) and len(orgs) == 1, (
        f"v1 upgrade should produce a single-element orgs list, got {orgs!r}"
    )
    assert orgs[0]["uuid"] == "v1-org-uuid"
    assert _StubFetcher.last_kwargs.get("primary_org_id") == "v1-org-uuid"
    assert _StubFetcher.last_kwargs.get("session_key") == "sk-v1"
