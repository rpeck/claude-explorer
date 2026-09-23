"""`os.replace` needs a bounded retry on Windows.

POSIX replaces a file even while another process holds it open. Windows
does not: if antivirus, a search indexer, a backup agent, or another reader
has the destination open, `os.replace` raises PermissionError (WinError 5
or 32). Those holders let go within milliseconds, so a short retry turns a
hard failure into a pause.

Without it a user sees preferences, bookmarks, credentials, or the fetch
index silently fail to save, which reads as data loss.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fetcher.atomic import atomic_replace


def _sharing_violation() -> PermissionError:
    """Build the error Windows raises when another process holds the file.

    On POSIX a PermissionError carries no ``winerror``, and a real one there
    is a genuine permission problem rather than a transient holder, so the
    production code deliberately refuses to retry it. Tagging the exception
    reproduces the Windows shape on any platform.
    """
    exc = PermissionError(13, "in use by another process")
    exc.winerror = 32  # sharing violation
    return exc


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "new.tmp"
    dst = tmp_path / "live.json"
    src.write_text("new")
    dst.write_text("old")
    return src, dst


def test_replaces_on_the_first_try(tmp_path: Path) -> None:
    src, dst = _pair(tmp_path)
    atomic_replace(src, dst)
    assert dst.read_text() == "new"
    assert not src.exists()


def test_retries_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A holder that lets go must not become a user-visible failure."""
    src, dst = _pair(tmp_path)
    calls = {"n": 0}
    real = os.replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _sharing_violation()
        return real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    atomic_replace(src, dst, attempts=5, base_delay=0.001)

    assert calls["n"] == 3
    assert dst.read_text() == "new"


def test_gives_up_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bidirectional: a permanent holder must still surface the error."""
    src, dst = _pair(tmp_path)

    def always(a, b):
        raise _sharing_violation()

    monkeypatch.setattr(os, "replace", always)
    with pytest.raises(PermissionError):
        atomic_replace(src, dst, attempts=3, base_delay=0.001)

    # The original must survive a failed replace.
    assert dst.read_text() == "old"


def test_does_not_retry_unrelated_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the sharing-violation family is worth retrying."""
    src, dst = _pair(tmp_path)
    calls = {"n": 0}

    def wrong_kind(a, b):
        calls["n"] += 1
        raise IsADirectoryError("not a sharing violation")

    monkeypatch.setattr(os, "replace", wrong_kind)
    with pytest.raises(IsADirectoryError):
        atomic_replace(src, dst, attempts=5, base_delay=0.001)

    assert calls["n"] == 1, "a non-retryable error must fail immediately"
