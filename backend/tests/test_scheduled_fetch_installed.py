from __future__ import annotations

from backend.scheduled_fetch_status import is_scheduled_fetch_installed


def test_env_override_true(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_EXPLORER_SCHEDULED_FETCH_INSTALLED", "1")
    assert is_scheduled_fetch_installed() is True


def test_env_override_false(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_EXPLORER_SCHEDULED_FETCH_INSTALLED", "0")
    assert is_scheduled_fetch_installed() is False
