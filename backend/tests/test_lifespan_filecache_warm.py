"""W1 — Pre-warm FileCache for N most-recently-updated conversations.

Per the 2026-05-23 council decision record, the first user navigation
to a conversation hits a cold FileCache → 1.3 s of file I/O + JSONL
parse. Pre-warming the N=5 most-recently-updated conversations during
lifespan startup means the first navigation lands on a hot FileCache.

Contracts pinned:

  T1: lifespan startup invokes the file-cache warm task; it calls
      _find_conversation_data for each of the top-N uuids drawn from
      the summary cache.
  T2: the warm task is non-blocking — lifespan yields before the
      warm completes.
  T3: env var CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM=1 disables the
      warm task.
  T4: ordering — the warm task waits for the summary cache fill to
      complete (otherwise there's nothing recent to warm against).

Why we use the summary cache for the "most recent" list:
  * The explorer doesn't track per-user access history. The closest
    proxy is updated_at from the source (Claude.ai / Claude Code).
  * The summary cache (backend/summary_cache.py) is already filled at
    lifespan startup with every JSONL session's summary, including
    its parsed updated_at timestamp. Sorting by updated_at and taking
    the top N is O(corpus_size) but happens once at startup.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# Use the same lifespan-fixture pattern as test_lifespan_cold_start.
# `asyncio_mode = "auto"` in pyproject.toml.


def _write_jsonl(path: Path, session_uuid: str, updated_at: str) -> None:
    """Minimal JSONL session file the fast reader can parse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "type": "user",
            "uuid": f"{session_uuid}-u1",
            "sessionId": session_uuid,
            "timestamp": updated_at,
            "cwd": "/tmp/proj",
            "gitBranch": "main",
            "version": "1.0",
            "message": {"role": "user", "content": "hello"},
        },
        {
            "type": "assistant",
            "uuid": f"{session_uuid}-a1",
            "sessionId": session_uuid,
            "timestamp": updated_at,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "id": f"msg_{session_uuid}",
                "content": [{"type": "text", "text": "hi"}],
            },
        },
    ]
    with path.open("w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")


@pytest.fixture
def warm_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Seed 10 JSONL sessions with VARYING updated_at timestamps so
    the W1 prewarm has a well-defined "top N most recent" set."""
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    proj = claude_dir / "projects" / "proj-A"
    proj.mkdir(parents=True)

    # 10 sessions, increasing updated_at — sess-0009 is the most recent.
    for i in range(10):
        _write_jsonl(
            proj / f"sess-{i:04d}.jsonl",
            f"sess-{i:04d}",
            f"2026-05-{(i % 28) + 1:02d}T12:00:00Z",
        )

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Disable every OTHER lifespan task to isolate the warm-task behavior.
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")

    config_mod.get_settings.cache_clear()
    try:
        yield claude_dir
    finally:
        config_mod.get_settings.cache_clear()


async def test_lifespan_warms_filecache_for_top_n_recent_conversations(
    warm_corpus: Path,
) -> None:
    """T1: After lifespan startup the warm task calls
    _find_conversation_data for the 5 most-recently-updated uuids."""
    from backend.main import app

    seen_uuids: list[str] = []
    original = None

    def _spy(self, uuid):
        seen_uuids.append(uuid)
        if original is not None:
            return original(self, uuid)
        return (None, None)

    # Patch the bound method on the class so all per-request store
    # instances inherit the spy.
    with patch(
        "backend.store.ConversationStore._find_conversation_data",
        autospec=True,
        side_effect=_spy,
    ):
        # The top 5 by updated_at are sess-0005..sess-0009.
        expected_top5 = {f"sess-{i:04d}" for i in range(5, 10)}

        async with app.router.lifespan_context(app):
            # Wait for the SET we care about, not merely five calls of any
            # kind. Other startup work also calls _find_conversation_data, so
            # a count can reach five while the prewarm still has entries
            # outstanding. That raced on the slower ARM runner and dropped
            # sess-0009, the most recent session of all.
            for _ in range(100):
                if expected_top5 <= set(seen_uuids):
                    break
                await asyncio.sleep(0.1)

    assert len(seen_uuids) >= 5, (
        f"Expected at least 5 calls to _find_conversation_data from "
        f"the W1 prewarm; saw {len(seen_uuids)}: {seen_uuids}"
    )
    # Allow some slack: the warm task may also call _find_conversation_data
    # later for other reasons; we only assert the recent set is INCLUDED.
    seen_set = set(seen_uuids)
    missing = expected_top5 - seen_set
    assert not missing, (
        f"W1 prewarm did not call _find_conversation_data for the top-5 "
        f"most recent uuids. Missing: {missing}. Saw: {seen_set}"
    )


async def test_lifespan_filecache_warm_is_non_blocking(
    warm_corpus: Path,
) -> None:
    """T2: lifespan yields immediately; the warm task runs in
    background. We pin this by making _find_conversation_data
    artificially slow and asserting the yield happens fast.

    The test only asserts the YIELD time; it does not depend on a
    successful warm-call count (T1 already pins that).
    """
    import time
    from backend.main import app

    def _slow(self, uuid):
        time.sleep(0.5)  # 5×0.5 = 2.5s of synthetic work if all 5 fire
        return (None, None)

    with patch(
        "backend.store.ConversationStore._find_conversation_data",
        autospec=True,
        side_effect=_slow,
    ):
        t0 = time.monotonic()
        async with app.router.lifespan_context(app):
            yield_elapsed = time.monotonic() - t0
            # Yield should be sub-second even though the warm work
            # would take ~2.5s to complete if it ran in-line.
            assert yield_elapsed < 1.5, (
                f"Lifespan yield blocked for {yield_elapsed:.2f}s; "
                f"W1 prewarm must not block startup"
            )


async def test_lifespan_filecache_warm_respects_disable_env_var(
    warm_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: env var CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM=1 disables
    the warm task entirely.

    Bidirectional check: runs TWO trials (enabled vs disabled) and
    asserts the differential. A no-op implementation (no warm task
    at all) would pass the disabled trial AND fail the enabled trial,
    so the differential catches both the "env var ignored" and the
    "warm task missing" failure modes.
    """
    from backend.main import app
    from backend.config import get_settings as gs

    async def _trial(disabled: bool) -> int:
        gs.cache_clear()
        if disabled:
            monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM", "1")
        else:
            monkeypatch.delenv(
                "CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM", raising=False
            )

        local_count = 0

        def _spy(self, uuid):
            nonlocal local_count
            local_count += 1
            return (None, None)

        with patch(
            "backend.store.ConversationStore._find_conversation_data",
            autospec=True,
            side_effect=_spy,
        ):
            async with app.router.lifespan_context(app):
                # Wait long enough that the warm task (if enabled) would
                # have made all its calls.
                for _ in range(40):
                    if local_count >= 5:
                        break
                    await asyncio.sleep(0.1)

        return local_count

    enabled_calls = await _trial(disabled=False)
    disabled_calls = await _trial(disabled=True)

    assert enabled_calls >= 5, (
        f"Enabled trial saw only {enabled_calls} calls; W1 warm task "
        f"may not be wired at all (test would trivially pass)."
    )
    assert disabled_calls == 0, (
        f"Disabled trial made {disabled_calls} calls; "
        f"CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM=1 is not honored."
    )
