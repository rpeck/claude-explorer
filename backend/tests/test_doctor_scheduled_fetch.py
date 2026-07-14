from __future__ import annotations

import backend.doctor as doctor
from backend.doctor import Status
from backend.scheduled_fetch_status import FetchStatus


def test_not_installed_is_warn(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "is_scheduled_fetch_installed", lambda: False)
    r = doctor.check_scheduled_fetch()
    assert r.status is Status.WARN
    assert "install fetch" in (r.fix_command or "")


def test_auth_expired_is_warn_with_capture(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "is_scheduled_fetch_installed", lambda: True)
    monkeypatch.setattr(doctor, "read_status",
                        lambda: FetchStatus(last_result="auth_expired", auth_expired=True))
    r = doctor.check_scheduled_fetch()
    assert r.status is Status.WARN
    assert "capture" in (r.fix_command or "")


def test_fresh_success_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "is_scheduled_fetch_installed", lambda: True)
    monkeypatch.setattr(doctor, "_fetch_status_is_stale", lambda s: False)
    monkeypatch.setattr(doctor, "read_status",
                        lambda: FetchStatus(last_result="ok", last_success_at="2026-07-02T10:00:00Z"))
    assert doctor.check_scheduled_fetch().status is Status.OK
