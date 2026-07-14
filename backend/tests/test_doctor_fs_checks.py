from __future__ import annotations

from pathlib import Path

import backend.doctor as doctor
from backend.config import get_settings
from backend.doctor import Status


def _set_data_dir(monkeypatch, tmp_path: Path) -> Path:
    conv = tmp_path / "conversations"
    conv.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(conv))
    get_settings.cache_clear()
    return conv


def test_credentials_missing_is_warn(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor, "credentials_path", lambda: tmp_path / "nope.json")
    r = doctor.check_credentials()
    assert r.status is Status.WARN
    assert "capture" in (r.fix_command or "")


def test_credentials_present_is_ok(monkeypatch, tmp_path: Path) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    monkeypatch.setattr(doctor, "credentials_path", lambda: creds)
    assert doctor.check_credentials().status is Status.OK


def test_data_dir_present_and_writable_is_ok(monkeypatch, tmp_path: Path) -> None:
    conv = _set_data_dir(monkeypatch, tmp_path)
    (conv / "a.json").write_text("{}")
    r = doctor.check_data_dir()
    assert r.status is Status.OK
    assert "1" in r.detail  # one conversation counted


def test_data_dir_missing_is_fail(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "conversations"  # not created
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(missing))
    get_settings.cache_clear()
    assert doctor.check_data_dir().status is Status.FAIL


def test_config_valid_is_ok(monkeypatch, tmp_path: Path) -> None:
    _set_data_dir(monkeypatch, tmp_path)
    assert doctor.check_config().status is Status.OK


def test_config_corrupt_is_fail(monkeypatch, tmp_path: Path) -> None:
    from backend import config as cfg
    monkeypatch.setattr(
        doctor, "get_settings",
        lambda: cfg.Settings(
            claude_dir=tmp_path, data_dir=tmp_path,
            claude_desktop_app_dir=tmp_path,  # required field on Settings
            config_corrupt_reason="x.json: JSONDecodeError: boom",
        ),
    )
    r = doctor.check_config()
    assert r.status is Status.FAIL
    assert "boom" in r.detail
