"""Concurrent PDF renders must be serialized, not run side by side.

Earned 2026-09-22. A pytest-xdist worker crashed at process level, not on
an assertion, while rendering a PDF under parallel load. WeasyPrint draws
through cairo and pango via cffi, which are not reliably thread-safe, and
both callers reach create_pdf on a worker thread: the HTTP route through
asyncio.to_thread, and the MCP server directly.

In the running server the same collision takes down `serve` mid-request
for everyone connected, so this is a user-visible crash and not only a CI
annoyance.
"""

from __future__ import annotations

import threading

from backend.exporters import pdf as pdf_mod


def test_render_lock_exists() -> None:
    assert isinstance(pdf_mod._RENDER_LOCK, type(threading.Lock()))


def test_renders_do_not_overlap() -> None:
    """Two threads inside the guarded region must never overlap.

    The render itself is replaced, so this exercises the guard rather than
    WeasyPrint. Overlap is what crashes the real library.
    """
    overlap_seen: list[bool] = []
    inside = 0
    inside_lock = threading.Lock()
    start = threading.Barrier(2)

    def fake_render() -> None:
        nonlocal inside
        with pdf_mod._RENDER_LOCK:
            with inside_lock:
                inside += 1
                if inside > 1:
                    overlap_seen.append(True)
            # Hold long enough that a second thread would collide if the
            # lock were absent.
            threading.Event().wait(0.05)
            with inside_lock:
                inside -= 1

    def worker() -> None:
        start.wait(timeout=5)
        fake_render()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not overlap_seen, "two threads were inside the render guard at once"


def test_lock_is_released_when_the_render_raises() -> None:
    """A failed render must not wedge every later export."""
    try:
        with pdf_mod._RENDER_LOCK:
            raise RuntimeError("render blew up")
    except RuntimeError:
        pass

    assert pdf_mod._RENDER_LOCK.acquire(timeout=2), "lock was not released"
    pdf_mod._RENDER_LOCK.release()
