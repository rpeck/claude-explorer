from __future__ import annotations

from backend.doctor import CheckResult, Status, has_failure, run_checks


def _ok() -> CheckResult:
    return CheckResult(name="ok-check", status=Status.OK, detail="fine")


def _warn() -> CheckResult:
    return CheckResult(name="warn-check", status=Status.WARN, detail="meh", fix_command="do x")


def _boom() -> CheckResult:
    raise RuntimeError("kaboom")


def test_run_checks_collects_all_results() -> None:
    results = run_checks([("A", _ok), ("B", _warn)])
    assert [r.status for r in results] == [Status.OK, Status.WARN]


def test_exception_in_one_check_becomes_fail_and_does_not_abort_others() -> None:
    results = run_checks([("Boom", _boom), ("After", _ok)])
    assert results[0].status is Status.FAIL
    assert "kaboom" in results[0].detail
    assert results[0].name == "Boom"          # registry name used on exception
    assert results[1].status is Status.OK       # later checks still run


def test_has_failure_true_only_when_a_fail_present() -> None:
    assert has_failure([_ok(), _warn()]) is False
    assert has_failure([_ok(), CheckResult("x", Status.FAIL, "broken")]) is True
