from __future__ import annotations

import backend.doctor as doctor
import backend.mcp_config_install as mci
from backend.doctor import CheckResult, Status, render_text
from cli.main import _summarize_install, main
from click.testing import CliRunner


ESC = "\x1b["


# --- render_text (doctor) --------------------------------------------------

_RESULTS = [
    CheckResult("A", Status.OK, "fine"),
    CheckResult("B", Status.WARN, "meh", fix_command="do x"),
    CheckResult("C", Status.FAIL, "broken", fix_command="fix it"),
]


def test_render_text_no_color_is_plain_and_unchanged() -> None:
    out = render_text(_RESULTS)  # default color=False
    assert ESC not in out
    assert "[ok]" in out and "[warn]" in out and "[FAIL]" in out
    assert "-> do x" in out and "-> fix it" in out


def test_render_text_color_adds_ansi_but_keeps_text() -> None:
    out = render_text(_RESULTS, color=True)
    assert ESC in out
    # markers still present as text (color is additive / colorblind-safe)
    assert "[ok]" in out and "[FAIL]" in out


# --- doctor CLI ------------------------------------------------------------

def _patch_checks(monkeypatch, results):
    monkeypatch.setattr(doctor, "ALL_CHECKS", [(r.name, (lambda r=r: r)) for r in results])


def test_doctor_no_color_flag_suppresses_ansi(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")  # would enable, but --no-color wins
    _patch_checks(monkeypatch, _RESULTS)
    res = CliRunner().invoke(main, ["doctor", "--no-color"])
    assert ESC not in res.output


def test_doctor_force_color_emits_ansi(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    _patch_checks(monkeypatch, _RESULTS)
    res = CliRunner().invoke(main, ["doctor"])
    assert ESC in res.output


def test_doctor_json_is_never_colored(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    _patch_checks(monkeypatch, [CheckResult("A", Status.FAIL, "broken")])
    res = CliRunner().invoke(main, ["doctor", "--json"])
    assert ESC not in res.output  # machine-readable stays plain


def test_doctor_no_color_env_suppresses_ansi(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    _patch_checks(monkeypatch, _RESULTS)
    res = CliRunner().invoke(main, ["doctor"])
    assert ESC not in res.output


# --- install summary -------------------------------------------------------

def test_summarize_install_plain(capsys) -> None:
    results = [mci.InstallResult("code", True, True, "done"),
               mci.InstallResult("desktop", False, False, "nope")]
    code = _summarize_install(results, color=False)
    out = capsys.readouterr().out
    assert code == 1  # one failure
    assert ESC not in out
    assert "[ok]" in out and "[FAIL]" in out


def test_summarize_install_color(capsys) -> None:
    results = [mci.InstallResult("code", True, True, "done")]
    _summarize_install(results, color=True)
    out = capsys.readouterr().out
    assert ESC in out and "[ok]" in out


def test_install_mcp_no_color_flag(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(mci, "install_mcp_code",
                        lambda scope="user", **k: mci.InstallResult("code", True, True, "done"))
    monkeypatch.setattr(mci, "install_mcp_desktop",
                        lambda **k: mci.InstallResult("desktop", True, True, "done"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "all", "--no-color"])
    assert res.exit_code == 0
    assert ESC not in res.output


def test_install_mcp_force_color(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(mci, "install_mcp_code",
                        lambda scope="user", **k: mci.InstallResult("code", True, True, "done"))
    monkeypatch.setattr(mci, "install_mcp_desktop",
                        lambda **k: mci.InstallResult("desktop", False, False, "nope"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "all"])
    assert res.exit_code == 1
    assert ESC in res.output  # colored [ok]/[FAIL] markers
