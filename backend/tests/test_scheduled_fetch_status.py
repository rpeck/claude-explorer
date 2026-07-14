from __future__ import annotations

import json
from pathlib import Path

from backend.scheduled_fetch_status import FetchStatus, read_status, write_status


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    write_status(FetchStatus(last_result="ok", auth_expired=False, fetched_count=3,
                             last_success_at="2026-07-02T00:00:00Z", interval_sec=3600), p)
    s = read_status(p)
    assert s.last_result == "ok"
    assert s.fetched_count == 3
    assert s.auth_expired is False


def test_missing_file_is_default(tmp_path: Path) -> None:
    s = read_status(tmp_path / "absent.json")
    assert s.last_result == "unknown"
    assert s.auth_expired is False


def test_corrupt_file_is_default_no_raise(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text("{ not json ")
    assert read_status(p).last_result == "unknown"


def test_write_is_0600_and_atomic(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "s.json"  # parent absent
    write_status(FetchStatus(last_result="auth_expired", auth_expired=True), p)
    assert json.loads(p.read_text())["auth_expired"] is True
    assert (p.stat().st_mode & 0o777) == 0o600
    assert list((tmp_path / "sub").glob("*.tmp")) == []


def test_read_status_uses_field_defaults_for_missing_keys(tmp_path: Path) -> None:
    """Partial JSON (e.g. hand-edited) missing a key falls back to field default."""
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"auth_expired": True}))
    s = read_status(p)
    assert s.last_result == "unknown"  # declared default, not None
    assert s.auth_expired is True
    assert s.fetched_count is None
