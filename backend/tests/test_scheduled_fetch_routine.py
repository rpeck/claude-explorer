from __future__ import annotations

from pathlib import Path

import backend.scheduled_fetch as sf
from backend.scheduled_fetch_status import FetchStatus, read_status, write_status
from fetcher.http_retry import FetchAuthError


def _setup(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path / "conversations"))
    (tmp_path / "conversations").mkdir(parents=True, exist_ok=True)
    # status + creds live under a tmp home
    monkeypatch.setattr(sf, "status_path", lambda: tmp_path / "status.json")
    monkeypatch.setattr(sf, "credentials_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(sf, "_reindex_drift", lambda: None)
    monkeypatch.setattr(sf, "_acquire_lock", lambda: object())  # always acquire
    monkeypatch.setattr(sf, "_release_lock", lambda h: None)


def test_success_writes_ok_and_clears_auth(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    (tmp_path / "credentials.json").write_text("{}")
    monkeypatch.setattr(sf, "run_incremental_fetch", lambda **k: None)
    code = sf.run_scheduled_fetch(interval_sec=3600, now="2026-07-02T10:00:00Z")
    assert code == 0
    s = read_status(tmp_path / "status.json")
    assert s.last_result == "ok" and s.auth_expired is False
    assert s.last_success_at == "2026-07-02T10:00:00Z"


def test_missing_creds_is_needs_auth(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)  # credentials.json not created
    fired = []
    monkeypatch.setattr(sf, "notify", lambda t, m: fired.append((t, m)) or True)
    code = sf.run_scheduled_fetch(interval_sec=3600, now="2026-07-02T10:00:00Z")
    assert code == 1
    assert read_status(tmp_path / "status.json").last_result == "needs_auth"
    assert len(fired) == 1  # notified exactly once


def test_auth_expired_notifies_once_on_transition(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    (tmp_path / "credentials.json").write_text("{}")
    monkeypatch.setattr(sf, "run_incremental_fetch",
                        lambda **k: (_ for _ in ()).throw(FetchAuthError("401")))
    fired = []
    monkeypatch.setattr(sf, "notify", lambda t, m: fired.append(1) or True)
    # first run: ok->expired transition -> notifies
    sf.run_scheduled_fetch(interval_sec=3600, now="2026-07-02T10:00:00Z")
    # second run: already expired -> does NOT re-notify
    sf.run_scheduled_fetch(interval_sec=3600, now="2026-07-02T11:00:00Z")
    assert read_status(tmp_path / "status.json").auth_expired is True
    assert len(fired) == 1


def test_overlap_lock_skips(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(sf, "_acquire_lock", lambda: None)  # lock held -> None
    ran = []
    monkeypatch.setattr(sf, "run_incremental_fetch", lambda **k: ran.append(1))
    code = sf.run_scheduled_fetch(interval_sec=3600, now="2026-07-02T10:00:00Z")
    assert code == 0 and ran == []  # skipped, no fetch
