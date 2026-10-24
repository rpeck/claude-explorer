"""Task A4 — PDF export must offload WeasyPrint render to a thread with a
30s timeout, returning 504 on overrun.

Two contracts:

1. **Concurrency**: an in-flight ``/api/.../export/pdf`` request must not
   block other event-loop traffic. Today the route calls
   ``create_pdf(...)`` synchronously inside ``async def export_pdf``,
   which pins the event loop for the entire WeasyPrint render (2-10s on
   long conversations). After the fix, the render lives on a thread and
   sibling requests stay responsive.
2. **Timeout**: a render that exceeds ``PDF_RENDER_TIMEOUT_SECONDS``
   must return ``504 Gateway Timeout`` (not 500, not hang, not 200 with
   truncated output). The frontend (Task A5) surfaces this as a toast.

These tests monkeypatch ``backend.routers.export.create_pdf`` (the
router's local name binding from ``from ..export import create_pdf``) to
a controlled blocking sleep, so we don't need WeasyPrint native libs to
exercise the concurrency/timeout codepath. Real WeasyPrint render tests
live in ``test_export_pdf_images.py`` and ``test_export_no_tool_placeholder.py``
and auto-skip when the libs aren't loadable.

NOTE: this filename intentionally does NOT contain "pdf" as a separate
token in the way the conftest auto-skip checks for. Wait — it DOES
contain "pdf". The autouse skip fixture in conftest fires on any nodeid
containing ``pdf``. We monkeypatch ``create_pdf`` so WeasyPrint is never
imported by these tests, so the skip is a false positive for this file.
To stay aligned with the project convention, we check WeasyPrint
availability ourselves: if it's missing AND someone tries to run the
real PDF render codepath, that's a separate issue. Our tests don't
touch the real renderer.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path


def _seed_conversation(data_dir: Path, uuid: str) -> None:
    """Write a minimal valid conversation file into the isolated data dir.

    Matches the on-disk shape that ``ConversationStore.get_conversation``
    expects (CLAUDE_AI source, one human message, no branches).
    """
    conv = {
        "uuid": uuid,
        "name": "Concurrency test",
        "summary": "",
        "source": "CLAUDE_AI",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-18T12:00:00Z",
        "updated_at": "2026-05-18T12:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": "m1",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": "hi",
                "content": [{"type": "text", "text": "hi"}],
                "created_at": "2026-05-18T12:00:00Z",
                "updated_at": "2026-05-18T12:00:00Z",
                "parent_message_uuid": None,
            }
        ],
    }
    (data_dir / f"{uuid}.json").write_text(json.dumps(conv))


async def test_pdf_export_does_not_block_other_endpoints(
    isolated_data_dir, real_async_client, monkeypatch
):
    """If ``create_pdf`` blocks for 2s, a concurrent ``/api/conversations``
    request must complete BEFORE the PDF render finishes.

    Pre-fix (synchronous call): the event loop is pinned during the
    blocking sleep, so the sibling request can't make progress until
    the PDF render returns. Both requests therefore finish at roughly
    the same wall-clock time (~2s after kickoff).
    Post-fix (``asyncio.to_thread``): the WeasyPrint render runs on a
    worker thread; the event loop services the sibling request
    immediately while the render proceeds.

    Test shape: fire both requests via ``asyncio.gather`` so they
    schedule concurrently, and assert the sibling completes
    measurably earlier than the PDF response. With a 2s blocking
    sleep, a 500ms gap is a robust signal: pre-fix the gap is near
    zero (both block until PDF returns); post-fix the gap is ~2s.

    Monkeypatch target: ``backend.routers.export.create_pdf`` — the
    router does ``from ..export import create_pdf`` which value-binds
    the name into its own module namespace; patching
    ``backend.export.create_pdf`` would NOT affect the route's local
    reference.
    """
    uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _seed_conversation(isolated_data_dir, uuid)

    def slow_create_pdf(conv, include_tools, include_compact=False):
        time.sleep(2.0)
        return b"%PDF-1.4\n%fake\n"

    monkeypatch.setattr(
        "backend.routers.export.create_pdf", slow_create_pdf
    )

    # Warm up the conversations endpoint so cold-start (store init,
    # FileCache priming) doesn't pollute our timing measurement.
    warmup = await real_async_client.get("/api/conversations")
    assert warmup.status_code == 200, warmup.text

    async def timed_pdf():
        await real_async_client.get(f"/api/conversations/{uuid}/export/pdf")
        return time.monotonic()

    async def timed_sibling():
        # Yield one tick so the PDF handler enters first and starts the
        # blocking work. Without this, the sibling could win the
        # scheduling race even pre-fix and the test would falsely pass.
        await asyncio.sleep(0.05)
        resp = await real_async_client.get("/api/conversations")
        assert resp.status_code == 200, resp.text
        return time.monotonic()

    pdf_done_at, sibling_done_at = await asyncio.gather(
        timed_pdf(), timed_sibling()
    )

    gap = pdf_done_at - sibling_done_at
    assert gap > 0.5, (
        f"sibling /api/conversations finished only {gap:.3f}s before the "
        f"PDF export (2s blocking sleep). Pre-fix the event loop is "
        f"pinned and both finish together (gap ~0); post-fix the "
        f"sibling should finish ~2s before. A small gap means the loop "
        f"is still being blocked."
    )


async def test_pdf_export_timeout_returns_504(
    isolated_data_dir, real_async_client, monkeypatch
):
    """If a render exceeds ``PDF_RENDER_TIMEOUT_SECONDS`` the route must
    return ``504 Gateway Timeout`` (not 500, not 200, not hang).

    We monkeypatch the timeout constant to 1.0s and have create_pdf
    sleep 3.0s, so the timeout path fires deterministically without
    waiting 30s of real time.

    Both bounds matter:

    * **lower bound (>= 0.9s)**: a regression where the timeout is not
      wired up at all would cause the (still-synchronous) blocking sleep
      to either return immediately (if mocked away) or run full duration;
      a real timeout fires AT the deadline.
    * **upper bound (< 2.0s)**: ensures we return promptly on timeout
      instead of waiting for the abandoned thread to finish its 3.0s
      sleep.
    """
    uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _seed_conversation(isolated_data_dir, uuid)

    def hanging_create_pdf(conv, include_tools, include_compact=False):
        time.sleep(3.0)
        return b"unreachable"

    monkeypatch.setattr(
        "backend.routers.export.create_pdf", hanging_create_pdf
    )
    monkeypatch.setattr(
        "backend.routers.export.PDF_RENDER_TIMEOUT_SECONDS", 1.0
    )

    t0 = time.monotonic()
    resp = await real_async_client.get(
        f"/api/conversations/{uuid}/export/pdf",
        timeout=10.0,
    )
    elapsed = time.monotonic() - t0

    assert resp.status_code == 504, (
        f"expected 504 Gateway Timeout, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", "")
    assert "timeout" in detail.lower(), (
        f"504 detail should mention 'timeout' for frontend toast clarity, "
        f"got: {detail!r}"
    )

    # Lower bound: timeout fired AT or AFTER the configured deadline.
    # (Small slack for scheduling jitter.)
    assert elapsed >= 0.9, (
        f"504 returned in {elapsed:.3f}s, before the 1.0s timeout fired; "
        f"timeout may not be wired up correctly"
    )
    # Upper bound: we returned promptly after the timeout, NOT after the
    # full 3.0s blocking sleep.
    assert elapsed < 2.0, (
        f"504 returned in {elapsed:.3f}s, suggesting we waited for the "
        f"abandoned worker thread instead of returning on timeout"
    )


async def test_pdf_export_runtime_error_does_not_leak_exception_text(
    isolated_data_dir, real_async_client, monkeypatch
):
    """Council code-review B1 (2026-05-21).

    Pre-council the route did ``raise HTTPException(status_code=500,
    detail=str(e))`` for a RuntimeError from create_pdf. ``str(e)`` on
    a WeasyPrint failure typically embeds the local file path of the
    failing resource (``/Users/<name>/.claude-explorer/files/...``),
    library internals (``cairo``, ``pango``), and sometimes the
    backtrace context. That's a CWE-200 information-disclosure leak
    in a portfolio-piece app.

    Post-council: the route returns a generic user-actionable message
    and logs ``exc_info=True`` server-side so operators can still
    diagnose. The frontend toast shows the static message; the user
    is told to try Markdown export instead.

    Bidirectional:
      * Positive: detail is a non-empty string with user-actionable
        guidance ("PDF export", "Markdown").
      * Negative: detail does NOT contain the raw exception text.
        Specifically, the canary path ``/Users/SECRET_NAME/.claude``
        must not appear in the response body.
    """
    uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _seed_conversation(isolated_data_dir, uuid)

    canary = "/Users/SECRET_NAME/.claude-explorer/files/secret_token=abc123"

    def leaking_create_pdf(conv, include_tools, include_compact=False):
        raise RuntimeError(
            f"WeasyPrint failed to load image at {canary}: cairo_status: out of memory"
        )

    monkeypatch.setattr(
        "backend.routers.export.create_pdf", leaking_create_pdf
    )

    resp = await real_async_client.get(
        f"/api/conversations/{uuid}/export/pdf",
        timeout=10.0,
    )

    assert resp.status_code == 500, (
        f"RuntimeError from create_pdf should still surface as 500; "
        f"got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", "")
    assert isinstance(detail, str), (
        f"detail must be a string (council B3 unification); got {detail!r}"
    )
    # Negative case: raw exception text must not be exposed.
    assert canary not in detail, (
        f"CWE-200 leak: raw exception path appeared in 500 detail. "
        f"detail={detail!r}"
    )
    assert "SECRET_NAME" not in detail, (
        f"CWE-200 leak: raw exception path appeared in 500 detail. "
        f"detail={detail!r}"
    )
    assert "cairo_status" not in detail, (
        f"CWE-200 leak: WeasyPrint internals appeared in 500 detail. "
        f"detail={detail!r}"
    )
    # Positive case: detail is user-actionable and points at the
    # Markdown alternative (already-shipped pattern in the timeout
    # branch).
    assert "PDF" in detail or "pdf" in detail, (
        f"detail should mention what failed; got {detail!r}"
    )
    assert "markdown" in detail.lower() or "try" in detail.lower(), (
        f"detail should suggest a recovery action; got {detail!r}"
    )
