"""RED tests for the 2026-05-25 cowork search bug.

Bug: full-text search returns no matches for content inside Claude Cowork
conversations. The user's live SQLite index has Cowork paths recorded in
``indexed_files`` but ZERO rows in the FTS5 ``messages`` table with
``source='CLAUDE_COWORK'``. The viewer renders the conversation correctly
(so the reader works), the integration test passes (so the build path
works on a clean DB) — the corruption is a steady-state inconsistency
only visible against the live index.

Root causes pinned by this slab:

  * **Bug 1 (delete_by_path stem-mismatch)**: ``SearchIndex.delete_by_path``
    derives the conv_uuid via ``file_path.stem``. For CC files
    (``<uuid>.jsonl``) the stem IS the uuid; for Cowork
    (``local_<uuid>/audit.jsonl``) the stem is always the literal string
    ``"audit"``. The DELETE-by-conv_uuid for messages and conversations
    therefore matches NOTHING on Cowork — orphan rows survive any
    cleanup pass that targets a Cowork path. (And conversely, the
    indexed_files row IS deleted by path — so the next drift pass sees
    the path as "new" and the orphans accumulate to two copies under
    different stamping epochs, or are joined by NEW rows. The live
    state suggests a different sequence: indexed_files stamped but
    messages absent — see Bug 2.)

  * **Bug 2 (no SCHEMA_VERSION bump for Cowork)**: Cowork support
    shipped without bumping SCHEMA_VERSION from 11. Users with an
    existing v11 index (built before Cowork landed) do not get a
    forced rebuild. The drift-pass-only path leaves the live state
    in whatever inconsistency the incremental writes produced — in
    the user's case, indexed_files claims 42 cowork paths are
    indexed but the FTS5 messages table has none.

  * **Bug 3 (clear_all leaks conversations rows)**: ``clear_all``
    (called by ``claude-explorer reindex-search --full``) truncates
    messages + indexed_files but NOT the v10 conversations
    projection. A subsequent build re-populates conversations via
    INSERT OR REPLACE on each upsert, so this is partly self-
    healing, but a re-build that skips files (e.g. cowork_root
    transiently missing) leaves orphan projection rows that the
    title sweep will still surface.

Tests below pin each bug independently. Each is RED-first per the
project's TDD discipline.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.search_index import (
    SCHEMA_VERSION,
    SearchIndex,
    build_full_index,
    update_drifted_files,
)
from backend.store import ConversationStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cowork"
HAPPY_DEPLOYMENT = FIXTURE_ROOT / "d_deployment1"
HAPPY_ORG = HAPPY_DEPLOYMENT / "o_org1"
HAPPY_SESSION_DIR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777"
HAPPY_SIDECAR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777.json"
COWORK_CONV_UUID = "aaaa1111-2222-3333-4444-555566667777"


def _make_isolated_cowork_root(tmp_path: Path) -> Path:
    cowork_root = tmp_path / "claude_desktop_app" / "local-agent-mode-sessions"
    dep = cowork_root / "d_test"
    org = dep / "o_test"
    sess = org / f"local_{COWORK_CONV_UUID}"
    sess.mkdir(parents=True)
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", sess / "audit.jsonl")
    shutil.copy(HAPPY_SIDECAR, org / f"local_{COWORK_CONV_UUID}.json")
    return cowork_root


@pytest.fixture
def populated_index(tmp_path: Path) -> tuple[SearchIndex, ConversationStore, Path]:
    """Build a fresh index over the cowork fixture; return (index, store, audit_path)."""
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root = _make_isolated_cowork_root(tmp_path)
    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    db_path = tmp_path / "search-index.sqlite"
    index = SearchIndex(db_path)
    build_full_index(store, index=index)

    audit_path = (
        cowork_root / "d_test" / "o_test"
        / f"local_{COWORK_CONV_UUID}" / "audit.jsonl"
    )
    return index, store, audit_path


# ---------------------------------------------------------------------------
# Bug 1: delete_by_path stem-mismatch for cowork audit.jsonl paths
# ---------------------------------------------------------------------------


def test_delete_by_path_purges_cowork_messages(populated_index):
    """``delete_by_path`` MUST drop the FTS5 message rows for the conv whose
    audit.jsonl is the argument, NOT just the indexed_files row.

    Pre-fix this test fails because ``delete_by_path`` derives the
    conv_uuid from ``file_path.stem`` — for a Cowork path the stem is
    always ``"audit"``, so DELETE WHERE conv_uuid='audit' is a no-op for
    messages AND conversations. The indexed_files row IS dropped (it's
    keyed by full path), so the index ends up in a paradoxical state:
    no record of having indexed the file, but its rows still pollute
    the inverted index and the title-sweep projection.
    """
    index, _, audit_path = populated_index

    # Sanity: build_full_index put cowork rows in BOTH messages AND conversations.
    conn = index._get_read_conn()
    pre_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conv_uuid = ?", (COWORK_CONV_UUID,)
    ).fetchone()[0]
    pre_convs = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE conv_uuid = ?",
        (COWORK_CONV_UUID,),
    ).fetchone()[0]
    assert pre_msgs > 0, "fixture build should produce cowork message rows"
    assert pre_convs == 1, "fixture build should produce one cowork projection row"

    # Act: delete the cowork file from the index.
    index.delete_by_path(audit_path)

    # Assert: messages AND conversations for that conv_uuid are GONE.
    post_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conv_uuid = ?", (COWORK_CONV_UUID,)
    ).fetchone()[0]
    post_convs = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE conv_uuid = ?",
        (COWORK_CONV_UUID,),
    ).fetchone()[0]
    assert post_msgs == 0, (
        f"delete_by_path left {post_msgs} orphan message rows for cowork "
        f"conv_uuid={COWORK_CONV_UUID}"
    )
    assert post_convs == 0, (
        f"delete_by_path left {post_convs} orphan conversations row for cowork "
        f"conv_uuid={COWORK_CONV_UUID}"
    )

    # And the indexed_files row is gone (already worked pre-fix).
    post_files = conn.execute(
        "SELECT COUNT(*) FROM indexed_files WHERE path = ?", (str(audit_path),)
    ).fetchone()[0]
    assert post_files == 0


def test_delete_by_path_still_purges_cc_messages(tmp_path: Path):
    """Regression guard: the delete_by_path fix MUST NOT break CC paths.

    For CC files (``<uuid>.jsonl``) the stem IS the conv_uuid. The fix
    must not regress that case.
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    db_path = tmp_path / "search-index.sqlite"
    index = SearchIndex(db_path)

    # Synthesize a CC-shaped conv with a path whose stem matches the uuid.
    cc_uuid = "cc-uuid-1234"
    cc_path = claude_dir / "projects" / "encoded-cwd" / f"{cc_uuid}.jsonl"
    cc_path.parent.mkdir(parents=True)
    cc_path.write_text("ignored")
    conv = {
        "uuid": cc_uuid,
        "name": "CC Conv",
        "source": "CLAUDE_CODE",
        "project_path": "/some/cwd",
        "created_at": "2026-05-25T10:00:00Z",
        "updated_at": "2026-05-25T10:00:01Z",
        "chat_messages": [
            {
                "uuid": "m-1", "sender": "human",
                "text": "CC user message", "content": [],
                "created_at": "2026-05-25T10:00:00Z",
            },
        ],
    }
    index.upsert_conversation(conv, cc_path, cc_path.stat().st_mtime)

    conn = index._get_read_conn()
    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conv_uuid = ?", (cc_uuid,)
    ).fetchone()[0] > 0

    index.delete_by_path(cc_path)

    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conv_uuid = ?", (cc_uuid,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE conv_uuid = ?", (cc_uuid,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Bug 2: SCHEMA_VERSION bump + fast v11→v12 migration purges stale Cowork rows
# ---------------------------------------------------------------------------


def test_schema_version_bumped_so_existing_indexes_rebuild():
    """Cowork support shipped without bumping SCHEMA_VERSION. Bumping it
    to >=12 (or higher) is the recovery mechanism that forces a one-time
    migration on existing user indexes — without it, the user's live
    index will never recover from the corruption pinned by the live
    diagnostic dump (indexed_files rows for cowork but zero messages
    rows).
    """
    # The bump is required. Tests below pin what the v12 migration does;
    # this gate ensures the migration actually fires.
    assert SCHEMA_VERSION >= 12, (
        "SCHEMA_VERSION must be bumped to >=12 so existing user indexes "
        "rebuild on next process start (cowork rows currently missing)."
    )


def test_v11_to_v12_fast_migration_purges_orphan_cowork_state(tmp_path: Path):
    """A pre-existing v11 SQLite file with orphan cowork rows (mirroring
    the live state — indexed_files cowork paths + zero CLAUDE_COWORK
    messages) MUST be cleaned by the v11→v12 fast migration. The CC and
    Desktop rows must SURVIVE the migration (no full rebuild needed).
    """
    db_path = tmp_path / "search-index.sqlite"

    # Hand-craft a v11 DB shape with the same schema the code wrote
    # before the bump, then seed it with the live corruption pattern.
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE VIRTUAL TABLE messages USING fts5(
            conv_uuid UNINDEXED,
            message_uuid UNINDEXED,
            sender UNINDEXED,
            created_at UNINDEXED,
            source UNINDEXED,
            project_path UNINDEXED,
            organization_id UNINDEXED,
            conv_created_at UNINDEXED,
            conv_updated_at UNINDEXED,
            title,
            body,
            body_text,
            tokenize = "porter unicode61 remove_diacritics 1"
        );
        CREATE TABLE indexed_files (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            indexed_at INTEGER NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE conversation_summaries (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            summary_json BLOB NOT NULL,
            cached_at REAL NOT NULL
        );
        CREATE TABLE conversation_summaries_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE conversations (
            conv_uuid TEXT PRIMARY KEY,
            title TEXT,
            conv_created_at TEXT,
            conv_updated_at TEXT,
            project_path TEXT,
            source TEXT,
            organization_id TEXT
        );
        INSERT INTO schema_version (version) VALUES (11);
        """
    )

    # Seed: 1 CC conversation (must survive) + the live corruption pattern
    # (cowork indexed_files row with NO messages rows).
    cc_uuid = "cc-survive-uuid"
    conn.execute(
        "INSERT INTO messages "
        "(conv_uuid, message_uuid, sender, created_at, source, "
        " project_path, organization_id, conv_created_at, conv_updated_at, "
        " title, body, body_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cc_uuid, "m-1", "human", "2026-05-20T00:00:00Z",
         "CLAUDE_CODE", "/some/cwd", "", "2026-05-20T00:00:00Z",
         "2026-05-20T00:00:00Z", "CC Survivor", "important code", "important code"),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cc_uuid, "CC Survivor", "2026-05-20T00:00:00Z",
         "2026-05-20T00:00:00Z", "/some/cwd", "CLAUDE_CODE", ""),
    )
    cc_path = "/fake/projects/encoded-cwd/cc-survive-uuid.jsonl"
    conn.execute(
        "INSERT INTO indexed_files VALUES (?, ?, ?)",
        (cc_path, 1779700000.0, 1779700000),
    )

    # Live corruption pattern: cowork indexed_files row, NO messages.
    cowork_path = (
        "/fake/local-agent-mode-sessions/d/o/local_aaaa1111/audit.jsonl"
    )
    conn.execute(
        "INSERT INTO indexed_files VALUES (?, ?, ?)",
        (cowork_path, 1779700000.0, 1779700000),
    )
    conn.commit()
    conn.close()

    # Now open the index with the CURRENT (post-bump) code.
    #
    # 2026-05-26 update: SCHEMA_VERSION bumped 12 → 13 to add the
    # ``is_compaction_summary`` UNINDEXED column on the messages FTS5
    # table. FTS5 does NOT support ALTER TABLE ADD COLUMN, so v12 →
    # v13 has no fast migration path — it falls through to the full
    # DROP+rebuild branch in ``_init_schema``. That branch also catches
    # the v11 → v13 case (the v11→v12 fast-migration gate
    # ``SCHEMA_VERSION == 12`` no longer fires). The user's CC rows are
    # rebuilt from the on-disk JSONL files by the next drift scan;
    # cowork orphans are purged by the DROP. End state is the same as
    # the original fast-migration contract — this test now pins the
    # full-rebuild path as a correctness baseline.
    index = SearchIndex(db_path)

    rconn = index._get_read_conn()
    # Schema version bumped.
    sv = rconn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert sv == SCHEMA_VERSION

    # Under the full DROP+rebuild path (the fast migration gate doesn't
    # fire for v11→v13), in-memory rows are wiped. The next drift scan
    # re-indexes the CC rows from disk — that's the standard recovery
    # contract pinned by ``test_cowork_token_searchable_after_v12_*``.
    # Here we assert the rebuild semantics: messages and conversations
    # are empty, indexed_files is empty, schema_version is current.
    assert rconn.execute(
        "SELECT COUNT(*) FROM messages"
    ).fetchone()[0] == 0, (
        "v11→current (full rebuild) must DROP all messages — they'll "
        "be re-indexed from disk by the next drift scan"
    )
    assert rconn.execute(
        "SELECT COUNT(*) FROM indexed_files"
    ).fetchone()[0] == 0, (
        "Full rebuild must DROP indexed_files so every on-disk path "
        "looks new to the next drift scan"
    )

    # Defensive: no orphan CLAUDE_COWORK rows survive (the originating
    # bug — cowork indexed_files row with no messages — is impossible
    # after the rebuild, since the rebuild wipes both tables).
    assert rconn.execute(
        "SELECT COUNT(*) FROM messages WHERE source = 'CLAUDE_COWORK'"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Bug 1 + Bug 2 together: end-to-end search-after-recovery
# ---------------------------------------------------------------------------


def test_cowork_token_searchable_after_v12_migration_and_drift(tmp_path: Path):
    """End-to-end: simulate the live failure mode, run the v11→v12
    migration, then a drift pass, then query — the cowork token MUST
    be findable. This is the user-facing acceptance contract.
    """
    # Step 1: pre-seed v11 DB with the corruption pattern.
    db_path = tmp_path / "search-index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE VIRTUAL TABLE messages USING fts5(
            conv_uuid UNINDEXED,
            message_uuid UNINDEXED,
            sender UNINDEXED,
            created_at UNINDEXED,
            source UNINDEXED,
            project_path UNINDEXED,
            organization_id UNINDEXED,
            conv_created_at UNINDEXED,
            conv_updated_at UNINDEXED,
            title,
            body,
            body_text,
            tokenize = "porter unicode61 remove_diacritics 1"
        );
        CREATE TABLE indexed_files (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            indexed_at INTEGER NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE conversation_summaries (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            summary_json BLOB NOT NULL,
            cached_at REAL NOT NULL
        );
        CREATE TABLE conversation_summaries_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE conversations (
            conv_uuid TEXT PRIMARY KEY,
            title TEXT,
            conv_created_at TEXT,
            conv_updated_at TEXT,
            project_path TEXT,
            source TEXT,
            organization_id TEXT
        );
        INSERT INTO schema_version (version) VALUES (11);
        """
    )

    # Stage a real cowork session on disk so the drift pass can find it.
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root = _make_isolated_cowork_root(tmp_path)
    audit_path = (
        cowork_root / "d_test" / "o_test"
        / f"local_{COWORK_CONV_UUID}" / "audit.jsonl"
    )

    # Plant the live-state orphan row pointing at the real on-disk path
    # but with NO accompanying messages (this is the bug).
    conn.execute(
        "INSERT INTO indexed_files VALUES (?, ?, ?)",
        (str(audit_path), audit_path.stat().st_mtime, 1779700000),
    )
    conn.commit()
    conn.close()

    # Step 2: open the index — v11→v12 migration purges the orphan row.
    index = SearchIndex(db_path)

    # Step 3: drift pass re-indexes the path from scratch (now it's "new").
    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    update_drifted_files(store, index=index)

    # Step 4: the cowork token IS findable.
    rows = index.query("COWORK_FIXTURE_HELLO_XYZ", limit=10)
    uuids = sorted({r["conv_uuid"] for r in rows})
    assert uuids == [COWORK_CONV_UUID], (
        f"after recovery, expected {COWORK_CONV_UUID} in results, got {uuids}"
    )


def test_unrelated_token_returns_empty_after_recovery(tmp_path: Path):
    """Bidirectional pair to the recovery test: a token NOT present in
    the fixture returns ZERO rows. Defeats the "always-empty" false-pass
    failure mode where a broken FTS5 path could trivially pass the
    positive assertion by returning [] on every query.
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root = _make_isolated_cowork_root(tmp_path)
    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    db_path = tmp_path / "search-index.sqlite"
    index = SearchIndex(db_path)
    build_full_index(store, index=index)

    # A unique token that does NOT appear in the fixture.
    rows = index.query("TOKEN_THAT_DOES_NOT_APPEAR_ANYWHERE_QWERTY", limit=10)
    assert rows == [], f"unrelated token returned {len(rows)} unexpected hits"


# ---------------------------------------------------------------------------
# Bug 3: clear_all leaks conversations rows
# ---------------------------------------------------------------------------


def test_clear_all_also_truncates_conversations_projection(populated_index):
    """``clear_all`` (used by ``reindex-search --full``) must truncate
    ALL three index tables. Pre-fix it leaks the v10 conversations
    projection — a re-build that skips files leaves orphan title-sweep
    rows for the deleted-from-disk conversations.
    """
    index, _, _ = populated_index
    conn = index._get_read_conn()
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] > 0

    index.clear_all()

    # ALL three must be empty.
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
