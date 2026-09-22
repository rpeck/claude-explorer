"""Portable home-directory patching for tests.

Earned 2026-09-22 on the first Windows CI run. Several fixtures pointed
``Path.home()`` at a temporary directory by setting ``HOME``, and documented
that as "the contract Python's Path.home() promises". That is true on POSIX
only.

On Windows ``os.path.expanduser`` never reads ``HOME``. It reads
``USERPROFILE`` first, then ``HOMEDRIVE`` plus ``HOMEPATH``. A test that sets
only ``HOME`` therefore runs against the developer's real home directory,
which is the exact outcome those fixtures exist to prevent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def patch_home(monkeypatch: pytest.MonkeyPatch, path: Path) -> Path:
    """Point ``Path.home()`` at ``path`` on every platform.

    Returns the path, so a fixture can hand it straight back.
    """
    monkeypatch.setenv("HOME", str(path))
    if sys.platform == "win32":
        monkeypatch.setenv("USERPROFILE", str(path))
        drive, tail = os.path.splitdrive(str(path))
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", tail)
    return path
