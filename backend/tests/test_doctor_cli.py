from __future__ import annotations

import json

from click.testing import CliRunner

import backend.doctor as doctor
from backend.doctor import CheckResult, Status
from cli.main import main


def _patch_checks(monkeypatch, results: list[CheckResult]) -> None:
    monkeypatch.setattr(
        doctor, "ALL_CHECKS",
        [(r.name, (lambda r=r: r)) for r in results],
    )


def test_doctor_all_ok_exit_zero(monkeypatch) -> None:
    _patch_checks(monkeypatch, [CheckResult("A", Status.OK, "fine")])
    res = CliRunner().invoke(main, ["doctor"])
    assert res.exit_code == 0
    assert "All checks passed" in res.output


def test_doctor_warn_only_exit_zero(monkeypatch) -> None:
    _patch_checks(monkeypatch, [CheckResult("A", Status.WARN, "meh", fix_command="do x")])
    res = CliRunner().invoke(main, ["doctor"])
    assert res.exit_code == 0
    assert "do x" in res.output  # fix hint rendered


def test_doctor_any_fail_exit_one(monkeypatch) -> None:
    _patch_checks(monkeypatch, [
        CheckResult("A", Status.OK, "fine"),
        CheckResult("B", Status.FAIL, "broken", fix_command="fix it"),
    ])
    res = CliRunner().invoke(main, ["doctor"])
    assert res.exit_code == 1
    assert "fix it" in res.output


def test_doctor_json_output(monkeypatch) -> None:
    _patch_checks(monkeypatch, [CheckResult("A", Status.FAIL, "broken")])
    res = CliRunner().invoke(main, ["doctor", "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.output)
    assert payload["checks"][0]["status"] == "fail"
    assert payload["summary"]["failed"] == 1
