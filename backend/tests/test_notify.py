from __future__ import annotations

import backend.notify as notify


def test_macos_uses_osascript(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify, "_run", lambda cmd: calls.append(cmd) or True)
    assert notify.notify("T", "M") is True
    assert calls[0][0] == "osascript"


def test_linux_uses_notify_send_when_present(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify, "_run", lambda cmd: calls.append(cmd) or True)
    assert notify.notify("T", "M") is True
    assert calls[0][0] == "notify-send"


def test_linux_no_notify_send_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify.notify("T", "M") is False


def test_windows_uses_powershell(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify, "_run", lambda cmd: calls.append(cmd) or True)
    assert notify.notify("T", "M") is True
    assert "powershell" in calls[0][0].lower()


def test_unknown_platform_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(notify.sys, "platform", "sunos5")
    assert notify.notify("T", "M") is False
