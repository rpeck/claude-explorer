"""Tests for the auto-warm CC image-cache pass.

The user's V1 complaint: "I don't want the user to have to use the
CLI except in extreme circumstances. E.g., this should not be
necessary to ensure we've cached all the images:
``uv run claude-explorer warm-cc-cache``."

Fix: a background task in the FastAPI lifespan calls
:func:`backend.cc_image_cache.warm_all_sessions_async` at every
startup. The CLI remains as a manual override but is no longer
required for normal operation.

Spec-driven discipline (TESTING.md §1):
    Files consulted while authoring this test:
      * ``backend/cc_image_cache.py`` (warm_all_sessions / _async)
      * ``backend/main.py`` (lifespan startup hook)
      * ``backend/tests/test_cc_watcher.py`` (sibling pattern)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_jsonl_session(claude_dir: Path, sess: str, image_path: Path) -> Path:
    """Write a minimal CC JSONL session that references one
    ``[Image: source: <abs>]`` marker.

    The ``read_claude_code_conversation`` parser yields a dict shaped like
    ``{"uuid": ..., "chat_messages": [{"content": [{"type":"text","text":...}]}]}``.
    The marker text is what cache_all_markers walks.
    """
    project_dir = claude_dir / "projects" / "test-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl = project_dir / f"{sess}.jsonl"
    # Minimal user-message line that read_claude_code_conversation will
    # parse into a chat_messages entry.
    jsonl.write_text(
        json.dumps({
            "type": "user",
            "uuid": "msg-1",
            "sessionId": sess,
            "timestamp": "2026-05-10T12:00:00Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"hello [Image: source: {image_path}]"},
                ],
            },
        }) + "\n"
    )
    return jsonl


def test__warm_all_sessions__populates_cache_for_referenced_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """warm_all_sessions walks every CC session JSONL and copies each
    referenced [Image: source: …] file into the permanent cache.

    Verifies the no-CLI-required path: a freshly-installed backend with
    a CC session containing an image marker should populate the cache
    without any user action.
    """
    # Set up an isolated claude-dir + data-dir layout.
    claude_dir = tmp_path / "claude"
    data_dir = tmp_path / "exporter"
    image_cache_dir = claude_dir / "image-cache" / "sess-warm-1"
    image_cache_dir.mkdir(parents=True)
    fixture_png = image_cache_dir / "1.png"
    fixture_png.write_bytes(b"\x89PNG\r\n\x1a\nWARM_PASS_FIXTURE_BYTES_AAA")

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]

    _make_jsonl_session(claude_dir, "sess-warm-1", fixture_png)

    from backend.cc_image_cache import warm_all_sessions, cache_dir

    state = warm_all_sessions()

    assert state["sessions_walked"] >= 1, (
        f"warm_all_sessions should walk >=1 session; state={state!r}"
    )
    assert state["files_cached"] >= 1, (
        f"warm_all_sessions should cache >=1 file; state={state!r}"
    )

    # The cache layout is <cache_dir>/<conv-uuid>/<sess>--<N>.<sha8>.<ext>.
    # For CC sessions conv-uuid == sess-uuid == jsonl-stem.
    cached = list(cache_dir().glob("sess-warm-1/*--1.*.png"))
    assert cached, (
        f"expected a cached copy of the fixture image under "
        f"{cache_dir()}/sess-warm-1/; got: {list(cache_dir().rglob('*'))!r}"
    )
    assert cached[0].read_bytes() == fixture_png.read_bytes(), (
        "cached file bytes must equal fixture bytes"
    )


def test__warm_all_sessions__idempotent_re_run_no_dupes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running warm_all_sessions on a fully-cached state must not
    create duplicates or grow the cache.

    Idempotency matters because the FastAPI lifespan calls this on
    every startup; we don't want the cache directory to grow unbounded
    on every restart.
    """
    claude_dir = tmp_path / "claude"
    data_dir = tmp_path / "exporter"
    image_cache_dir = claude_dir / "image-cache" / "sess-warm-2"
    image_cache_dir.mkdir(parents=True)
    fixture_png = image_cache_dir / "1.png"
    fixture_png.write_bytes(b"\x89PNG\r\n\x1a\nIDEM_FIXTURE_BBB")

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]

    _make_jsonl_session(claude_dir, "sess-warm-2", fixture_png)

    from backend.cc_image_cache import warm_all_sessions, cache_dir

    warm_all_sessions()
    cached_after_first = list(cache_dir().rglob("*"))

    warm_all_sessions()
    cached_after_second = list(cache_dir().rglob("*"))

    assert sorted(p.name for p in cached_after_first) == sorted(
        p.name for p in cached_after_second
    ), (
        f"second warm pass must NOT add files; "
        f"first={[p.name for p in cached_after_first]!r}, "
        f"second={[p.name for p in cached_after_second]!r}"
    )


@pytest.mark.asyncio
async def test__warm_all_sessions_async__returns_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async wrapper must return the same state dict as the sync
    function and must NOT raise on whole-pass exceptions (instead
    returns ``{"error": ...}`` and logs).
    """
    claude_dir = tmp_path / "claude"
    data_dir = tmp_path / "exporter"
    image_cache_dir = claude_dir / "image-cache" / "sess-warm-3"
    image_cache_dir.mkdir(parents=True)
    fixture_png = image_cache_dir / "1.png"
    fixture_png.write_bytes(b"\x89PNG\r\n\x1a\nASYNC_FIXTURE_CCC")

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]

    _make_jsonl_session(claude_dir, "sess-warm-3", fixture_png)

    from backend.cc_image_cache import warm_all_sessions_async

    state = await warm_all_sessions_async()
    assert "sessions_walked" in state, f"missing sessions_walked in {state!r}"
    assert state["sessions_walked"] >= 1
    assert state["files_cached"] >= 1
