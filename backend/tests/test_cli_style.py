from __future__ import annotations

from backend import cli_style


ESC = "\x1b["  # ANSI escape prefix


def test_style_status_adds_ansi_when_color(monkeypatch) -> None:
    out = cli_style.style_status("[ok]", "ok", color=True)
    assert ESC in out and "[ok]" in out


def test_style_status_plain_when_no_color() -> None:
    assert cli_style.style_status("[FAIL]", "fail", color=False) == "[FAIL]"


def test_style_dim_plain_when_no_color() -> None:
    assert cli_style.style_dim("-> fix", color=False) == "-> fix"


def test_style_dim_adds_ansi_when_color() -> None:
    assert ESC in cli_style.style_dim("-> fix", color=True)


def test_should_use_color_off_when_no_color_flag(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert cli_style.should_use_color(no_color=True) is False


def test_should_use_color_off_when_NO_COLOR_env(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert cli_style.should_use_color(no_color=False) is False


def test_should_use_color_on_when_FORCE_COLOR_env(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli_style.should_use_color(no_color=False) is True


def test_no_color_flag_beats_force_color(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli_style.should_use_color(no_color=True) is False


def test_should_use_color_defers_to_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr(cli_style.sys.stdout, "isatty", lambda: True)
    assert cli_style.should_use_color(no_color=False) is True
    monkeypatch.setattr(cli_style.sys.stdout, "isatty", lambda: False)
    assert cli_style.should_use_color(no_color=False) is False
