"""Tests for backend.cc_watcher.

The watcher polls ``~/.claude/image-cache/`` periodically and copies
any new files to the permanent cache. Tests cover:
  - cold scan picks up files already on disk
  - second scan is a no-op (idempotent via the per-process _seen set)
  - new files dropped after the first scan are picked up on the next
  - non-image files are ignored (and remembered as ignored)
  - source rotation between scans doesn't blow up the watcher
"""

from __future__ import annotations

import base64

import pytest

# 1x1 transparent PNG bytes — same payload used elsewhere.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAA"
    "YAAjCB0C8AAAAASUVORK5CYII="
)
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)
OTHER_PNG_BYTES = TINY_PNG_BYTES + b"\x00other"


@pytest.fixture
def watcher_env(tmp_path, monkeypatch):
    """Stand up isolated CLAUDE_DIR + CLAUDE_EXPLORER_DATA_DIR and
    clear the watcher's per-process ``_seen`` cache between tests.
    """
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    (claude_dir / "image-cache").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))

    from backend import cc_watcher, config

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()

    yield {
        "claude_dir": claude_dir,
        "data_dir": data_dir,
        "image_cache": claude_dir / "image-cache",
        "perm_cache_root": data_dir.parent / "cc-images"
        if data_dir.name == "conversations"
        else data_dir / "cc-images",
    }

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()


def _drop_image(image_cache, sess: str, n: str, payload: bytes) -> None:
    sess_dir = image_cache / sess
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / f"{n}.png").write_bytes(payload)


def _cached_files(perm_cache_root, sess: str, n: str):
    if not perm_cache_root.exists():
        return []
    return list(perm_cache_root.glob(f"{sess}/{sess}--{n}.*.png"))


def test_cold_scan_picks_up_existing_files(watcher_env):
    from backend import cc_watcher

    _drop_image(watcher_env["image_cache"], "sess-cold", "1", TINY_PNG_BYTES)
    _drop_image(watcher_env["image_cache"], "sess-cold", "2", TINY_PNG_BYTES)

    handled = cc_watcher.scan_once()
    assert handled == 2

    cached = _cached_files(watcher_env["perm_cache_root"], "sess-cold", "1")
    assert len(cached) == 1
    assert cached[0].read_bytes() == TINY_PNG_BYTES


def test_second_scan_is_idempotent_via_seen_set(watcher_env):
    from backend import cc_watcher

    _drop_image(watcher_env["image_cache"], "sess-seen", "1", TINY_PNG_BYTES)
    assert cc_watcher.scan_once() == 1
    # Second pass: same path is already in _seen, so it counts 0 newly
    # handled.
    assert cc_watcher.scan_once() == 0


def test_files_added_after_first_scan_caught_on_next_pass(watcher_env):
    from backend import cc_watcher

    _drop_image(watcher_env["image_cache"], "sess-incr", "1", TINY_PNG_BYTES)
    assert cc_watcher.scan_once() == 1

    # Simulate Claude Code dropping a NEW file after our first sweep.
    _drop_image(watcher_env["image_cache"], "sess-incr", "2", OTHER_PNG_BYTES)
    assert cc_watcher.scan_once() == 1

    cached_2 = _cached_files(watcher_env["perm_cache_root"], "sess-incr", "2")
    assert len(cached_2) == 1
    assert cached_2[0].read_bytes() == OTHER_PNG_BYTES


def test_non_image_extension_ignored(watcher_env):
    from backend import cc_watcher

    sess_dir = watcher_env["image_cache"] / "sess-other"
    sess_dir.mkdir(parents=True)
    (sess_dir / "notes.txt").write_bytes(b"not an image")

    handled = cc_watcher.scan_once()
    assert handled == 0
    assert not watcher_env["perm_cache_root"].exists() or not list(
        watcher_env["perm_cache_root"].rglob("*.txt")
    )


# ---------------------------------------------------------------------------
# Search-index drift pass — REAL-state tests (C2 hardening, 2026-05-18)
#
# These tests previously mocked ``backend.search_index._search_index`` and
# ``backend.search_index.update_drifted_files`` to verify that scan_once()
# drives the drift pass. The mocks hid whether the production wiring
# actually upserts rows into FTS5 — a passing mock-test with broken
# production code was a real failure mode. The rewrite drops the mocks
# and asserts against the real on-disk index, so a broken drift pass
# surfaces as an empty query result.
# ---------------------------------------------------------------------------


@pytest.fixture
def real_search_index(watcher_env, tmp_path, monkeypatch):
    """Build a per-test ``SearchIndex`` pointed at a tmp sqlite file
    and install it as the ``backend.search_index._search_index``
    singleton.

    Fixture-scope (not inline ``monkeypatch.setattr`` in the test body)
    so teardown ordering is correct under pytest-xdist: pytest tears
    fixtures down LIFO, so an inline monkeypatch teardown would null
    out the singleton BEFORE we get a chance to call ``idx.close()``,
    leaking a SQLite file handle per test. Here we yield the index,
    then on teardown explicitly ``idx.close()`` and restore whatever
    the prior singleton value was (typically ``None``).

    Yields the ``SearchIndex`` instance so tests can call
    ``mark_ready()`` / ``query()`` / inspect ``list_indexed_paths()``.
    """
    from backend import search_index as si

    idx = si.SearchIndex(tmp_path / "real-index.sqlite")
    prior = si._search_index
    si._search_index = idx

    try:
        yield idx
    finally:
        # Restore the prior singleton FIRST so any concurrent code
        # sees the right value, then close our handle. The autouse
        # ``isolate_search_index_singleton`` conftest fixture will run
        # its own reset on teardown immediately after; it's a no-op
        # because ``prior`` is typically None.
        si._search_index = prior
        try:
            idx.close()
        except Exception:  # noqa: BLE001, S110 — teardown is best-effort
            pass


def _write_real_cc_session(claude_dir, project: str, session_uuid: str, body: str):
    """Drop a real CC JSONL session that ``read_claude_code_conversation``
    can parse.

    CRITICAL: passes ``session_id=session_uuid`` to the user-entry
    builder. The reader extracts ``conv["uuid"]`` from the first
    user entry's ``sessionId``, NOT from the filename stem (with the
    stem as fallback only). If the builder default ``"sess"`` leaks
    through, the FTS5 ``conv_uuid`` won't match the filename and the
    by-uuid assertion below would spuriously fail.
    """
    from backend.tests import builders as B

    path = claude_dir / "projects" / project / f"{session_uuid}.jsonl"
    entries = [
        B.build_cc_user_entry(
            uuid=f"{session_uuid}-u1",
            text=body,
            session_id=session_uuid,
        ),
        B.build_cc_assistant_entry(
            uuid=f"{session_uuid}-a1",
            msg_id=f"msg_{session_uuid}",
            text="ack",
            session_id=session_uuid,
        ),
    ]
    return B.write_cc_jsonl(path, entries)


def test_scan_once_runs_search_index_drift_pass(
    watcher_env, real_search_index,
):
    """When ``scan_once()`` runs with a ready index, a newly-written CC
    JSONL must end up indexed in FTS5 — discoverable by a body-token
    query and recorded in ``indexed_files``.

    Bug it would surface: forgetting to wire the drift pass into the
    watcher → search index never picks up file changes between
    backend restarts. (The old mock-based test would PASS even if the
    upsert silently dropped rows, because it only checked that the
    function was called.)
    """
    from backend import cc_watcher

    real_search_index.mark_ready()

    session_uuid = "abcdef01-0000-0000-0000-000000000001"
    body = "needle_real_drift_pass_aardvark"
    jsonl_path = _write_real_cc_session(
        watcher_env["claude_dir"], "proj-A", session_uuid, body,
    )

    cc_watcher.scan_once()

    # Direct evidence #1: the file is recorded in indexed_files.
    indexed = real_search_index.list_indexed_paths()
    assert jsonl_path in indexed, (
        f"scan_once() must drive update_drifted_files to upsert the new "
        f"JSONL into FTS5 indexed_files. Indexed paths: {indexed}"
    )

    # Direct evidence #2: a real FTS5 query against the body returns
    # the session, with conv_uuid == session UUID (file stem).
    hits = real_search_index.query(body)
    hit_uuids = {row["conv_uuid"] for row in hits}
    assert session_uuid in hit_uuids, (
        f"FTS5 query for the freshly-indexed body must hit the new "
        f"session. Got conv_uuids={hit_uuids}, expected to contain "
        f"{session_uuid!r}."
    )


def test_scan_once_skips_drift_when_index_not_ready(
    watcher_env, real_search_index,
):
    """If the index is still building (``is_ready()`` is False), the
    drift pass must NOT run — no rows should land in ``indexed_files``
    for the newly-written JSONL.

    Bug it would surface: drift pass running on a half-built index
    would waste cycles re-indexing files the initial build is about
    to write, AND (worse) could race with the initial build's writes.
    The fixture starts the index in not-ready state (no mark_ready()
    call), so this directly pins the gate.
    """
    from backend import cc_watcher

    # Sanity: fresh SearchIndex is not ready until build_full_index
    # calls mark_ready(). We rely on that default here.
    assert real_search_index.is_ready() is False

    session_uuid = "abcdef02-0000-0000-0000-000000000002"
    body = "needle_skipped_pre_ready_zebra"
    _write_real_cc_session(
        watcher_env["claude_dir"], "proj-A", session_uuid, body,
    )

    cc_watcher.scan_once()

    # The gate held: no file got indexed.
    assert real_search_index.list_indexed_paths() == [], (
        "scan_once() must skip the drift pass while is_ready()=False, "
        "but indexed_files has rows: "
        f"{real_search_index.list_indexed_paths()}"
    )
    # And the body is not queryable.
    assert real_search_index.query(body) == [], (
        "No FTS5 hits should be possible before the initial build "
        "fires mark_ready()."
    )


def test_scan_once_drift_failure_does_not_break_image_pass(
    watcher_env, real_search_index,
):
    """A real drift-pass failure (closed SQLite write-conn → real
    ``sqlite3.ProgrammingError`` inside ``upsert_conversation``) must
    NOT prevent the image-cache pass from completing.

    Negative-space: pin the failure-domain isolation. An error in the
    search-index pass is not allowed to silently break the image
    watcher — which is the load-bearing data-loss prevention path.

    Failure-injection mechanism (no mocks): we close the index's
    write connection. ``_drift_first_scan`` uses the thread-local
    read connection so it correctly identifies the JSONL as drifted;
    ``upsert_conversation`` then attempts a write on the closed
    handle and raises ``sqlite3.ProgrammingError`` — exactly the
    "real I/O error mid-flight" we want to simulate.
    """
    from backend import cc_watcher

    real_search_index.mark_ready()

    # Need a drifted file so the upsert path actually fires (and hits
    # the closed write conn). Without this, _drift_first_scan returns
    # empty and the failure path never executes.
    session_uuid = "abcdef03-0000-0000-0000-000000000003"
    _write_real_cc_session(
        watcher_env["claude_dir"], "proj-A", session_uuid, "noop_body",
    )

    # Real failure injection: close the write connection. The drift
    # pass's stat/diff via the per-thread read conn still succeeds,
    # but the subsequent upsert blows up with sqlite3.ProgrammingError.
    real_search_index._write_conn.close()

    # Image pass must still complete.
    _drop_image(watcher_env["image_cache"], "sess-isolated", "1", TINY_PNG_BYTES)
    handled = cc_watcher.scan_once()
    assert handled == 1, (
        "Image pass must complete despite the drift-pass SQL failure; "
        f"got handled={handled}."
    )
    cached = _cached_files(watcher_env["perm_cache_root"], "sess-isolated", "1")
    assert len(cached) == 1, (
        "Image was not copied into the permanent cache — the drift-pass "
        "failure leaked into the image pass."
    )


def test_scan_once_refreshes_summary_cache_for_drifted_files(
    watcher_env, monkeypatch, tmp_path,
):
    """scan_once() also refreshes the sidebar summary cache for any
    JSONL whose mtime has changed since the last cache stamp.

    Setup: write a CC session JSONL, prime the cache with a stale
    mtime, run scan_once, and verify the cache row now reflects the
    new mtime AND the row count is unchanged (no duplicates).
    """
    import os

    from backend import cc_watcher
    from backend import summary_cache as sc

    # Force a per-test SQLite file so we don't touch ~/.claude-explorer.
    cache_path = tmp_path / "search-index.sqlite"
    monkeypatch.setattr(sc, "default_index_path", lambda: cache_path)
    sc.reset_summary_cache_for_tests()

    # Point discover_jsonl_files at our isolated tree.
    projects = watcher_env["claude_dir"] / "projects" / "test-proj"
    projects.mkdir(parents=True)
    jsonl = projects / "session-1.jsonl"
    jsonl.write_text(
        '{"type":"user","sessionId":"session-1","cwd":"/x","timestamp":"2026-05-16T12:00:00Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"hello"}]}}\n'
        '{"type":"assistant","timestamp":"2026-05-16T12:00:01Z",'
        '"message":{"id":"m1","role":"assistant","model":"x","content":[{"type":"text","text":"world"}]}}\n'
    )

    cache = sc.get_summary_cache()
    assert cache is not None

    # Prime with a STALE row (mtime=0, size=0) so the drift pass
    # treats it as a miss and re-reads.
    cache._write_conn.execute(
        "INSERT OR REPLACE INTO conversation_summaries "
        "(path, mtime, size, summary_json, cached_at) VALUES (?, ?, ?, ?, ?)",
        (str(jsonl), 0.0, 0, b'{"uuid":"stale"}', 0.0),
    )
    cache._write_conn.commit()

    # Run scan_once.
    cc_watcher.scan_once()

    # The row should now be stamped with the real mtime+size and the
    # blob should reflect a re-read (not the "stale" placeholder).
    cur = cache._write_conn.execute(
        "SELECT mtime, size, summary_json FROM conversation_summaries WHERE path = ?",
        (str(jsonl),),
    )
    row = cur.fetchone()
    assert row is not None
    mtime, size, blob = row
    actual_stat = os.stat(jsonl)
    assert float(mtime) == float(actual_stat.st_mtime)
    assert int(size) == int(actual_stat.st_size)
    # Blob is a real summary, not the "stale" placeholder.
    assert b"session-1" in blob

    sc.reset_summary_cache_for_tests()


def test_scan_once_drops_summary_cache_rows_for_missing_files(
    watcher_env, monkeypatch, tmp_path,
):
    """scan_once() removes summary-cache rows for paths that no longer
    exist on disk. Mirrors the FTS5 cleanup pass.
    """
    from backend import cc_watcher
    from backend import summary_cache as sc

    cache_path = tmp_path / "search-index.sqlite"
    monkeypatch.setattr(sc, "default_index_path", lambda: cache_path)
    sc.reset_summary_cache_for_tests()

    cache = sc.get_summary_cache()
    assert cache is not None

    # Row for a file that doesn't exist anywhere on disk.
    ghost = tmp_path / "nonexistent-session.jsonl"
    cache._write_conn.execute(
        "INSERT OR REPLACE INTO conversation_summaries "
        "(path, mtime, size, summary_json, cached_at) VALUES (?, ?, ?, ?, ?)",
        (str(ghost), 12345.0, 100, b'{"uuid":"ghost"}', 0.0),
    )
    cache._write_conn.commit()
    assert cache.stats()["rows"] == 1

    cc_watcher.scan_once()

    assert cache.stats()["rows"] == 0, (
        "scan_once() must drop summary-cache rows whose underlying "
        "JSONL no longer exists, mirroring the FTS5 cleanup pass."
    )
    sc.reset_summary_cache_for_tests()


def test_scan_once_summary_cache_failure_does_not_break_image_pass(
    watcher_env, monkeypatch,
):
    """A summary-cache drift-pass failure must not break the image
    watcher (load-bearing data-loss prevention path).

    Same failure-domain isolation pattern as the search-index drift
    pass test above.
    """
    from backend import cc_watcher

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated summary-cache failure")

    # Force the summary-cache path to throw by making get_summary_cache
    # blow up. The image-cache pass must still complete.
    monkeypatch.setattr(
        "backend.summary_cache.get_summary_cache", _boom,
    )

    _drop_image(watcher_env["image_cache"], "sess-iso2", "1", TINY_PNG_BYTES)
    handled = cc_watcher.scan_once()
    assert handled == 1
    cached = _cached_files(watcher_env["perm_cache_root"], "sess-iso2", "1")
    assert len(cached) == 1


def test_source_rotated_between_scans_does_not_break_watcher(watcher_env):
    """If a source file disappears between the rglob enumeration and
    the read, copy_marker_image_to_cache returns None and the watcher
    keeps going without raising.
    """
    from backend import cc_watcher

    # File exists when scan starts; disappears after the seen-check but
    # before the read. Easiest way to simulate: after the first scan
    # caches it, delete it manually and re-run. The cache copy should
    # survive (proving rotation safety).
    _drop_image(watcher_env["image_cache"], "sess-rot", "1", TINY_PNG_BYTES)
    cc_watcher.scan_once()

    src = watcher_env["image_cache"] / "sess-rot" / "1.png"
    src.unlink()

    # Permanent cache copy still exists and is intact.
    cached = _cached_files(watcher_env["perm_cache_root"], "sess-rot", "1")
    assert len(cached) == 1
    assert cached[0].read_bytes() == TINY_PNG_BYTES

    # Subsequent scan does NOT re-handle the missing path.
    handled = cc_watcher.scan_once()
    assert handled == 0


# ---------------------------------------------------------------------------
# Event-driven path (watchdog migration, 2026-05-15)
# ---------------------------------------------------------------------------
#
# The production code uses ``watchdog.observers.Observer`` which auto-
# selects the OS-native backend (FSEvents/inotify/RDCW). FSEvents in
# particular is hard to test deterministically — events fire on the
# kernel's own schedule, with macOS-specific coalescing latencies of
# a few hundred ms even in the best case. So these tests use
# ``watchdog.observers.polling.PollingObserver`` directly with a tight
# poll interval, which is the same code path the production
# auto-selector falls back to on unsupported filesystems (NFS, etc.)
# and on sandboxed Pythons. Behaviorally identical from the watcher's
# perspective; just deterministic on CI.


def test_handle_one_path_idempotent(watcher_env):
    """Calling handle_one_path twice on the same file does NOT
    re-cache. Pins the per-process _seen guarantee that both
    scan_once and the event handler depend on for dedup.
    """
    from backend import cc_watcher

    _drop_image(watcher_env["image_cache"], "sess-once", "1", TINY_PNG_BYTES)
    src = watcher_env["image_cache"] / "sess-once" / "1.png"

    assert cc_watcher.handle_one_path(src) is True
    assert cc_watcher.handle_one_path(src) is False
    cached = _cached_files(watcher_env["perm_cache_root"], "sess-once", "1")
    assert len(cached) == 1


def test_handle_one_path_skips_non_image(watcher_env):
    """Non-image suffixes are remembered as ignored; not retried."""
    from backend import cc_watcher

    sess_dir = watcher_env["image_cache"] / "sess-skip"
    sess_dir.mkdir(parents=True)
    src = sess_dir / "notes.txt"
    src.write_bytes(b"not an image")

    assert cc_watcher.handle_one_path(src) is False
    # Marked seen so subsequent calls don't re-stat.
    assert src in cc_watcher._seen


def test_handle_one_path_missing_file_returns_false(watcher_env):
    """A path that doesn't exist returns False without raising."""
    from backend import cc_watcher

    src = watcher_env["image_cache"] / "sess-gone" / "1.png"
    assert cc_watcher.handle_one_path(src) is False


def test_event_handler_funnels_create_through_handle_one_path(watcher_env):
    """The watchdog FileSystemEventHandler fires handle_one_path on
    create events. Pinned via direct synthetic event injection so we
    don't depend on a live FSEvents/inotify backend.

    Bug it would surface: an event handler that swallows or
    misroutes events would silently regress the latency win the
    whole watchdog migration is for.
    """
    from backend import cc_watcher

    sess_dir = watcher_env["image_cache"] / "sess-evt"
    sess_dir.mkdir(parents=True)
    src = sess_dir / "1.png"
    src.write_bytes(TINY_PNG_BYTES)

    handler = cc_watcher._build_event_handler()

    class _FakeEvent:
        def __init__(self, src_path: str, is_directory: bool = False) -> None:
            self.src_path = src_path
            self.is_directory = is_directory

    handler.on_created(_FakeEvent(str(src)))
    cached = _cached_files(watcher_env["perm_cache_root"], "sess-evt", "1")
    assert len(cached) == 1
    assert cached[0].read_bytes() == TINY_PNG_BYTES

    # Idempotent: repeat event is a no-op.
    handler.on_created(_FakeEvent(str(src)))
    cached2 = _cached_files(watcher_env["perm_cache_root"], "sess-evt", "1")
    assert len(cached2) == 1


def test_event_handler_ignores_directory_events(watcher_env):
    """Directory-create events (e.g., a new sess-XXX subdir) must
    not be treated as files. A bare on_created on a dir would have
    handle_one_path attempt path.is_file() → False, so this is a
    cheap-but-real correctness pin.
    """
    from backend import cc_watcher

    sess_dir = watcher_env["image_cache"] / "sess-dir"
    sess_dir.mkdir(parents=True)

    handler = cc_watcher._build_event_handler()

    class _FakeDirEvent:
        src_path = str(sess_dir)
        is_directory = True

    handler.on_created(_FakeDirEvent())
    # Dir wasn't added to _seen — it was filtered by is_directory check.
    assert sess_dir not in cc_watcher._seen


def test_run_watcher_with_pollingobserver_captures_event(watcher_env, monkeypatch):
    """End-to-end: drop a file AFTER run_watcher has started its
    Observer; the event-driven path picks it up before the backstop
    poll fires.

    Uses PollingObserver (deterministic) substituted into
    ``_try_start_observer`` so the test doesn't depend on FSEvents
    being available in the test runner's sandbox.
    """
    import asyncio

    from watchdog.observers.polling import PollingObserver

    from backend import cc_watcher

    # Force PollingObserver with a tight 100ms poll so the test
    # finishes in well under the backstop interval.
    def _fake_try_start_observer():
        from watchdog.events import FileSystemEventHandler

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    cc_watcher.handle_one_path(__import__("pathlib").Path(event.src_path))
            def on_modified(self, event):
                if not event.is_directory:
                    cc_watcher.handle_one_path(__import__("pathlib").Path(event.src_path))

        observer = PollingObserver(timeout=0.1)
        root = cc_watcher._live_image_cache_root()
        root.mkdir(parents=True, exist_ok=True)
        observer.schedule(_Handler(), str(root), recursive=True)
        observer.start()
        return observer

    monkeypatch.setattr(
        cc_watcher, "_try_start_observer", _fake_try_start_observer
    )
    # Make backstop interval enormous so we know the win came from
    # events, not from a backstop scan.
    monkeypatch.setattr(cc_watcher, "SCAN_INTERVAL_SEC", 3600.0)

    async def _scenario():
        stop_event = asyncio.Event()
        watcher_task = asyncio.create_task(
            cc_watcher.run_watcher(stop_event)
        )

        # Wait briefly for Observer to come up, then drop a file.
        await asyncio.sleep(0.3)
        _drop_image(
            watcher_env["image_cache"], "sess-live", "1", TINY_PNG_BYTES
        )

        # Give the Observer up to 3 seconds to notice + handle.
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            cached = _cached_files(
                watcher_env["perm_cache_root"], "sess-live", "1"
            )
            if cached:
                break
            await asyncio.sleep(0.1)

        stop_event.set()
        await asyncio.wait_for(watcher_task, timeout=10.0)

        return _cached_files(
            watcher_env["perm_cache_root"], "sess-live", "1"
        )

    cached = asyncio.run(_scenario())
    assert len(cached) == 1, (
        "Event-driven path must capture a file dropped AFTER the "
        "watcher started, well before the backstop poll fires."
    )


def test_run_watcher_falls_back_to_polling_when_observer_unavailable(
    watcher_env, monkeypatch
):
    """If _try_start_observer returns None (e.g., watchdog missing,
    sandboxed Python, NFS mount), run_watcher must still complete
    its initial + backstop scans without raising.

    We force the Observer to "fail to start" and verify the initial
    scan_once still picks up files.
    """
    import asyncio

    from backend import cc_watcher

    monkeypatch.setattr(cc_watcher, "_try_start_observer", lambda: None)
    # Tight backstop so we don't hang the test.
    monkeypatch.setattr(cc_watcher, "SCAN_INTERVAL_SEC", 0.1)

    _drop_image(
        watcher_env["image_cache"], "sess-poll-only", "1", TINY_PNG_BYTES
    )

    async def _scenario():
        stop_event = asyncio.Event()
        watcher_task = asyncio.create_task(
            cc_watcher.run_watcher(stop_event)
        )
        # Initial scan is synchronous-within-task; one tick is plenty.
        await asyncio.sleep(0.05)
        stop_event.set()
        await asyncio.wait_for(watcher_task, timeout=5.0)

    asyncio.run(_scenario())

    cached = _cached_files(
        watcher_env["perm_cache_root"], "sess-poll-only", "1"
    )
    assert len(cached) == 1, (
        "Polling-only fallback path must still process pre-existing "
        "files via the eager initial scan_once."
    )
