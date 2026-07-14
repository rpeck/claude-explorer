from __future__ import annotations

import backend.doctor as doctor
from backend.doctor import Status


def test_uvx_present_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/" + name)
    r = doctor.check_uvx()
    assert r.status is Status.OK
    assert "uvx" in r.detail


def test_uvx_missing_is_warn(monkeypatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    r = doctor.check_uvx()
    assert r.status is Status.WARN
    assert r.fix_command is not None


def test_pdf_libs_importable_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_weasyprint_importable", lambda: (True, ""))
    assert doctor.check_pdf_libs().status is Status.OK


def test_pdf_libs_missing_is_warn_with_os_hint(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_weasyprint_importable", lambda: (False, "OSError: no pango"))
    r = doctor.check_pdf_libs()
    assert r.status is Status.WARN
    assert r.fix_command  # OS-specific install hint present
