"""W2 — Pre-execute FTS5 warmup queries at lifespan startup.

Per the 2026-05-23 council decision record, the first user search
after a server restart takes ~6 s on the user's real corpus because
SQLite's FTS5 page cache is cold. The fix: issue two warmup queries
during lifespan startup right after build_full_index completes:

  1. A no-match sentinel ("warmup_zzzz_xyzzy_nomatch") — exercises
     the term-dictionary lookup path (proves the FTS5 read-side is
     functional and warms the dictionary B-tree pages).
  2. A common-term query ("the" with LIMIT 1) — forces the engine
     to actually read doclist + segment pages, which is what
     short-circuited no-match queries don't.

Both queries are issued via asyncio.to_thread so they don't block
the event loop. The total cost is bounded (~100 ms on the user's
corpus).

Contracts pinned:

  T1: the warm task fires at least 2 queries via SearchIndex.query
      after build_full_index completes.
  T2: env var CLAUDE_EXPLORER_DISABLE_FTS5_WARM=1 disables warmup.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_jsonl(path: Path, session_uuid: str) -> None:
    """Minimal JSONL session — same shape as test_lifespan_cold_start."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "type": "user",
            "uuid": f"{session_uuid}-u1",
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T10:00:00Z",
            "cwd": "/tmp/proj",
            "gitBranch": "main",
            "version": "1.0",
            "message": {"role": "user", "content": "hello"},
        },
        {
            "type": "assistant",
            "uuid": f"{session_uuid}-a1",
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T10:00:01Z",
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
def fts5_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Seed a small corpus and ENABLE the FTS5 search index so the
    warm task has something to query against."""
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    proj = claude_dir / "projects" / "proj-A"
    proj.mkdir(parents=True)
    for i in range(3):
        _write_jsonl(proj / f"sess-{i:04d}.jsonl", f"sess-{i:04d}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Suppress unrelated lifespan tasks. CRITICALLY, do NOT disable
    # the search index — that's what we're testing.
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")

    config_mod.get_settings.cache_clear()
    # Reset the search index singleton so the test app uses a fresh
    # in-memory / file-backed index.
    try:
        from backend.search_index import reset_search_index_for_tests
        reset_search_index_for_tests()
    except ImportError:
        pass
    try:
        yield claude_dir
    finally:
        config_mod.get_settings.cache_clear()
        try:
            from backend.search_index import reset_search_index_for_tests
            reset_search_index_for_tests()
        except ImportError:
            pass


async def test_lifespan_fires_fts5_warmup_queries_after_build(
    fts5_corpus: Path,
) -> None:
    """T1: after the FTS5 index build completes, the lifespan
    invokes SearchIndex.query at least twice (the no-match sentinel
    and the common-term warmup)."""
    from backend.main import app

    seen_queries: list[str] = []

    real_query = None

    def _spy(self, user_query, **kwargs):
        seen_queries.append(user_query)
        if real_query is not None:
            return real_query(self, user_query, **kwargs)
        return []

    with patch(
        "backend.search_index.SearchIndex.query",
        autospec=True,
        side_effect=_spy,
    ):
        async with app.router.lifespan_context(app):
            # Wait for the FTS5 build (with its 500ms head-start sleep)
            # AND the warm queries to fire. Total budget: ~5 s.
            for _ in range(60):
                if len(seen_queries) >= 2:
                    break
                await asyncio.sleep(0.1)

    assert len(seen_queries) >= 2, (
        f"Expected at least 2 SearchIndex.query calls (warmup queries) "
        f"after the FTS5 build completed; saw {len(seen_queries)}: "
        f"{seen_queries!r}"
    )
    # Pin the specific warmup needles. The exact strings are an
    # implementation contract — if a future maintainer renames the
    # sentinel, they should update both the source AND this test.
    assert any("warmup" in q.lower() for q in seen_queries), (
        f"Expected one warmup query to contain 'warmup' sentinel; "
        f"saw {seen_queries!r}"
    )


async def test_lifespan_fts5_warm_respects_disable_env_var(
    fts5_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2: env var CLAUDE_EXPLORER_DISABLE_FTS5_WARM=1 disables the
    warmup queries. Bidirectional differential check."""
    from backend.config import get_settings as gs
    from backend.main import app

    async def _trial(disabled: bool) -> int:
        gs.cache_clear()
        try:
            from backend.search_index import reset_search_index_for_tests
            reset_search_index_for_tests()
        except ImportError:
            pass
        if disabled:
            monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_FTS5_WARM", "1")
        else:
            monkeypatch.delenv(
                "CLAUDE_EXPLORER_DISABLE_FTS5_WARM", raising=False
            )

        local_seen: list[str] = []

        def _spy(self, user_query, **kwargs):
            local_seen.append(user_query)
            return []

        with patch(
            "backend.search_index.SearchIndex.query",
            autospec=True,
            side_effect=_spy,
        ):
            async with app.router.lifespan_context(app):
                for _ in range(40):
                    if len(local_seen) >= 2:
                        break
                    await asyncio.sleep(0.1)

        return len(local_seen)

    enabled_count = await _trial(disabled=False)
    disabled_count = await _trial(disabled=True)

    assert enabled_count >= 2, (
        f"Enabled trial saw only {enabled_count} warmup queries; "
        f"W2 warm task may not be wired (test would trivially pass)."
    )
    assert disabled_count == 0, (
        f"Disabled trial saw {disabled_count} warmup queries; "
        f"CLAUDE_EXPLORER_DISABLE_FTS5_WARM=1 is not honored."
    )
