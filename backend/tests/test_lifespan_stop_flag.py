"""Cooperative stop for long lifespan executor work (Quit during a first build).

The lifespan runs the first FTS5 build (``build_full_index``) and, when FTS5
is off, the image-warm walk (``warm_all_sessions``) in the default executor.
Cancelling the asyncio task abandons the future, but the OS thread keeps
running. ``asyncio.run`` then waits for the default executor at loop close,
and Python joins executor threads at exit. A first build over a large corpus
can take minutes, so after Quit the server process stayed alive (and the
macOS app stayed in the Dock) until the build finished.

The fix: the lifespan owns a ``threading.Event``. It passes the event to each
long executor loop, and its shutdown sets the event before it cancels the
tasks. Each loop checks the event once per conversation and returns early.

This file pins:

  1. ``build_full_index`` stops after the conversation in progress when the
     flag is set mid-build, and does not mark a partial index ready.
  2. A later build without the flag indexes the rest (no lost work).
  3. ``warm_all_sessions`` stops early the same way.
  4. The lifespan passes a stop event to ``build_full_index`` and sets it
     on shutdown, so the executor thread returns promptly.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend import search_index as si
from backend.store import ConversationStore


def _conv(uuid: str) -> dict:
    return {
        "uuid": uuid,
        "name": f"Conv {uuid}",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": f"{uuid}-m1",
        "project_path": None,
        "source": "CLAUDE_AI",
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "text": f"body of {uuid}",
                "content": [{"type": "text", "text": f"body of {uuid}"}],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }


def _store_with_convs(tmp_path: Path, n: int) -> ConversationStore:
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    for i in range(n):
        uuid = f"conv-{i:04d}"
        (by_org / f"{uuid}.json").write_text(json.dumps(_conv(uuid)))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    return ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)


def test_build_full_index_stops_promptly_when_flag_set(tmp_path: Path) -> None:
    """Set the flag after 5 of 200 files: the build returns after file 5.

    Bug it would surface: the build ignores the flag and walks all 200
    files, so Quit waits for the whole first build.
    """
    store = _store_with_convs(tmp_path, 200)
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    stop = threading.Event()

    def _progress(done: int, _total: int) -> None:
        if done == 5:
            stop.set()

    files, msgs = si.build_full_index(
        store, index=idx, on_progress=_progress, stop=stop
    )

    assert files == 5, f"build kept going after the stop flag: {files} files"
    assert msgs == 5
    # A partial index must not go live: search keeps the linear-scan
    # fallback until a full build absorbs every file.
    assert idx.is_ready() is False

    # No lost work: the next build (next startup) indexes the other 195.
    files2, _ = si.build_full_index(store, index=idx)
    assert files2 == 195
    assert idx.is_ready() is True
    idx.close()


def test_build_full_index_with_flag_already_set_indexes_nothing(
    tmp_path: Path,
) -> None:
    store = _store_with_convs(tmp_path, 20)
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    stop = threading.Event()
    stop.set()

    files, msgs = si.build_full_index(store, index=idx, stop=stop)

    assert (files, msgs) == (0, 0)
    assert idx.is_ready() is False
    idx.close()


def test_warm_all_sessions_stops_when_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image-warm walk (FTS5-off fallback) honors the same flag."""
    from backend import cc_image_cache
    from backend import claude_code_reader as ccr
    from backend import config as config_mod

    proj = tmp_path / "claude" / "projects" / "proj-A"
    proj.mkdir(parents=True)
    for i in range(30):
        (proj / f"sess-{i:04d}.jsonl").write_text("{}\n")
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    config_mod.get_settings.cache_clear()

    stop = threading.Event()
    reads: list[Path] = []

    def _fake_read(path: Path) -> dict | None:
        reads.append(path)
        if len(reads) == 3:
            stop.set()
        return None

    monkeypatch.setattr(ccr, "read_claude_code_conversation", _fake_read)
    try:
        state = cc_image_cache.warm_all_sessions(stop=stop)
    finally:
        config_mod.get_settings.cache_clear()

    assert len(reads) == 3, f"warm walk kept going: {len(reads)} reads"
    assert state["sessions_walked"] == 3


@pytest.fixture
def fts5_lifespan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Lifespan with only the FTS5 build task enabled."""
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM", "1")
    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", raising=False)
    config_mod.get_settings.cache_clear()
    si.reset_search_index_for_tests()
    try:
        yield
    finally:
        si.reset_search_index_for_tests()
        config_mod.get_settings.cache_clear()


async def test_lifespan_shutdown_sets_build_stop_flag(fts5_lifespan_env) -> None:
    """Exit the lifespan while the build runs: the build sees the flag.

    The fake build blocks in its executor thread until the stop flag is
    set (or 10 s pass). Before the fix the lifespan passed no flag, so
    the thread could only end by finishing its work.
    """
    from backend.main import app

    started = threading.Event()
    seen: dict[str, Any] = {}

    def _fake_build(*_args: Any, **kwargs: Any) -> tuple[int, int]:
        stop = kwargs.get("stop")
        seen["stop"] = stop
        started.set()
        if stop is None:
            return (0, 0)
        seen["stopped_in_time"] = stop.wait(timeout=10.0)
        seen["returned_at"] = time.monotonic()
        return (0, 0)

    with patch("backend.search_index.build_full_index", side_effect=_fake_build):
        async with app.router.lifespan_context(app):
            # Deterministic settle signal: the fake build has started.
            assert await asyncio.to_thread(started.wait, 10.0), (
                "build_full_index never started"
            )
            assert seen["stop"] is not None, (
                "lifespan did not pass a stop flag to build_full_index"
            )
            assert not seen["stop"].is_set()
            exit_at = time.monotonic()

        # The executor thread wakes on the flag; wait for it to return.
        for _ in range(200):
            if "returned_at" in seen:
                break
            await asyncio.sleep(0.01)

    assert seen["stop"].is_set(), "lifespan shutdown did not set the stop flag"
    assert seen.get("stopped_in_time") is True
    assert seen["returned_at"] - exit_at < 2.0
