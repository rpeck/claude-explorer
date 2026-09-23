"""Atomic file replacement that also works on Windows.

``os.replace`` is atomic on every platform this project supports, but the
platforms disagree about what happens when somebody else holds the
destination open.

POSIX replaces the file regardless. The old inode stays alive for whoever
still has it open, and the rename succeeds.

Windows refuses. If antivirus, the search indexer, a backup agent, a
sync client, or another copy of this app has the destination open without
delete sharing, ``os.replace`` raises ``PermissionError`` (WinError 5,
access denied, or WinError 32, sharing violation).

Those holders almost always let go within milliseconds, so a short bounded
retry turns a hard failure into a brief pause. Without it the user sees
preferences, bookmarks, credentials, or the fetch index fail to save, which
looks exactly like data loss.

The retry is deliberately narrow: only the sharing-violation family is
retried, and only a few times. A genuine error still surfaces promptly.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

# WinError 5 = access denied, WinError 32 = sharing violation. Both show up
# as PermissionError with these values in ``winerror``.
_RETRYABLE_WINERRORS = frozenset({5, 32})

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 0.05


def _is_retryable(exc: BaseException) -> bool:
    """True for the transient Windows sharing violations, nothing else."""
    if not isinstance(exc, PermissionError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is None:
        # On POSIX a PermissionError is a real permission problem, not a
        # transient holder, so do not spin on it.
        return os.name == "nt"
    return winerror in _RETRYABLE_WINERRORS


def atomic_replace(
    src: str | Path,
    dst: str | Path,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> None:
    """Rename ``src`` over ``dst``, retrying transient Windows holders.

    Args:
        src: The finished temporary file.
        dst: The live path to install it at.
        attempts: Total tries, including the first.
        base_delay: Seconds before the second try. The wait doubles each
            time, so the default gives roughly 50, 100, 200 and 400 ms.

    Raises:
        OSError: The original error, if every attempt fails or the error is
            not a transient sharing violation.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            if attempt:
                log.info("replaced %s after %d retries", dst, attempt)
            return
        except OSError as exc:
            if not _is_retryable(exc) or attempt == attempts - 1:
                raise
            last = exc
            delay = base_delay * (2**attempt)
            log.warning(
                "replace of %s blocked (%s); retrying in %.0f ms",
                dst,
                exc,
                delay * 1000,
            )
            time.sleep(delay)

    # Unreachable: the loop either returns or raises.
    raise AssertionError(f"atomic_replace fell through for {dst}") from last
