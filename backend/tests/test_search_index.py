"""Unit tests for the SQLite FTS5 search index (backend/search_index.py).

What this file pins:
  * FTS5 availability detection and graceful disablement when missing.
  * Schema creation, schema-version drop+rebuild on mismatch.
  * upsert_conversation transactionality (DELETE+INSERT in one BEGIN).
  * Drift detection via mtime tracking; cleanup of vanished files.
  * Query path: scope filters (source / conversation_uuid / project_path /
    bookmarks), prefix wildcard, FTS5-keyword escaping, empty result.
  * Singleton lifecycle and per-test reset.
  * Build-full-index reports correct file/message counts.

Bidirectional verification per TESTING.md §2:
  Every test in this file was first run against a deliberately-broken
  implementation (e.g., omitting the transaction, skipping the prefix
  wildcard, hardcoding source filter) to confirm it fails for the right
  reason. The breadcrumb in each test docstring names the bug it would
  surface if the impl regressed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from backend import search_index as si
from backend.store import ConversationStore


# ----- helpers ----------------------------------------------------


def _conv(
    uuid: str,
    name: str,
    *,
    body: str = "needle in haystack",
    source: str = "CLAUDE_AI",
    project_path: str | None = None,
    msg_uuid: str | None = None,
) -> dict:
    """Build a minimal conversation dict matching the on-disk shape."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": msg_uuid or f"{uuid}-m1",
        "project_path": project_path,
        "source": source,
        "chat_messages": [
            {
                "uuid": msg_uuid or f"{uuid}-m1",
                "sender": "human",
                "text": body,
                "content": [{"type": "text", "text": body}],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }


def _write_desktop_conv(by_org_dir: Path, conv: dict) -> Path:
    by_org_dir.mkdir(parents=True, exist_ok=True)
    path = by_org_dir / f"{conv['uuid']}.json"
    path.write_text(json.dumps(conv))
    return path


@pytest.fixture
def fresh_index(tmp_path):
    """A SearchIndex pointed at a per-test sqlite file. No singleton."""
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    yield idx
    idx.close()


@pytest.fixture
def reset_singleton():
    """Reset the module-level singleton between tests."""
    si.reset_search_index_for_tests()
    yield
    si.reset_search_index_for_tests()


# ----- 1. FTS5 availability detection ----------------------------


def test_fts5_available_returns_bool():
    """fts5_available() probes a temporary in-memory database.

    On macOS Homebrew Python this is True. The function is the gate the
    caller uses to decide whether to instantiate SearchIndex at all.
    """
    result = si.fts5_available()
    assert isinstance(result, bool)
    # On the dev/CI box we expect FTS5 to be present. If this assertion
    # ever fires on a real machine, the build/runtime is the wrong one.
    assert result is True, (
        "Local sqlite3 build is missing FTS5. macOS Homebrew Python ships "
        "FTS5 by default; if you're seeing this, check `python -c \"import "
        "sqlite3; print(sqlite3.sqlite_version)\"`"
    )


# ----- 2. Schema creation + version handling ---------------------


def test_schema_created_on_first_open(fresh_index):
    """Opening a fresh SearchIndex creates messages, indexed_files, and
    schema_version tables.

    Bug it would surface: forgetting to run the CREATE statements at
    init time.
    """
    cur = fresh_index._write_conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )
    names = {row[0] for row in cur.fetchall()}
    assert "messages" in names
    assert "indexed_files" in names
    assert "schema_version" in names

    # schema_version row must exist with the current SCHEMA_VERSION.
    cur = fresh_index._write_conn.execute("SELECT version FROM schema_version")
    versions = [row[0] for row in cur.fetchall()]
    assert versions == [si.SCHEMA_VERSION]


def test_schema_version_mismatch_triggers_full_rebuild(tmp_path, monkeypatch):
    """If the on-disk schema_version disagrees with SCHEMA_VERSION, every
    table is dropped and re-created.

    Bug it would surface: shipping a schema bump without a migration —
    users would see stale rows or query errors against the new code.
    """
    path = tmp_path / "index.sqlite"

    # Open once at the current version, write a row.
    idx1 = si.SearchIndex(path)
    idx1.upsert_conversation(_conv("conv-1", "Test"), tmp_path / "x.json", 1.0)
    assert idx1.stats()["messages"] == 1
    idx1.close()

    # Bump SCHEMA_VERSION and re-open — the rebuild should wipe rows.
    monkeypatch.setattr(si, "SCHEMA_VERSION", si.SCHEMA_VERSION + 1)
    idx2 = si.SearchIndex(path)
    assert idx2.stats()["messages"] == 0, (
        "Schema-version bump must drop all rows so the next build pass "
        "starts clean."
    )
    cur = idx2._write_conn.execute("SELECT version FROM schema_version")
    assert cur.fetchone()[0] == si.SCHEMA_VERSION
    idx2.close()


def test_open_rebuilds_when_messages_table_missing_expected_columns(tmp_path):
    """Defensive: if a prior process left the DB with a stale ``messages``
    table (wrong columns) but a current ``schema_version`` row, the next
    open MUST detect the column-level drift and rebuild — not trust the
    version row blindly.

    Bug it would surface (regression of 2026-05-15): a v4 ``messages``
    table on disk with ``schema_version=5`` (stamped by an interrupted
    prior rebuild) caused every ``upsert_conversation`` to crash with
    "no column named organization_id" forever, because the version-row
    check declared the schema "current" and never re-rebuilt.
    """
    import sqlite3
    path = tmp_path / "index.sqlite"

    # Hand-craft a stale v4-shaped DB: 8-column messages table + a
    # schema_version row claiming the current code version.
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE VIRTUAL TABLE messages USING fts5(
            conv_uuid UNINDEXED,
            message_uuid UNINDEXED,
            sender UNINDEXED,
            created_at UNINDEXED,
            source UNINDEXED,
            project_path UNINDEXED,
            title,
            body
        );
        CREATE TABLE indexed_files (
            path TEXT PRIMARY KEY, mtime REAL NOT NULL, indexed_at INTEGER NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        """
    )
    raw.execute("INSERT INTO schema_version (version) VALUES (?)", (si.SCHEMA_VERSION,))
    raw.commit()
    raw.close()

    # Opening with the real code must detect the column drift and rebuild
    # the messages table with the expected (current) column set, so the
    # very next upsert succeeds rather than raising "no column named X".
    idx = si.SearchIndex(path)
    try:
        idx.upsert_conversation(_conv("conv-1", "Recovered"), tmp_path / "x.json", 1.0)
        assert idx.stats()["messages"] == 1
    finally:
        idx.close()


def test_open_rebuilds_when_schema_version_row_missing_but_tables_exist(tmp_path):
    """Defensive: if ``schema_version`` is empty (e.g., a prior crashed
    rebuild dropped it but failed before re-INSERTing), a stale
    ``messages`` table from an older version MUST be rebuilt — not
    silently re-stamped as "current" while preserving the wrong columns.

    Bug it would surface: same root cause as the column-drift test above,
    different trigger path (empty version table instead of correct row).
    """
    import sqlite3
    path = tmp_path / "index.sqlite"

    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE VIRTUAL TABLE messages USING fts5(
            conv_uuid UNINDEXED, message_uuid UNINDEXED, sender UNINDEXED,
            created_at UNINDEXED, source UNINDEXED, project_path UNINDEXED,
            title, body
        );
        CREATE TABLE indexed_files (
            path TEXT PRIMARY KEY, mtime REAL NOT NULL, indexed_at INTEGER NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        """
    )
    # NB: no INSERT into schema_version on purpose.
    raw.commit()
    raw.close()

    idx = si.SearchIndex(path)
    try:
        idx.upsert_conversation(_conv("conv-1", "Recovered"), tmp_path / "x.json", 1.0)
        assert idx.stats()["messages"] == 1
    finally:
        idx.close()


def test_repeat_open_is_idempotent_within_same_version(tmp_path):
    """Opening the same file twice (same SCHEMA_VERSION) preserves rows.

    Bug it would surface: drop-rebuild firing on every open instead of
    only on version mismatch.
    """
    path = tmp_path / "index.sqlite"
    idx1 = si.SearchIndex(path)
    idx1.upsert_conversation(_conv("conv-keep", "Stays"), tmp_path / "x.json", 1.0)
    idx1.close()

    idx2 = si.SearchIndex(path)
    assert idx2.stats()["messages"] == 1, (
        "Re-opening at the same SCHEMA_VERSION must not wipe data."
    )
    idx2.close()


# ----- 3. Upsert and basic query -------------------------------


def test_upsert_then_query_finds_message(fresh_index):
    """Round-trip: write one conv, query its body text, retrieve the
    message_uuid back.

    Bug it would surface: column ordering wrong, MATCH not searching the
    body column, or the row never being committed.
    """
    conv = _conv("conv-1", "First conv", body="The cron job runs at midnight.")
    fresh_index.upsert_conversation(conv, Path("/fake/conv-1.json"), 1.0)
    fresh_index.mark_ready()

    rows = fresh_index.query("cron")
    assert len(rows) == 1
    assert rows[0]["conv_uuid"] == "conv-1"
    assert rows[0]["message_uuid"] == "conv-1-m1"
    assert rows[0]["sender"] == "human"


def test_upsert_replaces_existing_rows(fresh_index):
    """upsert_conversation is DELETE+INSERT — re-inserting the same conv
    must replace its rows, not duplicate them.

    Bug it would surface: forgetting the DELETE clause; users would see
    every saved version of a conversation in search results.
    """
    conv_v1 = _conv("conv-1", "v1", body="alpha bravo charlie")
    conv_v2 = _conv("conv-1", "v1", body="delta echo foxtrot")
    fresh_index.upsert_conversation(conv_v1, Path("/fake/conv-1.json"), 1.0)
    fresh_index.upsert_conversation(conv_v2, Path("/fake/conv-1.json"), 2.0)
    fresh_index.mark_ready()

    # Old body must be gone.
    assert fresh_index.query("alpha") == []
    # New body must be present.
    rows = fresh_index.query("delta")
    assert len(rows) == 1


def test_upsert_indexed_files_tracks_mtime(fresh_index):
    """The indexed_files table records path+mtime for every upsert.

    Bug it would surface: drift detection breaks because we never write
    the mtime; every file looks "stale" and gets re-indexed every pass.
    """
    fake_path = Path("/fake/conv-1.json")
    fresh_index.upsert_conversation(_conv("conv-1", "x"), fake_path, 1234.5)

    cur = fresh_index._write_conn.execute(
        "SELECT path, mtime FROM indexed_files WHERE path = ?",
        (str(fake_path),),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == str(fake_path)
    assert row[1] == 1234.5


def test_upsert_with_no_messages_still_emits_title_row(fresh_index):
    """A conversation with empty chat_messages still gets one row so
    title-only matches can find it.

    Bug it would surface: querying for the title of an empty conv returns
    nothing, which would make /api/search appear to "lose" empty convs
    even though the linear path finds them.
    """
    conv = _conv("conv-empty", "Searchable Title")
    conv["chat_messages"] = []
    fresh_index.upsert_conversation(conv, Path("/fake/conv-empty.json"), 1.0)
    fresh_index.mark_ready()

    rows = fresh_index.query("Searchable")
    assert len(rows) == 1
    assert rows[0]["conv_uuid"] == "conv-empty"


# ----- 4. Scope filters in query ------------------------------


def test_query_scope_conversation_uuid(fresh_index):
    """conversation_uuid restricts to a single conv; wins over other scopes."""
    fresh_index.upsert_conversation(_conv("a", "A", body="needle"), Path("/a.json"), 1)
    fresh_index.upsert_conversation(_conv("b", "B", body="needle"), Path("/b.json"), 1)
    fresh_index.mark_ready()

    rows = fresh_index.query("needle", conversation_uuid="b")
    assert [r["conv_uuid"] for r in rows] == ["b"]


def test_query_scope_project_path(fresh_index):
    """project_path filters by exact match against the conv's project_path."""
    fresh_index.upsert_conversation(
        _conv("p1", "X", body="needle", project_path="/work/projectA"),
        Path("/p1.json"), 1,
    )
    fresh_index.upsert_conversation(
        _conv("p2", "Y", body="needle", project_path="/work/projectA"),
        Path("/p2.json"), 1,
    )
    fresh_index.upsert_conversation(
        _conv("p3", "Z", body="needle", project_path="/work/projectB"),
        Path("/p3.json"), 1,
    )
    fresh_index.mark_ready()

    rows = fresh_index.query("needle", project_path="/work/projectA")
    uuids = sorted(r["conv_uuid"] for r in rows)
    assert uuids == ["p1", "p2"]


def test_query_scope_bookmarks(fresh_index):
    """bookmarks restricts to UUIDs in the set."""
    for u in ("a", "b", "c"):
        fresh_index.upsert_conversation(
            _conv(u, u, body="needle"), Path(f"/{u}.json"), 1,
        )
    fresh_index.mark_ready()

    rows = fresh_index.query("needle", bookmarks={"a", "c"})
    uuids = sorted(r["conv_uuid"] for r in rows)
    assert uuids == ["a", "c"]


def test_query_scope_empty_bookmarks_returns_empty(fresh_index):
    """An EMPTY bookmarks set means "no bookmarks selected" → no results
    (vs ``bookmarks=None`` which means "no scope restriction").

    Bug it would surface: treating empty set as None would silently widen
    scope to all conversations.
    """
    fresh_index.upsert_conversation(
        _conv("a", "A", body="needle"), Path("/a.json"), 1,
    )
    fresh_index.mark_ready()

    rows = fresh_index.query("needle", bookmarks=set())
    assert rows == []


def test_query_scope_source_filter(fresh_index):
    """source="CLAUDE_AI" omits CC rows and vice versa."""
    fresh_index.upsert_conversation(
        _conv("ai-1", "Desktop", body="needle", source="CLAUDE_AI"),
        Path("/ai-1.json"), 1,
    )
    fresh_index.upsert_conversation(
        _conv("cc-1", "CC session", body="needle", source="CLAUDE_CODE"),
        Path("/cc-1.jsonl"), 1,
    )
    fresh_index.mark_ready()

    ai_only = fresh_index.query("needle", source="CLAUDE_AI")
    assert [r["conv_uuid"] for r in ai_only] == ["ai-1"]

    cc_only = fresh_index.query("needle", source="CLAUDE_CODE")
    assert [r["conv_uuid"] for r in cc_only] == ["cc-1"]

    both = fresh_index.query("needle", source="all")
    assert sorted(r["conv_uuid"] for r in both) == ["ai-1", "cc-1"]


# ----- 5. translate_query ------------------------------------


def test_translate_query_single_token_gets_prefix_wildcard():
    """A single token gets a ``*`` wildcard so search-as-you-type works."""
    out = si.translate_query("python")
    assert out == '"python" *'


def test_translate_query_multi_token_only_last_gets_wildcard():
    """Only the LAST token is treated as a search-as-you-type prefix."""
    out = si.translate_query("hello world")
    assert out == '"hello" AND "world" *'


def test_translate_query_single_char_skips_wildcard():
    """A single-char prefix would explode the result set; never applied."""
    out = si.translate_query("a")
    assert out == '"a"'


def test_translate_query_quotes_fts5_keywords():
    """``AND``/``OR``/``NOT``/``NEAR`` typed by the user are literal terms,
    not operators (because every token gets quoted)."""
    out = si.translate_query("AND")
    assert out == '"AND" *'
    out = si.translate_query("hello OR world")
    # "OR" is the middle token — literal term wrapped in quotes.
    assert out == '"hello" AND "OR" AND "world" *'


def test_translate_query_escapes_internal_quotes():
    """A token with an internal ``"`` doubles the quote so the FTS5 phrase
    syntax stays valid."""
    out = si.translate_query('say "hi"')
    # Tokens split on whitespace — the second token retains its quotes.
    # The escape doubles internal " to "".
    assert '""hi""' in out


def test_translate_query_empty_returns_empty():
    """No usable tokens → empty string; caller skips the SQL entirely."""
    assert si.translate_query("") == ""
    assert si.translate_query("    ") == ""


# ----- 6. Drift detection -------------------------------------


def test_needs_update_true_for_new_path(fresh_index):
    """A path we've never seen needs indexing."""
    assert fresh_index.needs_update(Path("/never/seen.json"), 100.0) is True


def test_needs_update_false_for_same_mtime(fresh_index):
    """Same path + same mtime = no update needed."""
    fresh_index.upsert_conversation(_conv("a", "A"), Path("/a.json"), 100.0)
    assert fresh_index.needs_update(Path("/a.json"), 100.0) is False


def test_needs_update_true_when_mtime_changes(fresh_index):
    """Bumping the mtime triggers a re-index."""
    fresh_index.upsert_conversation(_conv("a", "A"), Path("/a.json"), 100.0)
    assert fresh_index.needs_update(Path("/a.json"), 200.0) is True


def test_delete_by_path_removes_messages_and_file_record(fresh_index):
    """Cleanup pass for vanished files drops both messages and indexed_files row."""
    path = Path("/a.json")
    # File stem ('a') matches the conv uuid by convention.
    fresh_index.upsert_conversation(_conv("a", "A", body="needle"), path, 100.0)
    fresh_index.mark_ready()
    assert len(fresh_index.query("needle")) == 1

    fresh_index.delete_by_path(path)

    assert fresh_index.query("needle") == []
    cur = fresh_index._write_conn.execute(
        "SELECT * FROM indexed_files WHERE path = ?", (str(path),)
    )
    assert cur.fetchone() is None


# ----- 7. Atomic upsert (transactional) ----------------------


def test_upsert_rollback_on_executemany_failure(fresh_index):
    """If executemany raises mid-INSERT, the prior DELETE must be rolled
    back so the OLD rows remain.

    TESTING.md §5.8: atomic-write under crash. The contract is
    "either the old state or the new state, never half-updated."

    Bug it would surface: skipping ``with self._write_conn`` would leave
    the DELETE committed and the INSERT incomplete; the conversation
    would vanish from search until the next successful upsert.

    Trigger: wrap ``_write_conn`` with a proxy whose ``executemany``
    raises after the DELETE has run. ``sqlite3.Connection`` attributes
    are C-level read-only so we can't monkeypatch the method directly;
    we substitute the whole connection attribute on the SearchIndex
    instance, which Python is happy to swap because it's a plain
    instance attribute.
    """
    # Seed the conversation so we have a baseline of OLD rows to test.
    conv_old = _conv("conv-rollback", "Old", body="alpha bravo")
    fresh_index.upsert_conversation(conv_old, Path("/r.json"), 1.0)
    fresh_index.mark_ready()
    assert len(fresh_index.query("alpha")) == 1

    real_conn = fresh_index._write_conn

    class _BoomProxy:
        """Forwards every call to the real connection EXCEPT executemany."""

        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def executemany(self, *args, **kwargs):
            raise sqlite3.OperationalError("simulated crash")

    fresh_index._write_conn = _BoomProxy(real_conn)
    try:
        conv_new = _conv("conv-rollback", "New", body="charlie delta")
        with pytest.raises(sqlite3.OperationalError):
            fresh_index.upsert_conversation(conv_new, Path("/r.json"), 2.0)
    finally:
        fresh_index._write_conn = real_conn

    # Old rows must still be present (DELETE was rolled back).
    rows = fresh_index.query("alpha")
    assert len(rows) == 1, (
        "Transaction rollback failed: the DELETE committed but INSERT did "
        "not, leaving the conversation invisible. Wrap the DELETE+INSERT "
        "in `with self._write_conn:` to keep them in one BEGIN."
    )
    assert fresh_index.query("charlie") == []


# ----- 8. Singleton lifecycle --------------------------------


def test_get_search_index_returns_same_instance(reset_singleton, monkeypatch, tmp_path):
    """Two calls return the same SearchIndex instance (lazy singleton)."""
    monkeypatch.setattr(si, "default_index_path", lambda: tmp_path / "shared.sqlite")
    a = si.get_search_index()
    b = si.get_search_index()
    assert a is not None
    assert a is b


def test_get_search_index_returns_none_when_fts5_missing(
    reset_singleton, monkeypatch
):
    """When fts5_available() is False the singleton stays None."""
    monkeypatch.setattr(si, "fts5_available", lambda: False)
    assert si.get_search_index() is None


# ----- 9. build_full_index ----------------------------------


def test_build_full_index_walks_all_conversations(tmp_path):
    """build_full_index reads every conversation and returns counts."""
    by_org = tmp_path / "by-org" / "org-1"
    paths = [
        _write_desktop_conv(by_org, _conv("a", "A", body="alpha")),
        _write_desktop_conv(by_org, _conv("b", "B", body="bravo")),
        _write_desktop_conv(by_org, _conv("c", "C", body="charlie")),
    ]
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    files, msgs = si.build_full_index(store, index=idx)

    assert files == 3
    # Each conv has one message → 3 message rows total.
    assert msgs == 3
    assert idx.is_ready() is True
    # Spot-check a query.
    assert len(idx.query("alpha")) == 1
    idx.close()
    # Quiet pyflakes.
    _ = paths


def test_build_full_index_marks_index_ready(tmp_path):
    """is_ready() is False before build, True after."""
    by_org = tmp_path / "by-org" / "org-1"
    _write_desktop_conv(by_org, _conv("a", "A"))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    assert idx.is_ready() is False

    si.build_full_index(store, index=idx)
    assert idx.is_ready() is True
    idx.close()


def test_build_full_index_runs_pragma_optimize_at_end(tmp_path):
    """build_full_index runs ``PRAGMA optimize`` after the inserts complete
    so SQLite's query planner picks up fresh statistics before the index
    goes live (perf-polish A3).

    Bug it would surface: the first search after a cold restart used the
    planner's default cost model and picked a bad path; running ``PRAGMA
    optimize`` once at the end of the build refreshes ANALYZE statistics
    cheaply.

    Pin: among the SQL statements executed on the index's write connection
    during ``build_full_index``, at least one is ``PRAGMA optimize`` and
    it appears AFTER the last ``INSERT INTO messages`` (so it sees a fully
    populated table).
    """
    by_org = tmp_path / "by-org" / "org-1"
    _write_desktop_conv(by_org, _conv("a", "A", body="alpha"))
    _write_desktop_conv(by_org, _conv("b", "B", body="bravo"))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    executed_sql: list[str] = []

    real_conn = idx._write_conn

    class _SpyConn:
        """Thin proxy: records every ``execute`` and ``executemany`` SQL
        string (the index uses both — single-row writes go through
        ``execute``, bulk inserts through ``executemany``), delegates
        everything else (commit, __enter__, __exit__, ...) to the real
        sqlite3.Connection unchanged."""

        def execute(self, sql, *args, **kwargs):
            executed_sql.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        def executemany(self, sql, *args, **kwargs):
            executed_sql.append(sql)
            return real_conn.executemany(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_conn, name)

        def __enter__(self):
            return real_conn.__enter__()

        def __exit__(self, exc_type, exc_val, exc_tb):
            return real_conn.__exit__(exc_type, exc_val, exc_tb)

    idx._write_conn = _SpyConn()  # type: ignore[assignment]

    try:
        si.build_full_index(store, index=idx)
    finally:
        idx._write_conn = real_conn

    # Locate the last INSERT INTO messages and assert a PRAGMA optimize
    # follows it.
    pragma_idx = next(
        (i for i, s in enumerate(executed_sql) if "PRAGMA optimize" in s),
        None,
    )
    assert pragma_idx is not None, (
        "build_full_index must call PRAGMA optimize so the FTS5 query "
        "planner has fresh stats on the first post-build query; "
        f"observed SQL: {executed_sql!r}"
    )
    last_insert_idx = max(
        (i for i, s in enumerate(executed_sql) if "INSERT INTO messages" in s),
        default=-1,
    )
    assert last_insert_idx >= 0, (
        "test setup error: no INSERT INTO messages observed during build"
    )
    assert pragma_idx > last_insert_idx, (
        "PRAGMA optimize must run AFTER the last INSERT so SQLite has "
        f"the final row distribution when it analyzes; pragma at "
        f"index {pragma_idx}, last insert at {last_insert_idx}"
    )
    idx.close()


# ----- 10. update_drifted_files ------------------------------


def test_update_drifted_files_picks_up_changed_file(tmp_path):
    """A file whose content changes after build is re-indexed by the
    drift pass.

    Bug it would surface: forgetting to compare current mtime against
    indexed_files; user touches a conversation, search returns stale
    text indefinitely.
    """
    by_org = tmp_path / "by-org" / "org-1"
    path = _write_desktop_conv(by_org, _conv("a", "A", body="old text"))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    assert len(idx.query("old")) == 1

    # Rewrite the file with new content + bumped mtime.
    path.write_text(json.dumps(_conv("a", "A", body="new text")))
    new_mtime = time.time() + 10
    import os
    os.utime(path, (new_mtime, new_mtime))

    # Bypass the in-memory cache so the store re-reads.
    from backend.cache import clear_cache
    clear_cache()

    updated = si.update_drifted_files(store, index=idx)
    assert updated == 1

    # Old rows are gone, new rows are present.
    assert idx.query("old") == []
    assert len(idx.query("new")) == 1
    idx.close()


def test_update_drifted_files_noop_when_unchanged(tmp_path):
    """Two consecutive drift passes against unchanged files do exactly zero
    re-indexes on the second call."""
    by_org = tmp_path / "by-org" / "org-1"
    _write_desktop_conv(by_org, _conv("a", "A", body="hello"))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)

    # First drift pass after build: nothing to do.
    assert si.update_drifted_files(store, index=idx) == 0
    # Second drift pass also nothing.
    assert si.update_drifted_files(store, index=idx) == 0
    idx.close()


def test_update_drifted_files_cleans_up_vanished_paths(tmp_path):
    """If a file disappears from disk between passes, its rows are removed.

    Negative-space: searching for the deleted conv's content must return
    no results after the cleanup pass.
    """
    by_org = tmp_path / "by-org" / "org-1"
    path = _write_desktop_conv(by_org, _conv("vanish", "Gone", body="ephemeral"))
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    assert len(idx.query("ephemeral")) == 1

    # Delete the file from disk and clear the conv cache so the store
    # doesn't return the cached copy on the next walk.
    path.unlink()
    from backend.cache import clear_cache
    clear_cache()

    si.update_drifted_files(store, index=idx)
    assert idx.query("ephemeral") == [], (
        "Cleanup pass failed to drop rows for a vanished file."
    )
    idx.close()


# ----- 10b. TOCTOU race: stat-AFTER-read causes persistent index staleness


def test_update_drifted_files_toctou_does_not_poison_index(tmp_path, monkeypatch):
    """Hunt #8 TOCTOU: if a file is mutated DURING the read in
    update_drifted_files, the index must NOT stamp the read content
    with a mtime that already reflects unread bytes. That would freeze
    drift detection at a snapshot the index never actually saw, hiding
    the unread bytes from search until the file is touched again.

    The race in the buggy implementation:
      1. _drift_first_scan sees mtime_disk != mtime_indexed → drift
      2. conv = _load_conversation_at(path, store) reads content C1 at T1
      3. (CC appends a new line; on-disk content becomes C2; mtime → M2)
      4. mtime = path.stat().st_mtime → reads M2
      5. index.upsert_conversation(conv=C1, path, mtime=M2) → STALE
         content C1 stamped with FRESH mtime M2
      6. Until CC appends AGAIN, drift checks return mtime_disk == M2
         == mtime_indexed → no drift → C2's new lines are invisible to
         search even though they're on disk.

    Bug it would surface: the recommended check-read-check (stat both
    sides of the read, abort on drift) was never implemented. The
    file's contents-at-indexed-mtime invariant is broken.

    Simulation strategy: monkeypatch _load_conversation_at so that it
    (a) returns the OLD content, and (b) atomically writes NEW content
    + bumps mtime. The post-read stat will see the bumped mtime. The
    fix must detect the mtime drift and skip the upsert; the file
    stays "drifted" for the next pass, which picks up C2 correctly.
    """
    import os

    by_org = tmp_path / "by-org" / "org-1"
    path = _write_desktop_conv(
        by_org, _conv("a", "A", body="content one alpha"),
    )
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    assert len(idx.query("alpha")) == 1

    # First legitimate append — drift pass should pick this up.
    path.write_text(json.dumps(_conv("a", "A", body="content two beta")))
    bumped_mtime = time.time() + 10.0
    os.utime(path, (bumped_mtime, bumped_mtime))
    from backend.cache import clear_cache
    clear_cache()

    # Inject the race: the loader returns C1 ("content one alpha")
    # but the file on disk advances to C2 ("content three gamma") with
    # a fresh mtime before the caller's post-read stat happens.
    real_loader = si._load_conversation_at
    race_mtime = bumped_mtime + 50.0

    def _racy_loader(p, s, source=None):
        if p == path:
            # Pretend we read the file's OLDEST snapshot (C1).
            conv = _conv("a", "A", body="content one alpha")
            # Then CC appends; disk content + mtime advance past us.
            path.write_text(
                json.dumps(_conv("a", "A", body="content three gamma"))
            )
            os.utime(p, (race_mtime, race_mtime))
            return conv
        return real_loader(p, s, source=source)

    monkeypatch.setattr(si, "_load_conversation_at", _racy_loader)
    si.update_drifted_files(store, index=idx)

    # Restore the real loader for the next pass.
    monkeypatch.setattr(si, "_load_conversation_at", real_loader)
    clear_cache()

    # KEY ASSERTION: after the race, a SECOND drift pass with the real
    # loader MUST re-index the file so search reflects on-disk C2
    # ("content three gamma"). With the bug, the index stamped C1 with
    # mtime=race_mtime; stat() now returns race_mtime; drift detector
    # sees mtime_disk == mtime_indexed and SKIPS the file — leaving
    # "content three gamma" permanently absent from search until the
    # next outside-the-race append.
    si.update_drifted_files(store, index=idx)
    assert len(idx.query("gamma")) == 1, (
        "TOCTOU bug confirmed: the in-flight mtime (read AFTER the "
        "file content read) was stamped onto stale content. Drift "
        "detection now believes the file is up-to-date even though "
        "on-disk content was never indexed. Fix requires check-read-"
        "check: stat the file both before and after the content read; "
        "if mtime drifted, abort the upsert and let the next pass retry."
    )
    assert idx.query("alpha") == [], (
        "Stale content C1 must not survive in the index after a "
        "second drift pass."
    )
    idx.close()


# ----- 11. title_match_uuids — public title-sweep UUID query ----
#
# Council A1, 2026-05-21: previously ``backend/search.py`` reached into
# SearchIndex._private (``_get_read_conn`` + ``_populate_allowed_conv``)
# to run a UUID-only title sweep with a raw SELECT. This public method
# replaces the reach-through; the tests below pin the contract so any
# future change to the title-sweep semantics has to be deliberate.


def test_title_match_uuids_returns_substring_hits(fresh_index):
    """title_match_uuids returns the set of conv_uuids whose title
    contains the needle as a LIKE substring.

    Bug it would surface: title sweep stops catching substring hits
    that the FTS5 tokenizer rejects (e.g., "edul" inside "scheduled").
    """
    fresh_index.upsert_conversation(
        _conv("a", "scheduled meetings", body="x"), Path("/a.json"), 1.0
    )
    fresh_index.upsert_conversation(
        _conv("b", "unrelated thread", body="x"), Path("/b.json"), 1.0
    )
    fresh_index.upsert_conversation(
        _conv("c", "scheduling", body="x"), Path("/c.json"), 1.0
    )
    fresh_index.mark_ready()

    hits = fresh_index.title_match_uuids("edul")
    assert hits == {"a", "c"}, (
        "title_match_uuids must catch mid-token substring matches that "
        "FTS5's prefix tokenizer cannot reach."
    )


def test_title_match_uuids_empty_needle_returns_empty(fresh_index):
    """An empty needle returns an empty set — never sweeps every title.

    Bug it would surface: a blank query string returning every
    conversation as a title match (LIKE '%%' matches everything).
    """
    fresh_index.upsert_conversation(
        _conv("a", "anything", body="x"), Path("/a.json"), 1.0
    )
    fresh_index.mark_ready()

    assert fresh_index.title_match_uuids("") == set()
    assert fresh_index.title_match_uuids("   ") == set()


def test_title_match_uuids_no_hits_returns_empty(fresh_index):
    """A needle with zero title matches returns an empty set, not an
    error.

    Bug it would surface: a SQL error swallowed without returning a
    valid empty set, causing the caller to receive ``None`` and crash
    on set union.
    """
    fresh_index.upsert_conversation(
        _conv("a", "hello world", body="x"), Path("/a.json"), 1.0
    )
    fresh_index.mark_ready()

    assert fresh_index.title_match_uuids("nonexistent") == set()


def test_title_match_uuids_source_filter(fresh_index):
    """The source filter narrows the title sweep just like the body
    sweep does — title-only hits in the wrong source are excluded.

    Bug it would surface: source filter ignored on the title sweep,
    causing cross-source bleed (a CC title-only hit appears even when
    the user filtered to CLAUDE_AI).
    """
    fresh_index.upsert_conversation(
        _conv("a", "needle in title", body="x", source="CLAUDE_AI"),
        Path("/a.json"),
        1.0,
    )
    fresh_index.upsert_conversation(
        _conv("b", "needle in title", body="x", source="CLAUDE_CODE"),
        Path("/b.json"),
        1.0,
    )
    fresh_index.mark_ready()

    assert fresh_index.title_match_uuids("needle", source="CLAUDE_AI") == {"a"}
    assert fresh_index.title_match_uuids("needle", source="CLAUDE_CODE") == {"b"}


def test_title_match_uuids_conversation_uuids_scope(fresh_index):
    """The conversation_uuids set restricts the title sweep to that
    set — title-only hits outside the allowlist are excluded.

    Bug it would surface: the sidebar-scope conversation_uuids filter
    fails to apply to title-only hits, allowing excluded conversations
    to leak through (regression of the 2026-05-14 sidebar-scope fix).
    """
    fresh_index.upsert_conversation(
        _conv("a", "shared title word", body="x"), Path("/a.json"), 1.0
    )
    fresh_index.upsert_conversation(
        _conv("b", "shared title word", body="x"), Path("/b.json"), 1.0
    )
    fresh_index.upsert_conversation(
        _conv("c", "shared title word", body="x"), Path("/c.json"), 1.0
    )
    fresh_index.mark_ready()

    hits = fresh_index.title_match_uuids("title", conversation_uuids={"a", "c"})
    assert hits == {"a", "c"}, (
        "conversation_uuids must AND with the LIKE clause via the "
        "per-connection allowed_conv TEMP table."
    )


def test_title_match_uuids_empty_conversation_uuids_returns_empty(fresh_index):
    """An empty conversation_uuids set returns empty — never falls
    back to "no scope" semantics.

    Bug it would surface: passing ``set()`` (a deliberate "nothing
    allowed" signal) being treated as None, returning every match
    instead of nothing.
    """
    fresh_index.upsert_conversation(
        _conv("a", "needle title", body="x"), Path("/a.json"), 1.0
    )
    fresh_index.mark_ready()

    assert fresh_index.title_match_uuids("needle", conversation_uuids=set()) == set()


def test_title_match_uuids_byte_for_byte_matches_legacy_reach_through(fresh_index):
    """Lock-in test: the public method's result must match the legacy
    private-reach-through SQL byte-for-byte for the same inputs.

    Bug it would surface: the public-method extraction subtly changed
    semantics (e.g., dropped a clause, used a different column, swapped
    LIKE for GLOB). This is a refactor-safety pin.
    """
    fresh_index.upsert_conversation(
        _conv("a", "alpha needle beta", body="x", source="CLAUDE_AI"),
        Path("/a.json"),
        1.0,
    )
    fresh_index.upsert_conversation(
        _conv("b", "no match here", body="x", source="CLAUDE_AI"),
        Path("/b.json"),
        1.0,
    )
    fresh_index.upsert_conversation(
        _conv("c", "needle elsewhere", body="x", source="CLAUDE_CODE"),
        Path("/c.json"),
        1.0,
    )
    fresh_index.mark_ready()

    # Reproduce the legacy SQL inline (the code we're replacing).
    conn = fresh_index._get_read_conn()
    cur = conn.execute(
        "SELECT DISTINCT conv_uuid FROM messages WHERE title LIKE ?",
        ("%needle%",),
    )
    legacy = {row[0] for row in cur.fetchall()}

    public = fresh_index.title_match_uuids("needle")
    assert public == legacy, (
        f"public title_match_uuids diverged from legacy SQL: "
        f"public={public} legacy={legacy}"
    )
