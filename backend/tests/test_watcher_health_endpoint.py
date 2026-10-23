"""Pin /api/health/watcher contract + the shared
``log_watcher_status`` helper used by both lifespan startup and the
``claude-explorer serve`` CLI.

Banner-side contract (Phase 3 in
PLANS/2026.05.26-watcher-install-detection.md) reads:

    GET /api/health/watcher  →  {installed, platform, install_command, docs_url}

The endpoint MUST reflect a mid-session install (user runs
``claude-explorer install-watcher`` while the backend is up) on the
next call — otherwise the banner lies about an install that just
happened.
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient


def test_endpoint_returns_installed_false_with_install_command(monkeypatch):
    from backend import watcher_status
    from backend.main import app

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()
    with TestClient(app) as client:
        r = client.get("/api/health/watcher")
    assert r.status_code == 200
    body = r.json()
    assert body["installed"] is False
    assert "install-watcher" in body["install_command"]
    assert body["platform"] in {"darwin", "linux", "win32"}
    assert "docs_url" in body


def test_endpoint_returns_installed_true_when_env_says_so(monkeypatch):
    """Bidirectional: env-var True → endpoint reports True. Defeats
    the trivially-broken impl that always returns False."""
    from backend import watcher_status
    from backend.main import app

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
    watcher_status.invalidate_cache()
    with TestClient(app) as client:
        r = client.get("/api/health/watcher")
    assert r.status_code == 200
    assert r.json()["installed"] is True


def test_endpoint_reflects_mid_session_install(monkeypatch):
    """User runs ``claude-explorer install-watcher`` mid-session.
    The next /api/health/watcher call must reflect the new state — the
    endpoint MUST invalidate the module-level cache itself (the banner
    polls every 5 min; the user shouldn't have to restart the backend
    to clear the banner).
    """
    from backend import watcher_status
    from backend.main import app

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()
    with TestClient(app) as client:
        assert client.get("/api/health/watcher").json()["installed"] is False
        # Simulate the user running install-watcher between polls. We
        # do NOT manually invalidate_cache() — that's the contract being
        # pinned. The endpoint must do it itself.
        monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
        assert client.get("/api/health/watcher").json()["installed"] is True


def test_log_watcher_status_emits_warning_when_uninstalled(monkeypatch, caplog):
    """Shared helper used by lifespan startup + CLI ``serve``. Pins
    that a missing watcher fires a single WARNING-level record with the
    install command in the message body so log greps + dashboards work."""
    from backend import watcher_status
    from backend.watcher_logging import log_watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()

    with caplog.at_level(logging.INFO, logger="backend.watcher_logging"):
        log_watcher_status()

    warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and "install-watcher" in r.message
    ]
    assert len(warnings) >= 1, (
        f"expected ≥1 WARNING about install-watcher; got "
        f"{[(r.levelname, r.message) for r in caplog.records]!r}"
    )


def test_log_watcher_status_emits_info_when_installed(monkeypatch, caplog):
    """Bidirectional pair: when installed, a single INFO record fires
    confirming the watcher is up. No WARNING — preventing the
    confusing "watcher installed AND warning about it" state."""
    from backend import watcher_status
    from backend.watcher_logging import log_watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
    watcher_status.invalidate_cache()

    with caplog.at_level(logging.INFO, logger="backend.watcher_logging"):
        log_watcher_status()

    info_records = [
        r for r in caplog.records
        if r.levelname == "INFO" and "watcher" in r.message.lower()
    ]
    warning_records = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "install-watcher" in r.message
    ]
    assert len(info_records) >= 1
    assert warning_records == [], (
        f"installed → must not WARN; got {warning_records!r}"
    )


def test_log_watcher_status_idempotent_for_one_emit_per_call(monkeypatch, caplog):
    """Plan §"Design principle": user must be told at most once per
    *session* about the missing watcher. Each call to log_watcher_status
    fires exactly one record (caller dedupes by calling exactly once
    per session). The function itself doesn't add internal dedupe —
    that would prevent the CLI + lifespan from BOTH emitting in their
    respective contexts."""
    from backend import watcher_status
    from backend.watcher_logging import log_watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()

    with caplog.at_level(logging.INFO, logger="backend.watcher_logging"):
        log_watcher_status()
        log_watcher_status()
        log_watcher_status()

    warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "install-watcher" in r.message
    ]
    assert len(warnings) == 3, (
        f"each call must emit one record; got {len(warnings)}"
    )
