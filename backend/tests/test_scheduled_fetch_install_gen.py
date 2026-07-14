from __future__ import annotations

from pathlib import Path

import cli.scheduled_fetch_install as sfi


def test_launchd_uses_start_interval_not_keepalive() -> None:
    plist = sfi.build_launchd_plist("/usr/bin/python3", 3600)
    assert "StartInterval" in plist and "3600" in plist
    assert "KeepAlive" not in plist


def test_systemd_service_is_oneshot() -> None:
    body = sfi.build_systemd_service("/usr/bin/python3", Path("/h/scheduled-fetch.py"))
    assert "Type=oneshot" in body
    assert "Restart=always" not in body


def test_systemd_timer_has_interval() -> None:
    timer = sfi.build_systemd_timer(3600)
    assert "OnUnitActiveSec=3600s" in timer
    assert "[Timer]" in timer
