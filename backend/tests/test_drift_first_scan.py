"""Drift-first scan tests (PLANS/SEARCH_INDEX_FRESHNESS.md).

Pins the new ``_drift_first_scan`` helper plus the refactored
``build_full_index`` / ``update_drifted_files`` that wrap it.

The contract:
  * Enumerate every on-disk conversation path (Desktop + CC) via
    the existing path-discovery helpers. NO content load.
  * Compare against ``indexed_files`` rows (path + mtime). Diff
    yields ``(drifted_paths, missing_paths)``.
  * Load conversation content ONLY for drifted paths.
  * Cleanup pass deletes ``indexed_files`` rows whose paths have
    vanished from disk.

Performance win: warm restart with zero drift goes from ~10 s
(today's ``get_all_conversations_raw`` walk) to ~100-300 ms
(stat-only). First install is unchanged: every path is "drifted"
so every file gets loaded once anyway.

Correctness invariant pinned by test 6: ``mark_ready()`` only fires
AFTER the drift set has been absorbed. Otherwise a stale FTS5 row
could be served between the version bump and the rebuild finishing,
silently breaking the "update session, restart, ⌘+K finds it" use
case.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from backend import search_index as si
from backend.store import ConversationStore

# ----- fixtures ----------------------------------------------------


def _write_desktop_conv(by_org_dir: Path, uuid: str, name: str) -> Path:
    """Drop a Desktop-shaped JSON file. Returns the on-disk path."""
    by_org_dir.mkdir(parents=True, exist_ok=True)
    path = by_org_dir / f"{uuid}.json"
    conv = {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": f"{uuid}-m1",
        "source": "CLAUDE_AI",
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "text": f"hello from {uuid}",
                "content": [{"type": "text", "text": f"hello from {uuid}"}],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }
    path.write_text(json.dumps(conv))
    return path


def _write_cc_jsonl(claude_dir: Path, project: str, session_uuid: str) -> Path:
    """Drop a minimal CC JSONL session file. Returns the on-disk path."""
    proj = claude_dir / "projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_uuid}.jsonl"
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
    return path


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Build a per-test store with a Desktop + CC corpus on disk.

    Seeds:
      * 2 Desktop convs under ``data_dir/by-org/test-org/``.
      * 2 CC JSONLs under ``claude_dir/projects/proj-A/``.

    Yields a dict with all the paths the tests need.
    """
    from backend import config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    config.get_settings.cache_clear()

    by_org = data_dir / "by-org" / "test-org"
    d1 = _write_desktop_conv(by_org, "d0000001-0000-0000-0000-000000000001", "Desktop one")
    d2 = _write_desktop_conv(by_org, "d0000002-0000-0000-0000-000000000002", "Desktop two")

    c1 = _write_cc_jsonl(claude_dir, "proj-A", "c0000001-0000-0000-0000-000000000001")
    c2 = _write_cc_jsonl(claude_dir, "proj-A", "c0000002-0000-0000-0000-000000000002")

    store = ConversationStore(data_dir=data_dir, claude_dir=claude_dir)

    yield {
        "store": store,
        "data_dir": data_dir,
        "claude_dir": claude_dir,
        "desktop": [d1, d2],
        "cc": [c1, c2],
        "all_paths": {d1, d2, c1, c2},
    }

    config.get_settings.cache_clear()


@pytest.fixture
def fresh_index(tmp_path):
    """A SearchIndex pointed at a per-test sqlite file. No singleton."""
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    yield idx
    idx.close()


# ----- 1. Empty index → every path is drifted --------------------


def test_empty_index_marks_every_path_as_drifted(isolated_store, fresh_index):
    """When ``indexed_files`` is empty, every on-disk conversation path
    must show up in ``drifted_paths``. Nothing should show up as missing
    because nothing was indexed to begin with.

    Bug it would surface: the diff treats "no row" as "indexed and
    unchanged", so the first-install corpus never gets indexed and
    search returns zero results until the user touches a file.
    """
    store = isolated_store["store"]
    expected = isolated_store["all_paths"]

    drifted, missing = si._drift_first_scan(store, fresh_index)

    # After Phase 3 (Cowork), drifted is now list[tuple[Path, str]].
    drifted_paths = {p for p, _ in drifted}
    assert drifted_paths == expected, (
        f"Empty index must mark every on-disk path as drifted. "
        f"Got drifted={drifted_paths}, expected={expected}"
    )
    assert set(missing) == set(), (
        f"Empty index has nothing to clean up; missing must be empty. "
        f"Got {set(missing)}"
    )


# ----- 2. All files unchanged → zero drift ----------------------


def test_unchanged_files_produce_zero_drift(isolated_store, fresh_index):
    """After a full build, calling ``_drift_first_scan`` again with no
    on-disk changes must return empty drift and empty missing.

    Bug it would surface: drift detection re-loads every file on every
    pass (the bug the entire plan is fixing). Warm-restart latency
    stays at the ~10 s ``get_all_conversations_raw`` cost.
    """
    store = isolated_store["store"]

    # First pass populates indexed_files for every on-disk path.
    si.build_full_index(store, index=fresh_index)

    # Second pass: nothing should be drifted, nothing should be missing.
    drifted, missing = si._drift_first_scan(store, fresh_index)

    assert list(drifted) == [], (
        f"Unchanged corpus must yield empty drift; got {list(drifted)}"
    )
    assert list(missing) == [], (
        f"Unchanged corpus must yield empty missing; got {list(missing)}"
    )


# ----- 3. One file modified → one drifted -----------------------


def test_one_modified_file_is_drifted(isolated_store, fresh_index):
    """Bumping one file's mtime via ``touch`` must surface that single
    path (and no others) as drifted.

    Bug it would surface: the mtime comparison is short-circuited or
    inverted, so real edits get missed entirely.
    """
    store = isolated_store["store"]
    si.build_full_index(store, index=fresh_index)

    target = isolated_store["cc"][0]
    # Bump mtime by a safe margin so float-equality comparisons resolve.
    future = time.time() + 10.0
    import os
    os.utime(target, (future, future))

    drifted, missing = si._drift_first_scan(store, fresh_index)

    drifted_paths = {p for p, _ in drifted}
    assert drifted_paths == {target}, (
        f"Only the touched file should be drifted. "
        f"Got {drifted_paths}, expected {{{target}}}"
    )
    assert list(missing) == [], (
        f"Touching a file does not delete it; missing must be empty. "
        f"Got {list(missing)}"
    )


# ----- 4. One file deleted → one missing ------------------------


def test_one_deleted_file_is_missing(isolated_store, fresh_index):
    """Removing one file from disk must surface that path (and no
    others) as missing. It must NOT show up in drifted.

    Bug it would surface: the cleanup path never runs, so deleted
    sessions stay searchable in FTS5 long after they're gone — a
    correctness regression.
    """
    store = isolated_store["store"]
    si.build_full_index(store, index=fresh_index)

    victim = isolated_store["desktop"][1]
    victim.unlink()

    drifted, missing = si._drift_first_scan(store, fresh_index)

    assert set(missing) == {victim}, (
        f"Only the deleted file should be missing. "
        f"Got {set(missing)}, expected {{{victim}}}"
    )
    assert victim not in set(drifted), (
        f"Deleted file should not appear in drifted; got drifted={set(drifted)}"
    )


# ----- 5. Second build is content-free when nothing drifted -----


def test_second_build_does_not_call_get_all_conversations_raw(
    isolated_store, fresh_index, monkeypatch
):
    """The whole point of drift-first: a warm restart with zero drift
    must skip the expensive ``get_all_conversations_raw`` walk.

    Bug it would surface: the refactor regresses to today's behavior of
    loading every conversation's full body on every build pass. Warm
    restart latency stays at ~10 s instead of dropping to ~100-300 ms.
    """
    store = isolated_store["store"]
    # First build populates indexed_files for every path.
    si.build_full_index(store, index=fresh_index)

    # Now spy on get_all_conversations_raw — second build with no drift
    # must NOT call it.
    call_count = {"n": 0}
    original = store.get_all_conversations_raw

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "get_all_conversations_raw", _spy)

    si.build_full_index(store, index=fresh_index)

    assert call_count["n"] == 0, (
        f"Second build with zero drift must NOT call "
        f"get_all_conversations_raw; got {call_count['n']} calls. "
        f"The drift-first refactor lost its central optimization."
    )


# ----- 6. mark_ready fires AFTER drift absorbed -----------------


def test_mark_ready_fires_after_drift_absorbed(
    isolated_store, fresh_index, monkeypatch
):
    """The correctness invariant: ``is_ready()`` must stay False until
    every drifted file has been upserted. Otherwise queries served
    between schema-rebuild and drift-absorption hit a half-built index.

    Strategy: patch the per-file loader to sleep ~1 s. Spawn
    ``build_full_index`` in a thread. Sample ``is_ready()`` mid-flight
    and assert False; then join the thread and assert True.

    Bug it would surface: a refactor that calls ``mark_ready()`` BEFORE
    the drift loop would silently break the use case "update a CC
    session, restart explorer, immediately ⌘+K-search for the new
    content" — FTS5 would return stale results during the build
    window.
    """
    store = isolated_store["store"]

    real_loader = si._load_conversation_at

    def _slow_loader(path: Path, store_arg, source=None):
        time.sleep(1.0)
        return real_loader(path, store_arg, source=source)

    monkeypatch.setattr(si, "_load_conversation_at", _slow_loader)

    done = threading.Event()
    result = {}

    def _build():
        try:
            si.build_full_index(store, index=fresh_index)
        finally:
            done.set()

    t = threading.Thread(target=_build, daemon=True)
    t.start()

    # Sample mid-flight: the slow loader is firing on at least one
    # drifted file, so is_ready() must be False right now.
    time.sleep(0.3)
    mid_ready = fresh_index.is_ready()

    done.wait(timeout=30.0)
    t.join(timeout=5.0)

    end_ready = fresh_index.is_ready()

    assert mid_ready is False, (
        "is_ready() returned True while the drift-absorption loop was "
        "still running. mark_ready() must fire AFTER the loop, not "
        "before — otherwise FTS5 serves stale results during the "
        "build window."
    )
    assert end_ready is True, (
        f"is_ready() must be True after build_full_index completes; got "
        f"{end_ready}. Did the build fail mid-flight? result={result}"
    )
