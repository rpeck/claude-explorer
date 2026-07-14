"""SQLite FTS5 inverted index for full-text search.

Backend caches at a glance (Cache landscape, 2026-05-18):
  * ``FileCache`` (``backend/cache.py``) — in-memory, per-path
    mtime-keyed cache of parsed conversation dicts; LRU-bounded;
    lost on process restart.
  * ``SummaryCache`` (``backend/summary_cache.py``) — SQLite-persisted
    sidebar summaries; mtime+size invalidation per row; full table
    wipe on ``claude_code_reader.LOGIC_VERSION`` mismatch at lifespan
    startup.
  * ``SearchIndex`` (this module) — SQLite FTS5 inverted index;
    drift-first incremental rebuild keyed on ``indexed_files`` mtime;
    full drop+rebuild on ``SCHEMA_VERSION`` bump or column-set drift
    in the ``messages`` virtual table.

Replaces the linear-scan search path (``backend/search.py``) for queries that
can be answered by a token-based inverted index. The linear-scan code remains
as the fallback whenever the index is unavailable, not yet built, or returns
a SQLite error.

Architecture (Scatter-Gather):
    The FTS5 index is used purely as an inverted index — it returns
    ``(conv_uuid, message_uuid)`` tuples for matched messages and nothing
    else. The body text is NOT pulled across the SQLite/Python boundary.
    The query path then loads each matched conversation from
    :class:`backend.cache.FileCache` (already warm from listing/rendering)
    and runs the existing :func:`backend.search.create_snippet` on the
    flattened message text. This guarantees the response is byte-for-byte
    compatible with the linear-scan path: same snippet boundaries, same
    title pseudo-message, same sort order, same ``MessageSnippet`` shape.

Lifecycle:
    1. **Initial build**: a background task in the FastAPI lifespan calls
       :func:`build_full_index`, which walks every JSON/JSONL file via
       ``store.get_all_conversations_raw()`` and inserts rows.
    2. **Incremental updates**: the existing CC watcher
       (``backend/cc_watcher.py``) calls :func:`update_drifted_files`
       once per scan pass (5s). Mtime check short-circuits no-op cases.
    3. **Drift safety**: every search query, if the index is ready, queries
       it directly. The watcher catches any drift on its next pass.
    4. **Fallback**: if the index isn't ready (initial build still running)
       OR FTS5 isn't available in this sqlite3 build OR a sqlite3.Error
       fires at query time, the search code falls back to linear scan.

Schema:
    See :data:`SCHEMA_SQL`. Bumping :data:`SCHEMA_VERSION` causes a full
    drop+rebuild on next open.

Threading:
    Read connections are per-thread via ``threading.local()`` (FastAPI
    runs sync route handlers in a thread pool). Writes go through a
    single dedicated connection guarded by ``threading.Lock``. WAL mode
    lets readers proceed concurrently with the writer without blocking.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal

from .compact_prefixes import (
    COMPACTION_TITLE_PREFIX,
    is_compaction_prefix_text,
)
from .config import get_settings
from .search_text import _extract_searchable_text


logger = logging.getLogger(__name__)


# Bump to force a full drop+rebuild on next open. Used when the schema
# below changes in a way the existing data can't satisfy.
#
# IMPORTANT: bumping this triggers a drop+rebuild only when the
# `SearchIndex` constructor next runs — i.e. on the NEXT PROCESS START.
# A running uvicorn worker that hot-reloads only the Python source will
# NOT pick up the version change until restart. In dev with
# `--reload`, uvicorn restarts workers on .py edits, so this is
# sufficient. In production, deploying new code restarts the workers.
# A documented manual escape hatch (`claude-explorer reindex-search`)
# also forces a fresh rebuild without bumping.
#
# Version history:
#   * v1: initial FTS5 index over message text + content blocks.
#   * v2 (2026-05-12, V1 polish round 3): also indexes the
#     `slash_command` field on CC command markers so searches for
#     `/coding` (literal) or `coding` (FTS5 token) hit the marker
#     bubble even when the user's args body doesn't contain the word.
#   * v3 (2026-05-13, V1 polish): `thinking` content blocks are no
#     longer indexed (see backend/search.py:_extract_searchable_text).
#     Bumping the version forces a one-time rebuild on next startup so
#     stale entries with thinking-only token matches don't poison FTS5
#     top-N ranking (e.g., a thinking-only hit displacing a real prose
#     match from the top 5000). Index rebuild is non-blocking via the
#     existing lifespan task.
#   * v4 (2026-05-13, V1 polish cleanup): argless command markers
#     (`is_command_marker=True` — `/exit`, `/clear`, `/compact`,
#     plus leading-prelude rows) are no longer indexed. They're
#     chrome that the viewer hides behind SessionPreludeAffordance /
#     SlashCommandBadge and the export surfaces drop via
#     export._is_excludable_marker; mirroring that exclusion in
#     search closes the "one truth, three surfaces" invariant.
#     Argful markers (`/coding <prose>`, `/plan <prose>`) carry
#     is_command_marker=False and continue to be searchable on
#     both the user's prose body and the slash_command token.
#     Bumping the version forces a one-time rebuild on next startup
#     so existing argless-marker body rows in the index get cleared.
#   * v5 (2026-05-14, sidebar-scope propagation): adds an
#     ``organization_id UNINDEXED`` column so the workspace dropdown
#     can narrow search results in SQL (mirrors how source and
#     project_path already work). Bumping the version forces a one-
#     time rebuild so the new column gets populated for every row.
#     Cost: ~36 chars per row UNINDEXED — negligible. The lifespan
#     task (backend/main.py:253, asyncio.to_thread) makes the
#     rebuild non-blocking; queries fall back to linear scan during
#     the rebuild window.
#   * v6 (2026-05-16, PHASE_2 Workstream A): adds
#     ``conv_created_at`` and ``conv_updated_at`` UNINDEXED columns
#     so the FTS5 fast path can build SearchResult objects (which
#     carry conversation-level timestamps) without walking the
#     conversation corpus or hitting the summary cache for every
#     hit. Cost: ~50 chars per row UNINDEXED — negligible against
#     the 861 MB index. Bumping the version forces a one-time
#     rebuild so the new columns get populated. The build remains
#     non-blocking via the existing lifespan task.
#   * v7 (2026-05-16, SEARCH_TOOL_AWARENESS plan §A): adds a second
#     indexed body column ``body_text`` carrying the text-only
#     projection (tool_use / tool_result stripped). The query path
#     selects via FTS5 column-scoped MATCH (``{body_text}:(...)`` vs
#     ``{body}:(...)``) so the Tools toggle behaves the same on the
#     fast path as on the linear-scan path — a hit whose only token
#     lives inside a hidden tool block is excluded BEFORE bm25
#     ranks. Cost: ~30% index size growth (text-only is most of the
#     body for typical CC sessions). Bumping the version forces a
#     one-time rebuild; the build remains non-blocking via the
#     existing lifespan task.
#   * v8 (2026-05-18, doubled-snippet bug): no schema change, but
#     ``_extract_searchable_text`` no longer appends both
#     ``message['text']`` AND each text-type content block. Pre-v8
#     rows have the prose indexed twice (``"X\nX"``), which surfaces
#     in the UI as doubled snippets (e.g. "Good! Now deploy this
#     image:\nGood! Now deploy this image:"). Bumping forces a one-
#     time rebuild so existing rows get the deduped projection;
#     query path falls back to linear scan during the rebuild.
#   * v9 (2026-05-19, doubled-snippet bug, tool-arg sibling): no
#     schema change, but ``_stringify_tool_input`` switched from
#     ``json.dumps + per-value append`` (which doubled every
#     value-text) to ``keys-line + deduped values`` (Option C). The
#     visible cost on pre-v9 rows was the same doubled-snippet UX
#     on every tool-call search hit (e.g. a search for "echo hello"
#     against a Bash tool_use rendered as two identical rows).
#     Bumping forces a one-time rebuild so existing rows get the
#     deduped projection.
# v10 (2026-05-23): adds the ``conversations`` projection table that
# accelerates the title sweep. Pre-v10 the sweep ran
# ``SELECT ... FROM messages WHERE title LIKE '%X%' GROUP BY conv_uuid``
# against the FTS5 virtual table (250K rows / 2.5GB on the user's real
# corpus) which cost ~6.3 s per cold search — 82% of total wall time.
# The projection is one row per conversation (~344 on the user's
# corpus) so LIKE scans in microseconds. Bumping the version triggers
# the v9→v10 migration in :meth:`_init_schema` which populates the
# projection via INSERT INTO conversations SELECT ... FROM messages
# GROUP BY conv_uuid — avoids the 30-min full FTS5 rebuild.
#   * v11 (2026-05-23, compact-marker auto-expand fix): no schema
#     change, but ``_extract_searchable_text`` now drops the BODY for
#     manual ``/compact`` trigger rows (the user message that wraps
#     ``<command-name>/compact</command-name>`` + the user prompt inside
#     ``<command-args>``). Pre-v11 the trigger-row body — including the
#     verbatim user prompt — was in the FTS5 inverted index, so a search
#     for words the user typed in their own /compact prompt landed on
#     the trigger row's UUID instead of the isCompactSummary marker
#     UUID. The wrong UUID broke the frontend's compact-marker
#     auto-expand chain (which keys on ``compact_marker.message_uuid``).
#     Bumping the version forces a one-time full rebuild on next start
#     so deployed users get the cleansed index automatically; query path
#     falls back to linear scan during the rebuild window. The slow
#     scatter-gather paths in backend.search also apply a per-conv
#     trigger→marker UUID rewrite as belt-and-suspenders for any latent
#     stale-index hits during the rebuild window AND for the linear-scan
#     fallback's correctness.
#   * v12 (2026-05-25, Cowork search-recovery): two coupled changes that
#     ride one schema bump.
#
#     (a) Add a ``conv_uuid TEXT`` column to ``indexed_files`` so
#     :meth:`delete_by_path` can look up the conv_uuid directly instead
#     of guessing it from ``file_path.stem``. Pre-v12 the stem heuristic
#     worked for CC + Desktop files (``<uuid>.jsonl`` / ``<uuid>.json``)
#     but silently broke for Cowork (``local_<uuid>/audit.jsonl`` ⇒
#     stem == ``"audit"``). A Cowork ``delete_by_path`` call would drop
#     the ``indexed_files`` row (keyed by path, correct) but the
#     ``DELETE FROM messages WHERE conv_uuid = 'audit'`` no-op'd —
#     orphan messages + conversations rows accumulated.
#
#     (b) Purge orphan Cowork state from existing user indexes. The
#     2026-05-25 bug report surfaced a live index with 42 cowork paths
#     in ``indexed_files`` but ZERO ``messages`` rows tagged
#     ``CLAUDE_COWORK``. The transactional code paths make this state
#     "impossible" under normal operation, so the root cause is either
#     an in-flight migration race or an externally-induced corruption
#     (mid-run CLI version mismatch). The recovery path is
#     deterministic regardless: drop all CLAUDE_COWORK rows from
#     ``messages`` + ``conversations`` and drop all ``audit.jsonl``
#     paths from ``indexed_files``. The next drift pass treats the
#     live cowork sessions as "new" and re-upserts them cleanly with
#     real messages.
#
#     Migration shape: FAST migration (mirrors v9→v10), NOT a full
#     DROP+rebuild. The user's real CC corpus is ~252K rows / 2.5 GB
#     of FTS5 inverted-list data — a full rebuild from disk would take
#     ~minutes during which search degrades to the linear-scan
#     fallback. The v12 migration touches only Cowork rows + adds a
#     column to ``indexed_files`` + backfills ``conv_uuid`` from
#     ``path.stem`` for the surviving CC/Desktop rows (where stem ==
#     uuid by construction). Total work: ~ms on any plausible corpus.
#     Pinned by ``test_cowork_search_bug_2026_05_25.py::
#     test_v11_to_v12_fast_migration_purges_orphan_cowork_state``.
#   * v13 (2026-05-26): added the ``is_compaction_summary`` UNINDEXED
#     column on the ``messages`` FTS5 table. Populated by
#     :meth:`upsert_conversation` from ``conv['compact_markers']`` (the
#     synthetic ``isCompactSummary: true`` row UUIDs). Drives the
#     ``include_compactions=False`` query path that mirrors the existing
#     ``include_tool_calls`` architecture: ``_build_match_where_clause``
#     adds ``AND is_compaction_summary = 0`` so the filter applies BEFORE
#     bm25 ranking + LIMIT — same correctness contract that drove the
#     ``body_text`` column for the Tools toggle.
#
#     Migration shape: full DROP+rebuild. Adding an UNINDEXED column to
#     an existing FTS5 virtual table requires a recreate; no FAST
#     migration path exists for FTS5 schema additions. The user's first
#     post-deploy run pays the rebuild cost (~5-15 s for a typical
#     corpus, ~mins for the ~250K-row power user). Council accepted
#     this trade-off vs the alternative (scatter-time post-filter) which
#     would have caused empty result sets when top-bm25 hits cluster in
#     compaction summaries. See PLANS / Decision Records 2026-05-26.
#   * v14 (2026-05-26, same-day follow-up): added the
#     ``is_compaction_titled`` column on the ``conversations``
#     projection table (NOT the FTS5 ``messages`` table). Closes the
#     v13 title-leak bug: when a CC session starts with a compaction-
#     summary message and has no ``summary``/``custom-title``/
#     ``agent-name`` row, the title falls back to the first 100 chars
#     of the compaction-summary text (see
#     ``cc_message_transforms._extract_conversation_metadata``), and
#     the title-substring sweep would surface the conv as a title-only
#     "ran out of context" hit even with Show Compactions OFF.
#     Populated by :meth:`upsert_conversation` from the canonical
#     ``COMPACTION_TITLE_PREFIX`` in :mod:`backend.compact_prefixes`
#     (single source of truth shared with ``cowork_reader``). The
#     title-sweep helpers add ``AND is_compaction_titled = 0`` when
#     ``include_compactions=False``. Linear-scan + slow FTS index path
#     gate their Python-side title pseudo-message emission on the same
#     predicate.
#
#     Migration shape: FAST migration. The column lives on a regular
#     SQLite table (not the FTS5 virtual table), so ``ALTER TABLE
#     conversations ADD COLUMN`` is constant-time and the backfill is
#     a single UPDATE over ~hundreds of rows. Total work: ~ms on any
#     plausible corpus. No reindex pain — the v13 messages table is
#     left intact. Mirrors the v11→v12 fast-migration pattern.
SCHEMA_VERSION = 14


# ``messages`` is the FTS5 virtual table. UNINDEXED columns store metadata
# we want to retrieve / filter on without paying the inverted-index cost.
# ``title`` and ``body`` are the only indexed columns. With FTS5's default
# ``MATCH`` semantics, an unqualified query searches both — which is what
# we want (the linear-scan path also matches both).
#
# ``indexed_files`` tracks which on-disk files we've already indexed and
# the mtime they had at indexing time. The drift-detection pass uses this
# to decide which files need re-upserting.
#
# ``schema_version`` holds a single integer row. Comparing it to
# :data:`SCHEMA_VERSION` at open time triggers a drop+rebuild on mismatch.
#
# ``conversation_summaries`` is the sidebar-metadata read-through cache
# that powers :mod:`backend.summary_cache`. Co-located in the same
# SQLite file as the FTS5 index so the watcher's drift pass can
# refresh both stores in a single walk. The cache is keyed by the
# on-disk path and stamped with both the file's mtime AND size — a
# miss on either means a re-scan. ``summary_json`` holds the orjson-
# serialized ``ConversationSummary`` payload (~1-2 KB per row). The
# companion ``conversation_summaries_meta`` table holds the source-hash
# of ``read_conversation_summary_fast`` (see ``claude_code_reader.
# LOGIC_VERSION``); a mismatch at startup wipes the cache table.
SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    conv_uuid UNINDEXED,
    message_uuid UNINDEXED,
    sender UNINDEXED,
    created_at UNINDEXED,
    source UNINDEXED,
    project_path UNINDEXED,
    organization_id UNINDEXED,
    conv_created_at UNINDEXED,
    conv_updated_at UNINDEXED,
    is_compaction_summary UNINDEXED,
    title,
    body,
    body_text,
    tokenize = "porter unicode61 remove_diacritics 1"
);

-- v12 (2026-05-25): added ``conv_uuid`` column so :meth:`delete_by_path`
-- can resolve the conv UUID via a path lookup instead of guessing it
-- from ``file_path.stem``. The stem heuristic is correct for CC files
-- (``<uuid>.jsonl``) and Desktop files (``<uuid>.json``) but wrong for
-- Cowork (``local_<uuid>/audit.jsonl`` ⇒ stem == ``"audit"``). The
-- column is populated by :meth:`upsert_conversation` and backfilled
-- for existing CC/Desktop rows by the v11→v12 fast migration in
-- :meth:`_init_schema` (stem is the correct uuid for those rows by
-- construction). The column is NOT a foreign key — the messages /
-- conversations tables have their own conv_uuid columns; this one
-- exists solely so deletion-by-path is a lookup, not a heuristic.
CREATE TABLE IF NOT EXISTS indexed_files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    indexed_at INTEGER NOT NULL,
    conv_uuid TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    summary_json BLOB NOT NULL,
    cached_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_summaries_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- v10 title-sweep projection. One row per conversation; ``conv_uuid`` is
-- PK so the title sweep can ``SELECT conv_uuid FROM conversations
-- WHERE title LIKE '%X%'`` against ~hundreds of rows instead of the
-- 250K-row FTS5 messages virtual table. Substring semantics (e.g.
-- "edul" inside "scheduled") are preserved because we still use LIKE;
-- the only change is the row set it scans. Maintained in the same
-- transaction as the matching messages-table writes so a crash mid-
-- upsert rolls back both or neither.
--
-- v14 (2026-05-26): added ``is_compaction_titled`` so the title-sweep
-- helpers (``title_match_snippets`` / ``title_match_uuids``) can
-- filter out conversations whose title text is the canonical
-- compaction-summary prefix when the user has Show Compactions OFF.
-- Populated in :meth:`upsert_conversation` from the canonical
-- ``COMPACTION_TITLE_PREFIX`` in :mod:`backend.compact_prefixes`.
-- INTEGER NOT NULL DEFAULT 0 so existing rows get the safe default
-- (visible) during fast migration; backfill UPDATE flips
-- compaction-titled rows to 1.
CREATE TABLE IF NOT EXISTS conversations (
    conv_uuid TEXT PRIMARY KEY,
    title TEXT,
    conv_created_at TEXT,
    conv_updated_at TEXT,
    project_path TEXT,
    source TEXT,
    organization_id TEXT,
    is_compaction_titled INTEGER NOT NULL DEFAULT 0
);
"""


# Match FTS5 reserved-keyword tokens that, if a user typed them
# unquoted, would be interpreted as query operators. The escape function
# below quotes EVERY token, so this is mostly defensive — but if the
# escape policy ever changes, this list documents the trap.
_FTS5_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def fts5_available() -> bool:
    """Probe whether the local sqlite3 build supports FTS5.

    macOS Homebrew Python (3.11+) and the python.org installer ship FTS5
    by default. Some Linux distros' system Python builds do not. If FTS5
    is missing, the caller MUST fall back to linear scan.
    """
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(c)")
        return True
    except sqlite3.OperationalError:
        return False


def default_index_path() -> Path:
    """Return the canonical on-disk index location.

    Lives at ``<data_dir>.parent / "search-index.sqlite"`` so it's a sibling
    of the conversations dir (typically ``~/.claude-explorer/``). Same
    pattern the preferences file uses.
    """
    return get_settings().data_dir.parent / "search-index.sqlite"


def translate_query(user_query: str) -> str:
    """Translate a free-form user query into an FTS5 MATCH expression.

    Modes:
      * **Phrase mode** — when the user wraps the whole query in double
        quotes (e.g. ``"foo bar baz"``), emit a single FTS5 phrase
        ``"foo bar baz"`` so MATCH requires the tokens to be adjacent
        in order. No trailing wildcard (an exact phrase shouldn't
        morph as the user types).
      * **Token mode** — unquoted whitespace-separated tokens are each
        quoted (defends FTS5 reserved keywords ``AND/OR/NOT/NEAR`` and
        punctuation) and AND'd. The LAST token gets a ``*`` prefix
        wildcard so search-as-you-type matches: typing ``"pyth"`` finds
        ``"python"``. A single-character last token does NOT get a
        wildcard — FTS5 prefix queries on single letters explode the
        result set.

    Internal ``"`` characters in tokens are doubled so FTS5's phrase
    grammar stays valid.

    Returns the empty string if the user query has no usable tokens; the
    caller treats that as "no query" and skips the SQL.
    """
    stripped = user_query.strip()
    if not stripped:
        return ""

    # Phrase mode: leading + trailing " and at least one char inside.
    if len(stripped) >= 3 and stripped[0] == '"' and stripped[-1] == '"':
        inner = stripped[1:-1].strip()
        if inner:
            clean = inner.replace('"', '""')
            return f'"{clean}"'

    tokens = stripped.split()
    if not tokens:
        return ""

    parts: list[str] = []
    last_idx = len(tokens) - 1
    for i, tok in enumerate(tokens):
        clean = tok.replace('"', '""')
        if i == last_idx and len(tok) >= 2:
            # Trailing prefix wildcard for search-as-you-type.
            parts.append(f'"{clean}" *')
        else:
            parts.append(f'"{clean}"')
    return " AND ".join(parts)


class SearchIndex:
    """A single SQLite/FTS5 file-backed inverted index.

    One instance per index file. The module-level :func:`get_search_index`
    singleton wraps this for the canonical location.

    Invalidation policy:
      * **Trigger (per file)**: :func:`update_drifted_files` /
        :func:`_drift_first_scan` compare each live file's ``os.stat``
        mtime against the value stamped in ``indexed_files``; any
        mismatch (or missing row) re-upserts the conversation in a
        single transaction. Files in ``indexed_files`` whose paths are
        gone from disk get dropped via :meth:`delete_by_path`.
        :meth:`upsert_conversation` wraps DELETE+INSERT in
        ``with self._write_conn:`` so a partial failure rolls back —
        no half-deleted conversations.
      * **Persists across restart**: yes — the FTS5 tables, the
        ``indexed_files`` mtime ledger, and the ``schema_version`` row
        all live in the SQLite file. A warm restart picks up where the
        last process left off; the lifespan task runs a drift pass to
        absorb any edits that happened while the process was down.
      * **Full rebuild**: :meth:`_init_schema` drops and recreates the
        tables on ANY of (a) ``schema_version`` row missing,
        (b) stored version ≠ :data:`SCHEMA_VERSION`, or (c) the
        on-disk ``messages`` column set ≠ ``_EXPECTED_MESSAGES_COLS``
        (defends against the historical "version stamped but rebuild
        failed" bug). ``_schema_ok=False`` during the rebuild so
        in-flight queries fall back. The manual escape hatch
        ``claude-explorer reindex-search`` forces a rebuild without
        bumping the version.
      * **Failure mode**: builds without FTS5 return ``None`` from
        :func:`get_search_index`. ``is_ready()`` stays False until the
        first :func:`build_full_index` pass completes, and flips back
        to False during a destructive migration. In all these cases
        :func:`backend.search.search_conversations` falls back to the
        linear scan — search never goes "down". A ``sqlite3.Error``
        at query time is caught at the dispatcher layer and also
        falls back to linear scan.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Per-thread read connections. SQLite forbids sharing a connection
        # across threads by default; check_same_thread=False relaxes that
        # but still doesn't make a single connection safe under concurrent
        # access. The robust pattern is one connection per thread.
        self._read_local = threading.local()

        # Single dedicated write connection guarded by a lock. WAL mode
        # ensures readers don't block on this writer.
        #
        # NOTE: we deliberately do NOT pass isolation_level=None here.
        # Python's sqlite3 default ("legacy" mode) auto-BEGINs a deferred
        # transaction before DML statements, and ``with conn:`` commits on
        # success / rolls back on exception. That's exactly the
        # crash-safety we need for upsert_conversation's DELETE+INSERT.
        # In autocommit (isolation_level=None) ``with conn:`` is a no-op
        # so a failed INSERT after a successful DELETE would leave the
        # rows GONE. A failing test in test_search_index.py
        # (test_upsert_rollback_on_executemany_failure) pins this.
        self._write_conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
        )
        self._write_lock = threading.Lock()

        # Configure WAL + sensible pragmas. Done on the write connection
        # but applies to the whole database file.
        #
        # 2026-05-24 concurrency fix: added busy_timeout=30000 (30 s).
        # search_index and summary_cache share the same SQLite database
        # file; each opens its own writer connection guarded by its own
        # threading.Lock. WAL mode lets the writers serialize at the
        # SQLite level via busy_timeout, but only IF a timeout is set —
        # the default is 0, which makes the second writer raise
        # ``database is locked`` immediately on any contention. 30 s
        # outlasts all known writers (summary_cache.upsert_many is the
        # slow path here, occasionally taking seconds during a fetch
        # backfill). Matches the value set on summary_cache._write_conn.
        self._write_conn.execute("PRAGMA journal_mode = WAL")
        self._write_conn.execute("PRAGMA synchronous = NORMAL")
        self._write_conn.execute("PRAGMA busy_timeout = 30000")
        self._write_conn.execute("PRAGMA temp_store = MEMORY")

        # _is_ready toggles to True after the first full build pass
        # finishes. Queries fall back to linear scan while this is False.
        self._is_ready = False
        # Set to False during a destructive schema migration so in-flight
        # queries fall back gracefully while the rebuild runs.
        self._schema_ok = True

        self._init_schema()

    # ----- schema ----------------------------------------------------

    # Expected user-facing columns of the ``messages`` FTS5 table for the
    # current SCHEMA_VERSION. Used at open time to detect when an on-disk
    # ``messages`` table predates the current code (column-level drift),
    # which the version-row check alone can miss — see below.
    _EXPECTED_MESSAGES_COLS = frozenset({
        "conv_uuid", "message_uuid", "sender", "created_at",
        "source", "project_path", "organization_id",
        "conv_created_at", "conv_updated_at",
        "is_compaction_summary",
        "title", "body", "body_text",
    })

    def _init_schema(self) -> None:
        """Create tables if missing; drop+rebuild if the on-disk schema
        doesn't match the current code.

        We trigger a drop+rebuild on ANY of:

          * the ``schema_version`` row is missing (legitimately fresh DB
            falls into this branch too — drop+rebuild on an empty file is
            cheap and ensures a clean state);
          * the ``schema_version`` row doesn't equal ``SCHEMA_VERSION``;
          * the existing ``messages`` table's column set doesn't match
            ``_EXPECTED_MESSAGES_COLS`` (defensive: catches the historical
            bug where a prior process stamped the version row but failed
            to actually rebuild the table, leaving the DB in a state where
            ``upsert_conversation`` raises "no column named X" forever
            because the version-row check declares the schema "current").

        On rebuild we set ``_schema_ok=False`` BEFORE the DROP so any
        concurrent query falls back to linear scan instead of seeing a
        half-rebuilt index. Once the rebuild finishes we restore the flag
        but leave ``_is_ready=False`` until :func:`build_full_index`
        completes its first pass.
        """
        with self._write_lock:
            cur = self._write_conn.cursor()

            # Inspect what's actually on disk BEFORE running CREATE IF NOT
            # EXISTS — otherwise we'd lose the ability to distinguish a
            # genuinely fresh DB from a stale-tables-without-version-row
            # case. ``PRAGMA table_info`` works on FTS5 virtual tables and
            # returns the user-defined column list.
            existing_cols = {
                r[1] for r in cur.execute("PRAGMA table_info(messages)").fetchall()
            }

            # ``schema_version`` may not exist yet on a fresh file; guard
            # the SELECT with a table-existence check.
            sv_exists = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone() is not None
            row = (
                cur.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
                if sv_exists else None
            )

            cols_ok = (not existing_cols) or existing_cols == self._EXPECTED_MESSAGES_COLS
            version_ok = row is not None and row[0] == SCHEMA_VERSION

            # v14 self-repair (2026-05-26): trust schema_version ONLY when
            # the conversations projection also has the v14 column set.
            # Observed live: uvicorn --reload race with the supervised
            # watcher stamped schema_version=14 against a v13-shaped
            # conversations table (no is_compaction_titled column). Pre-
            # v14, the early-return below skipped the ALTER → every
            # subsequent upsert crashed on INSERT into the missing
            # column. We re-add the column + backfill here when the
            # state is detected.
            conv_table_exists = cur.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='conversations'"
            ).fetchone() is not None
            conv_cols_ok = True
            if version_ok and conv_table_exists:
                conv_cols = {
                    r[1] for r in cur.execute(
                        "PRAGMA table_info(conversations)"
                    ).fetchall()
                }
                conv_cols_ok = "is_compaction_titled" in conv_cols
                if not conv_cols_ok:
                    logger.warning(
                        "search_index: v14 self-repair — schema_version=14 "
                        "but conversations.is_compaction_titled missing; "
                        "adding column + backfilling (silent prior failure "
                        "recovery)"
                    )
                    cur.execute(
                        "ALTER TABLE conversations "
                        "ADD COLUMN is_compaction_titled "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                    cur.execute(
                        "UPDATE conversations "
                        "SET is_compaction_titled = 1 "
                        "WHERE ltrim(title) LIKE ? || '%'",
                        (COMPACTION_TITLE_PREFIX,),
                    )
                    self._write_conn.commit()
                    conv_cols_ok = True

            if cols_ok and version_ok and conv_cols_ok:
                # On-disk schema matches; ensure all tables exist (no-op if
                # already there) and we're done.
                cur.executescript(SCHEMA_SQL)
                # v10 conditional backfill (2026-05-23): a CHEAP count
                # check guards against the race that surfaced in dev
                # when SCHEMA_VERSION was bumped from 9 to 10 while a
                # backend with the OLD code was still running. The OLD
                # code wrote to ``messages`` only, leaving rows in
                # ``messages`` that the v9→v10 migration (which fired
                # in a different process) had already snapshotted. The
                # next open finds version_ok=True so the migration
                # shim skips — without this backfill the orphaned
                # messages would never get projection rows and the
                # title sweep would silently miss them.
                #
                # The count check (~2 fast aggregate queries) runs on
                # every open. The corrective backfill INSERT only
                # fires when counts disagree — the steady-state cost
                # is ~ms, NOT the ~5 s full GROUP BY scan.
                conv_count = cur.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0]
                msg_conv_count = cur.execute(
                    "SELECT COUNT(DISTINCT conv_uuid) FROM messages "
                    "WHERE conv_uuid != ''"
                ).fetchone()[0]
                if conv_count < msg_conv_count:
                    logger.info(
                        "search_index: conversations projection drift "
                        "(projection=%d, messages distinct=%d); "
                        "backfilling",
                        conv_count, msg_conv_count,
                    )
                    # v14 (2026-05-26): populate ``is_compaction_titled``
                    # from the title text via SQL ``ltrim()`` + ``LIKE``
                    # so the backfilled rows respect the Show Compactions
                    # filter immediately. Predicate is anchored after
                    # leading whitespace (matches the Python
                    # ``is_compaction_prefix_text`` helper). Using SQL
                    # CASE here avoids a Python round-trip per row.
                    cur.execute(
                        "INSERT OR IGNORE INTO conversations "
                        "(conv_uuid, title, conv_created_at, "
                        " conv_updated_at, project_path, source, "
                        " organization_id, is_compaction_titled) "
                        "SELECT conv_uuid, title, conv_created_at, "
                        "       conv_updated_at, project_path, source, "
                        "       organization_id, "
                        "       CASE WHEN ltrim(title) LIKE ? || '%' "
                        "            THEN 1 ELSE 0 END "
                        "FROM messages WHERE conv_uuid != '' "
                        "GROUP BY conv_uuid",
                        (COMPACTION_TITLE_PREFIX,),
                    )
                self._write_conn.commit()
                return

            # v9 → v10 fast migration (2026-05-23): add the
            # ``conversations`` projection without dropping the
            # (potentially 2.5 GB) messages table. The user's real
            # corpus would otherwise re-walk every JSONL — ~30 min on
            # ~1,200 files. The targeted INSERT INTO conversations
            # SELECT ... FROM messages GROUP BY conv_uuid takes one
            # cold scan (~6 s) and is then done.
            #
            # Gating condition: v9 stamped, messages cols already match
            # current code (no body-schema drift), so the only delta is
            # the missing projection table.
            on_disk_version = row[0] if row is not None else None
            if (
                on_disk_version == 9
                and SCHEMA_VERSION == 10
                and existing_cols == self._EXPECTED_MESSAGES_COLS
            ):
                logger.info(
                    "search_index: fast-migrating v9 → v10 "
                    "(populating conversations projection from messages)"
                )
                self._schema_ok = False
                try:
                    cur.executescript(SCHEMA_SQL)
                    # Populate the projection from existing messages.
                    # GROUP BY collapses the per-message rows to one
                    # row per conv; conv_uuid='' rows (none should
                    # exist, but be defensive) are excluded so the
                    # PK doesn't collide.
                    cur.execute(
                        "INSERT OR IGNORE INTO conversations "
                        "(conv_uuid, title, conv_created_at, "
                        " conv_updated_at, project_path, source, "
                        " organization_id) "
                        "SELECT conv_uuid, title, conv_created_at, "
                        "       conv_updated_at, project_path, source, "
                        "       organization_id "
                        "FROM messages WHERE conv_uuid != '' "
                        "GROUP BY conv_uuid"
                    )
                    cur.execute("DELETE FROM schema_version")
                    cur.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                    self._write_conn.commit()
                finally:
                    self._schema_ok = True
                return

            # v11 → v12 fast migration (2026-05-25): two coupled changes.
            #
            # (a) Add ``conv_uuid`` to ``indexed_files`` and backfill from
            #     ``path.stem`` for existing rows. Pre-v12 the only
            #     ``delete_by_path`` strategy was to guess the conv_uuid
            #     from the file stem — correct for CC (``<uuid>.jsonl``)
            #     and Desktop (``<uuid>.json``) but wrong for Cowork
            #     (``local_<uuid>/audit.jsonl`` ⇒ stem == ``"audit"``).
            #     The column makes the deletion a SQL lookup.
            #
            # (b) Purge orphan Cowork state. The 2026-05-25 bug surfaced
            #     a live index with cowork paths in ``indexed_files``
            #     and ZERO matching ``messages`` rows. The transactional
            #     write paths make that state impossible under normal
            #     operation, so the migration deterministically purges
            #     all CLAUDE_COWORK rows + all ``audit.jsonl`` indexed
            #     paths and lets the next drift pass re-upsert from
            #     scratch.
            #
            # Gating condition: v11 stamped + messages cols match
            # current code (no body-schema drift). If either fails we
            # fall through to the full DROP+rebuild below — safer than
            # half-migrating.
            if (
                on_disk_version == 11
                and SCHEMA_VERSION == 12
                and existing_cols == self._EXPECTED_MESSAGES_COLS
            ):
                logger.info(
                    "search_index: fast-migrating v11 → v12 "
                    "(adding conv_uuid to indexed_files + purging orphan "
                    "Cowork rows)"
                )
                self._schema_ok = False
                try:
                    # Step 1: add the new column. ALTER TABLE ADD COLUMN
                    # is constant-time in SQLite (no row rewrite); the
                    # default is NULL for existing rows.
                    #
                    # Use PRAGMA table_info to check whether the column
                    # already exists (a partial prior migration could
                    # have added the column but failed to bump
                    # schema_version; idempotency preserves the recovery
                    # path).
                    existing_idx_cols = {
                        r[1] for r in cur.execute(
                            "PRAGMA table_info(indexed_files)"
                        ).fetchall()
                    }
                    if "conv_uuid" not in existing_idx_cols:
                        cur.execute(
                            "ALTER TABLE indexed_files ADD COLUMN conv_uuid TEXT"
                        )

                    # Step 2: backfill conv_uuid for existing rows. For
                    # ALL pre-v12 rows the stem heuristic was correct
                    # (Cowork rows weren't reachable via delete_by_path
                    # in the broken state anyway — that was the bug).
                    # Use Python-side stem extraction because SQLite has
                    # no portable path-stem function.
                    rows_to_backfill = cur.execute(
                        "SELECT path FROM indexed_files WHERE conv_uuid IS NULL"
                    ).fetchall()
                    backfill: list[tuple[str, str]] = []
                    for (path_str,) in rows_to_backfill:
                        p = Path(path_str)
                        # For Cowork: stem is "audit" — we're purging
                        # those rows below so the backfill value is
                        # irrelevant. For CC/Desktop: stem == uuid.
                        backfill.append((p.stem, path_str))
                    if backfill:
                        cur.executemany(
                            "UPDATE indexed_files SET conv_uuid = ? WHERE path = ?",
                            backfill,
                        )

                    # Step 3: purge orphan Cowork state. The audit.jsonl
                    # filename is a hard discriminator — no other code
                    # path writes a file with that exact name to the
                    # index. The DELETE FROM messages drops any rows
                    # that DID make it into the FTS5 table (handles the
                    # case where the live state has both indexed_files
                    # AND messages cowork rows; the bug report had only
                    # the former, but the migration is safe either way).
                    cur.execute(
                        "DELETE FROM messages WHERE source = 'CLAUDE_COWORK'"
                    )
                    cur.execute(
                        "DELETE FROM conversations WHERE source = 'CLAUDE_COWORK'"
                    )
                    cur.execute(
                        "DELETE FROM indexed_files WHERE path LIKE '%audit.jsonl'"
                    )

                    cur.execute("DELETE FROM schema_version")
                    cur.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                    self._write_conn.commit()
                finally:
                    self._schema_ok = True
                return

            # v13 → v14 fast migration (2026-05-26): add
            # ``is_compaction_titled`` column to the ``conversations``
            # projection table and backfill it from the title text.
            # The column lives on a regular SQLite table (NOT the FTS5
            # virtual table), so ALTER TABLE ADD COLUMN is constant-
            # time and the backfill is a single UPDATE over ~hundreds
            # of rows — total ~ms. CRITICAL: without this fast path
            # the v13→v14 transition would fall through to the full
            # DROP+rebuild branch below and the user would pay another
            # ~5-15 min reindex on the same day they paid for v12→v13.
            #
            # Gating condition: v13 stamped + messages cols match the
            # current code (the v13 FTS5 schema is unchanged in v14).
            # Both checks must pass — if messages cols drifted the safer
            # move is the full rebuild below.
            if (
                on_disk_version == 13
                and SCHEMA_VERSION == 14
                and existing_cols == self._EXPECTED_MESSAGES_COLS
            ):
                logger.info(
                    "search_index: fast-migrating v13 → v14 "
                    "(adding is_compaction_titled to conversations + "
                    "backfilling from title text)"
                )
                self._schema_ok = False
                try:
                    # Step 1: idempotent column add. ALTER TABLE ADD
                    # COLUMN is constant-time in SQLite. Gate on
                    # PRAGMA table_info to survive partial prior
                    # migrations (column added but schema_version not
                    # stamped) — same idempotency pattern v11→v12 uses
                    # for the indexed_files.conv_uuid column.
                    existing_conv_cols = {
                        r[1] for r in cur.execute(
                            "PRAGMA table_info(conversations)"
                        ).fetchall()
                    }
                    if "is_compaction_titled" not in existing_conv_cols:
                        # NOT NULL DEFAULT 0 is safe: every existing row
                        # gets 0 (visible) until the backfill UPDATE
                        # flips compaction-titled rows to 1.
                        cur.execute(
                            "ALTER TABLE conversations "
                            "ADD COLUMN is_compaction_titled "
                            "INTEGER NOT NULL DEFAULT 0"
                        )

                    # Step 2: backfill in pure SQL. ``ltrim(title)``
                    # mirrors the Python ``.lstrip()`` whitespace
                    # tolerance in :func:`is_compaction_prefix_text`.
                    # The ``? || '%'`` parameter pattern is the safe
                    # way to bind a LIKE-prefix in SQLite (parameter
                    # binding doesn't substitute inside the LIKE
                    # literal directly).
                    cur.execute(
                        "UPDATE conversations "
                        "SET is_compaction_titled = 1 "
                        "WHERE ltrim(title) LIKE ? || '%'",
                        (COMPACTION_TITLE_PREFIX,),
                    )

                    cur.execute("DELETE FROM schema_version")
                    cur.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                    self._write_conn.commit()
                finally:
                    self._schema_ok = True
                return

            logger.info(
                "search_index: rebuilding (version on-disk=%s code=%s; messages cols match=%s)",
                on_disk_version, SCHEMA_VERSION, existing_cols == self._EXPECTED_MESSAGES_COLS,
            )
            self._schema_ok = False
            try:
                cur.execute("DROP TABLE IF EXISTS messages")
                cur.execute("DROP TABLE IF EXISTS indexed_files")
                cur.execute("DROP TABLE IF EXISTS schema_version")
                # v10: also drop the projection so the rebuild starts
                # clean — every upsert will repopulate it row-by-row.
                cur.execute("DROP TABLE IF EXISTS conversations")
                cur.executescript(SCHEMA_SQL)
                cur.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
                self._write_conn.commit()
            finally:
                self._schema_ok = True

    # ----- read connections ------------------------------------------

    def _get_read_conn(self) -> sqlite3.Connection:
        """Get the per-thread read connection, creating it on first call."""
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
            )
            # 2026-05-24 concurrency fix: same rationale as the writer.
            # Default is 0, which makes the reader fail immediately on
            # any file-level contention (checkpoint, VACUUM). 30 s
            # outlasts all known writers.
            conn.execute("PRAGMA busy_timeout = 30000")
            self._read_local.conn = conn
        return conn

    # ----- readiness flags -------------------------------------------

    def is_ready(self) -> bool:
        """True when the index has been fully built at least once and the
        schema is intact. False during initial build or schema migration."""
        return self._is_ready and self._schema_ok

    def mark_ready(self) -> None:
        """Mark the index as queryable. Called by build_full_index after
        the first complete walk."""
        self._is_ready = True

    def indexed_file_count(self) -> int:
        """Number of files currently recorded in the index. 0 on any error.

        Reads the on-disk ``indexed_files`` ledger via a read connection, so
        it works in a cold one-shot process (e.g. the ``doctor`` CLI) that
        never ran ``build_full_index``/``mark_ready`` in-process."""
        try:
            conn = self._get_read_conn()
            row = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def is_built_on_disk(self) -> bool:
        """On-disk readiness — schema intact AND at least one file indexed.

        Unlike :meth:`is_ready` (which reflects whether THIS process ran the
        build and is the correct signal for the running server's FTS5-vs-
        linear dispatch), this reflects the actual on-disk state. Use it for
        diagnostics in a cold process where ``is_ready`` is always False."""
        return self._schema_ok and self.indexed_file_count() > 0

    # ----- writers ---------------------------------------------------

    def upsert_conversation(
        self,
        conv: dict[str, Any],
        file_path: Path,
        mtime: float,
    ) -> int:
        """Insert all messages for one conversation; replace any existing rows.

        Wrapped in a single transaction so a crash mid-upsert leaves either
        the OLD state (rolled back) or the NEW state — never a half-deleted
        conversation. Returns the count of message rows written.
        """
        conv_uuid = conv.get("uuid", "")
        if not conv_uuid:
            return 0

        title = conv.get("name", "") or ""
        source = conv.get("source", "CLAUDE_AI") or "CLAUDE_AI"
        project_path = conv.get("project_path") or ""
        # 2026-05-14 (v5): workspace gate. Empty string ("") for legacy
        # untagged Desktop blobs and for all Claude Code conversations
        # (CC has no workspace concept). The query path treats empty as
        # "no workspace" — only an exact UUID match counts.
        organization_id = conv.get("organization_id") or ""
        # 2026-05-16 (v6): conv-level timestamps so the FTS5 fast path
        # can build SearchResult objects without re-walking the corpus.
        # Stored as ISO 8601 strings (same format as per-message
        # created_at) so the SQL doesn't have to parse/coerce.
        conv_created_at = conv.get("created_at", "") or ""
        conv_updated_at = conv.get("updated_at", "") or ""

        # v13 (2026-05-26): build the set of UUIDs that are compaction-
        # summary rows (the synthetic ``isCompactSummary: true`` messages
        # backend.cc_image_markers.extract_compact_markers emits). Read
        # from ``conv['compact_markers']`` — the post-parse projection
        # the exporters also consume (backend/exporters/_shared.py:
        # _is_compact_summary_message). Same source of truth → no drift.
        # Empty for the common case (Desktop convs, CC sessions without
        # compactions); cheap dict.get + comprehension.
        compaction_uuids: set[str] = {
            m.get("message_uuid", "")
            for m in (conv.get("compact_markers") or [])
            if isinstance(m, dict) and m.get("message_uuid")
        }

        rows: list[tuple[str, str, str, str, str, str, str, str, str, int, str, str, str]] = []
        for msg in conv.get("chat_messages", []) or []:
            # 2026-05-16 (v7): two parallel projections from the SAME
            # source message via the existing linear-scan helper.
            # body_text strips tool_use / tool_result so a hit whose
            # only token lives inside a hidden tool block is excluded
            # at MATCH time when the user has Tools off. Both columns
            # share the same image-marker handling (the extractor
            # treats image content uniformly), so an [Image: ...]
            # placeholder appears in both — image markers stay
            # visible regardless of the Tools toggle.
            body = _extract_searchable_text(msg, include_tool_calls=True)
            body_text = _extract_searchable_text(msg, include_tool_calls=False)
            msg_uuid = msg.get("uuid", "") or ""
            # v13 (2026-05-26): 1 iff this is a compaction-summary row
            # by UUID; 0 otherwise. The exporters' identity predicate
            # (``_is_compact_summary_message``) keys on the same set,
            # so search + export agree on what counts as compaction
            # content. Stored as INTEGER (FTS5 has no native bool) so
            # the WHERE filter is ``is_compaction_summary = 0``.
            is_compact = 1 if msg_uuid in compaction_uuids else 0
            # We index even messages with empty body so the title-only
            # match still has a stable ``conv_uuid`` to anchor against.
            # Title-only matches are produced by the title column.
            rows.append(
                (
                    conv_uuid,
                    msg_uuid,
                    msg.get("sender", "") or "",
                    msg.get("created_at", "") or "",
                    source,
                    project_path,
                    organization_id,
                    conv_created_at,
                    conv_updated_at,
                    is_compact,
                    title,
                    body,
                    body_text,
                )
            )

        # If a conversation has no messages we still want a row so a
        # title-only query hits something. Use a sentinel message_uuid.
        # is_compaction_summary=0 — title-only sentinel is never a
        # compaction row.
        if not rows:
            rows.append(
                (
                    conv_uuid, "title", "title", "",
                    source, project_path, organization_id,
                    conv_created_at, conv_updated_at,
                    0,
                    title, "", "",
                )
            )

        with self._write_lock:
            with self._write_conn:  # explicit BEGIN; auto-COMMIT or ROLLBACK
                self._write_conn.execute(
                    "DELETE FROM messages WHERE conv_uuid = ?", (conv_uuid,)
                )
                self._write_conn.executemany(
                    "INSERT INTO messages "
                    "(conv_uuid, message_uuid, sender, created_at, source, "
                    " project_path, organization_id, conv_created_at, "
                    " conv_updated_at, is_compaction_summary, "
                    " title, body, body_text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                # v10 title projection (2026-05-23). INSERT OR REPLACE
                # keeps one-row-per-conv invariant on title renames /
                # re-upserts. Same transaction as the messages writes
                # so a crash mid-upsert rolls both back — no split-brain
                # "projection updated, messages stale" state.
                #
                # v14 (2026-05-26): also write ``is_compaction_titled``
                # so the title-sweep helpers can filter out compaction-
                # derived titles when Show Compactions is OFF. The
                # ``is_compaction_prefix_text`` shared helper applies
                # the canonical anchored ``.lstrip().startswith()``
                # check — same predicate as
                # ``cowork_reader._extract_cowork_compact_markers``.
                # User-renamed sessions (``/rename`` writes a
                # ``custom-title`` row that WINS over the fallback in
                # ``cc_message_transforms``) get the new title here, so
                # this flag re-evaluates correctly on the next upsert.
                is_compaction_titled = 1 if is_compaction_prefix_text(title) else 0
                self._write_conn.execute(
                    "INSERT OR REPLACE INTO conversations "
                    "(conv_uuid, title, conv_created_at, conv_updated_at, "
                    " project_path, source, organization_id, "
                    " is_compaction_titled) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        conv_uuid,
                        title,
                        conv_created_at,
                        conv_updated_at,
                        project_path,
                        source,
                        organization_id,
                        is_compaction_titled,
                    ),
                )
                # v12 (2026-05-25): also persist the conv_uuid so
                # delete_by_path can resolve it via a SQL lookup
                # instead of the stem heuristic that silently failed
                # for Cowork (``local_<uuid>/audit.jsonl`` ⇒ stem ==
                # "audit", DELETE no-op).
                self._write_conn.execute(
                    "INSERT OR REPLACE INTO indexed_files "
                    "(path, mtime, indexed_at, conv_uuid) "
                    "VALUES (?, ?, ?, ?)",
                    (str(file_path), float(mtime), int(time.time()), conv_uuid),
                )

        return len(rows)

    def delete_conversation(self, conv_uuid: str, file_path: Path | None = None) -> None:
        """Remove a conversation's rows from the index."""
        with self._write_lock:
            with self._write_conn:
                self._write_conn.execute(
                    "DELETE FROM messages WHERE conv_uuid = ?", (conv_uuid,)
                )
                # v10: purge the projection row alongside. Otherwise the
                # title sweep would still surface a deleted conversation.
                self._write_conn.execute(
                    "DELETE FROM conversations WHERE conv_uuid = ?", (conv_uuid,)
                )
                if file_path is not None:
                    self._write_conn.execute(
                        "DELETE FROM indexed_files WHERE path = ?", (str(file_path),)
                    )

    def delete_by_path(self, file_path: Path) -> None:
        """Remove all rows for a file that no longer exists on disk.

        Used by the drift-cleanup pass: if ``indexed_files`` mentions a
        path that ``os.path.exists`` says is gone, drop both the file
        record and any messages whose source file was that path.

        v12 (2026-05-25, Cowork search-recovery): resolve the conv_uuid
        by SQL lookup against the new ``indexed_files.conv_uuid``
        column. Pre-v12 the lookup was a ``file_path.stem`` heuristic
        — correct for CC (``<uuid>.jsonl``) and Desktop
        (``<uuid>.json``) where stem == uuid, but silently broken for
        Cowork (``local_<uuid>/audit.jsonl`` ⇒ stem == ``"audit"``).
        A Cowork ``delete_by_path`` call would drop the
        ``indexed_files`` row (correct, keyed by path) but the
        ``DELETE FROM messages WHERE conv_uuid = 'audit'`` no-op'd,
        leaking orphan ``messages`` + ``conversations`` rows until
        the next full rebuild.

        Defensive fallback: if no ``indexed_files`` row exists for the
        path (e.g., caller invoked delete_by_path on a path that was
        never indexed, or on a partial write), fall back to the stem
        heuristic so the legacy correctness contract for CC/Desktop
        paths still holds. The fallback is no-op for Cowork (stem ==
        "audit" matches no real conv_uuid), which is the same safety
        story as pre-v12 for that branch — the migration has already
        purged any orphans by the time this fallback would fire.
        """
        with self._write_lock:
            with self._write_conn:
                row = self._write_conn.execute(
                    "SELECT conv_uuid FROM indexed_files WHERE path = ?",
                    (str(file_path),),
                ).fetchone()
                if row is not None and row[0]:
                    conv_uuid = row[0]
                else:
                    # No indexed_files row → fall back to the historical
                    # stem heuristic. Correct for CC/Desktop; no-op for
                    # Cowork (acceptable: the migration purged orphans;
                    # any post-migration Cowork upsert wrote conv_uuid
                    # so the primary lookup above succeeds).
                    conv_uuid = file_path.stem
                self._write_conn.execute(
                    "DELETE FROM messages WHERE conv_uuid = ?", (conv_uuid,)
                )
                # v10: same uuid scope applies to the projection;
                # leave-no-trace contract matches delete_conversation.
                self._write_conn.execute(
                    "DELETE FROM conversations WHERE conv_uuid = ?",
                    (conv_uuid,),
                )
                self._write_conn.execute(
                    "DELETE FROM indexed_files WHERE path = ?", (str(file_path),)
                )

    def needs_update(self, file_path: Path, current_mtime: float) -> bool:
        """True if the file isn't indexed or its mtime has changed since.

        Threading: uses the per-thread read connection so cross-thread
        callers (the projects-dir Timer; asyncio.to_thread workers)
        don't share ``_write_conn`` with the writer.
        """
        conn = self._get_read_conn()
        cur = conn.execute(
            "SELECT mtime FROM indexed_files WHERE path = ?", (str(file_path),)
        )
        row = cur.fetchone()
        if row is None:
            return True
        # mtime equality with float tolerance — if the file was rewritten
        # within the same nanosecond we'd theoretically miss it, but in
        # practice the watcher poll interval (5s) dwarfs any plausible
        # mtime collision.
        return float(row[0]) != float(current_mtime)

    def list_indexed_paths(self) -> list[Path]:
        """All paths currently recorded in ``indexed_files``.

        Threading: uses the per-thread read connection so it's safe
        to call from a watchdog Timer thread or an asyncio.to_thread
        worker without contending with the writer for ``_write_conn``.
        """
        conn = self._get_read_conn()
        cur = conn.execute("SELECT path FROM indexed_files")
        return [Path(row[0]) for row in cur.fetchall()]

    def _read_indexed_files_map(self) -> dict[str, float]:
        """Snapshot of every ``indexed_files`` row as ``{path: mtime}``.

        One-shot bulk read used by :func:`_drift_first_scan` instead
        of per-file ``needs_update`` calls; saves N SQL round-trips
        and (critically) routes the query through the per-thread
        read connection so cross-thread callers (the projects-dir
        Timer; asyncio.to_thread workers) don't share ``_write_conn``
        with the writer or with each other. Returns ``{}`` if the
        table is empty.
        """
        conn = self._get_read_conn()
        cur = conn.execute("SELECT path, mtime FROM indexed_files")
        return {row[0]: row[1] for row in cur.fetchall()}

    def clear_all(self) -> None:
        """Wipe all rows. Caller is responsible for a subsequent rebuild.

        v12 (2026-05-25): also truncates the v10 ``conversations``
        projection. Pre-v12 ``clear_all`` deleted ``messages`` +
        ``indexed_files`` only, leaking projection rows for any
        conversations whose source file later disappeared between
        the wipe and the subsequent rebuild. A subsequent
        :func:`build_full_index` repopulated the projection for
        files still present (via INSERT OR REPLACE), so the leak
        was partly self-healing — but the title sweep would still
        surface deleted-from-disk conversations until the next
        watcher cleanup pass caught them.
        """
        with self._write_lock:
            with self._write_conn:
                self._write_conn.execute("DELETE FROM messages")
                self._write_conn.execute("DELETE FROM indexed_files")
                self._write_conn.execute("DELETE FROM conversations")

    # ----- query -----------------------------------------------------

    def query(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_compactions: bool = True,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Run an FTS5 MATCH query and return matched message metadata.

        Returns a list of dicts with keys: ``conv_uuid``, ``message_uuid``,
        ``sender``, ``created_at``. Body text is NOT returned — the caller
        re-loads the conversation from FileCache and runs the existing
        Python snippet logic on it (Scatter-Gather: FTS5 is a pre-filter).

        Filters (all AND'd with the MATCH clause):
          * source: "all" | "CLAUDE_AI" | "CLAUDE_CODE"
          * conversation_uuid: most-specific scope; wins over
            project_path / bookmarks / conversation_uuids
          * project_path: exact match against the conv's project_path
          * bookmarks: restrict to UUIDs in this set
          * organization_id (2026-05-14): workspace gate; UNINDEXED
            equality. None means "no constraint".
          * conversation_uuids (2026-05-14): active-filter set gate.
            Pushed into SQL via a TEMP TABLE join — NOT a Python post-
            filter — to avoid the `top-N-bm25 + post-filter = silent
            drop` correctness bug Council flagged. Empty set returns
            [] immediately. SQLite's SQLITE_MAX_VARIABLE_NUMBER (often
            999) is dodged by the TEMP table approach; bm25 ranking is
            preserved within the allowed set.
        """
        match_expr = translate_query(user_query)
        if not match_expr:
            return []

        # Empty active-filter set → empty results. (Distinct from None
        # which means "no constraint".) This is also short-circuited at
        # the search.py entry point but we re-check here for direct
        # callers of SearchIndex.query.
        if conversation_uuids is not None and not conversation_uuids:
            return []

        clauses: list[str] = ["messages MATCH ?"]
        params: list[Any] = [match_expr]

        # Whether to populate + join allowed_conv. The TEMP table is
        # per-connection (threading.local); _populate_allowed_conv
        # DROPs and recreates it idempotently.
        use_allowed_join = False

        if conversation_uuid is not None:
            clauses.append("conv_uuid = ?")
            params.append(conversation_uuid)
        else:
            if project_path is not None:
                clauses.append("project_path = ?")
                params.append(project_path)
            if bookmarks is not None:
                if not bookmarks:
                    return []
                placeholders = ",".join("?" * len(bookmarks))
                clauses.append(f"conv_uuid IN ({placeholders})")
                params.extend(sorted(bookmarks))
            if conversation_uuids is not None:
                # NOT an IN(?, ?, ...) — that hits SQLITE_MAX_VARIABLE_NUMBER
                # (often 999) on large active-filter sets. Use a TEMP
                # TABLE join instead. bm25 ranking is preserved within
                # the allowed set, which fixes the LIMIT-5000-drift
                # correctness bug Council flagged.
                use_allowed_join = True
                clauses.append("conv_uuid IN (SELECT uuid FROM allowed_conv)")

        if source != "all":
            clauses.append("source = ?")
            params.append(source)
        if organization_id is not None:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        # v14 (2026-05-26): same is_compaction_summary gate the
        # ``_build_match_where_clause`` helper applies on the snippet
        # path. The legacy ``query()`` predates the helper, so we
        # mirror the clause here. Without this, the slow
        # ``_search_via_index`` path would still surface compaction-
        # summary message-body hits even with Show Compactions OFF
        # (the same vector v13 closed on the fast paths).
        if not include_compactions:
            clauses.append("is_compaction_summary = 0")

        sql = (
            "SELECT conv_uuid, message_uuid, sender, created_at "
            "FROM messages "
            f"WHERE {' AND '.join(clauses)} "
            # bm25() returns negative floats; lower (more negative) = more
            # relevant. ASC order yields top-relevance first.
            "ORDER BY bm25(messages) "
            "LIMIT ?"
        )
        params.append(int(limit))

        conn = self._get_read_conn()
        if use_allowed_join:
            # Populate the TEMP TABLE on this connection BEFORE the
            # query references it. The title-sweep in search._search_via_index
            # also calls _populate_allowed_conv on the same connection
            # (idempotent — reuses the same TEMP table).
            assert conversation_uuids is not None  # narrowed above
            self._populate_allowed_conv(conn, conversation_uuids)

        cur = conn.execute(sql, tuple(params))
        return [
            {
                "conv_uuid": row[0],
                "message_uuid": row[1],
                "sender": row[2],
                "created_at": row[3],
            }
            for row in cur.fetchall()
        ]

    # FTS5 snippet() marker pair. The marks are intentionally OBSCURE
    # (no HTML, no characters that appear in natural prose) so the
    # Python parser can split on them deterministically without an
    # escape pass. The literal byte sequence ``\x01\x01`` would also
    # work but the printable form is easier to grep in test failures.
    _SNIPPET_OPEN = "\u0001\u0001MARK\u0001\u0001"
    _SNIPPET_CLOSE = "\u0001\u0001/MARK\u0001\u0001"
    # FTS5 snippet() args: (table, column_index, open, close,
    # ellipsis, max_tokens). column_index is the position in the
    # messages FTS5 schema (0-indexed). Sweep is bm25-driven so we
    # get the densest match cluster across multi-token queries.
    # v13 schema column order: conv_uuid(0), message_uuid(1), sender(2),
    # created_at(3), source(4), project_path(5), organization_id(6),
    # conv_created_at(7), conv_updated_at(8), is_compaction_summary(9),
    # title(10), body(11), body_text(12). The indices shifted by +1 vs
    # v12 when is_compaction_summary was inserted before title.
    _SNIPPET_BODY_COL_IDX = 11
    _SNIPPET_BODY_TEXT_COL_IDX = 12
    _SNIPPET_TITLE_COL_IDX = 10
    _SNIPPET_ELLIPSIS = "..."
    _SNIPPET_MAX_TOKENS = 30  # ~150 chars for English prose

    def _build_match_where_clause(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_tool_calls: bool = True,
        include_compactions: bool = True,
    ) -> tuple[str, list[Any], bool] | None:
        """Shared WHERE-clause builder for FTS5 MATCH queries.

        Returns ``(where_sql_without_WHERE, params, use_allowed_join)``,
        or ``None`` if the query short-circuits to empty results (empty
        query, empty bookmarks, empty conversation_uuids).

        Risk #5 in the plan: this helper is the single source of truth
        for the MATCH expression + scope filters so ``count_matches``
        and ``query_with_snippets`` can NEVER drift on what they're
        matching against. Both call this and stitch the SELECT / ORDER /
        LIMIT around the returned WHERE.

        2026-05-16 (v7): the ``include_tool_calls`` flag selects which
        body column the MATCH expression targets. Column-scoped MATCH
        syntax (``{body_text}:(...)``) excludes hits whose only token
        lives inside a hidden tool block — exact parity with the
        linear-scan path under ``Tools off``.
        """
        match_expr = translate_query(user_query)
        if not match_expr:
            return None

        if conversation_uuids is not None and not conversation_uuids:
            return None

        # Column-scoped MATCH. Wrap the translated expression in
        # parentheses so AND-of-tokens binds tighter than the column
        # qualifier (FTS5 parses ``col:foo AND bar`` as
        # ``col:foo AND bar`` — the AND clause loses the column
        # qualifier on the right side). With explicit grouping
        # ``col:(foo AND bar)`` both sides honor the column.
        column = "body_text" if not include_tool_calls else "body"
        scoped_match = f"{{{column}}} : ({match_expr})"

        clauses: list[str] = ["messages MATCH ?"]
        params: list[Any] = [scoped_match]
        use_allowed_join = False

        if conversation_uuid is not None:
            clauses.append("conv_uuid = ?")
            params.append(conversation_uuid)
        else:
            if project_path is not None:
                clauses.append("project_path = ?")
                params.append(project_path)
            if bookmarks is not None:
                if not bookmarks:
                    return None
                placeholders = ",".join("?" * len(bookmarks))
                clauses.append(f"conv_uuid IN ({placeholders})")
                params.extend(sorted(bookmarks))
            if conversation_uuids is not None:
                use_allowed_join = True
                clauses.append("conv_uuid IN (SELECT uuid FROM allowed_conv)")

        if source != "all":
            clauses.append("source = ?")
            params.append(source)
        if organization_id is not None:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        # v13 (2026-05-26): compaction-aware filter. When the user has
        # "Show Compactions" OFF in the conversation header, the wire
        # carries ``include_compactions=False``. Filtering at SQL —
        # BEFORE bm25 ranking and LIMIT — mirrors the include_tool_calls
        # treatment of body_text. Council rejected the alternative
        # (post-filter at scatter time) because top-bm25 hits can
        # cluster in compaction summaries (they're long, prose-heavy
        # bodies), which would empty the result set under LIMIT.
        # See PLANS / Decision Records 2026-05-26.
        if not include_compactions:
            clauses.append("is_compaction_summary = 0")

        return " AND ".join(clauses), params, use_allowed_join

    def query_with_snippets(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_tool_calls: bool = True,
        include_compactions: bool = True,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """FTS5 fast path with body snippets in-band.

        Same filter/scope semantics as :meth:`query` but each row also
        carries ``body_snippet`` (the FTS5 ``snippet()`` output for
        the body column) AND the conversation-level metadata
        (``title``, ``project_path``, ``organization_id``,
        ``source``) so the caller can build ``SearchResult`` objects
        without re-reading any JSON/JSONL.

        Marker characters in ``body_snippet``:
          * ``\\x01\\x01MARK\\x01\\x01`` — opens a highlighted span.
          * ``\\x01\\x01/MARK\\x01\\x01`` — closes the span.

        The caller parses these into ``SnippetFragment`` objects.
        We use non-printable sentinels (not HTML ``<mark>``) so the
        parser can split deterministically without an escape pass
        and so accidental ``<mark>`` text in user content can never
        be confused for a real highlight marker.

        LIMIT 1000 (down from 5000):
          FTS5 ``snippet()`` is the dominant cost in this query —
          ~140 µs per row for a typical hit (FTS5 has to scan the
          body column to locate token positions). At 5000 rows
          that's ~700 ms; at 1000 it's ~140 ms.

          The cap distributes across conversations naturally: a
          query that hits 100 conversations gets ~10 snippets per
          conv, plenty for the UI's "first 3 + show N more"
          affordance. A query that hits 5 conversations gets ~200
          snippets per conv, far more than any UI surfaces.

          A two-pass strategy (fetch top-N rowids cheap; snippet
          only the chosen rowids) was prototyped and was SLOWER
          than the single-pass with smaller LIMIT — combining
          ``rowid IN (?, ?, ...) AND messages MATCH ?`` forced
          FTS5 to scan with both predicates, defeating the win.
          Single-pass with bounded LIMIT is both simpler and
          faster on this index shape.

        ``include_tool_calls=False`` (2026-05-16, v7):
          Column-scoped MATCH targets ``body_text`` instead of
          ``body``. body_text excludes tool_use / tool_result, so a
          hit whose only token lives inside a hidden tool block is
          dropped at MATCH time — same semantics as the linear-scan
          path's runtime filter, but without the post-hoc Python
          walk. The corresponding ``snippet()`` call uses the
          body_text column too, so the highlighted span comes from
          the text the user can actually see.
        """
        built = self._build_match_where_clause(
            user_query,
            source=source, conversation_uuid=conversation_uuid,
            project_path=project_path, bookmarks=bookmarks,
            organization_id=organization_id,
            conversation_uuids=conversation_uuids,
            include_tool_calls=include_tool_calls,
            include_compactions=include_compactions,
        )
        if built is None:
            return []
        where_sql, params, use_allowed_join = built

        body_col_idx = (
            self._SNIPPET_BODY_COL_IDX if include_tool_calls
            else self._SNIPPET_BODY_TEXT_COL_IDX
        )
        body_snippet_expr = (
            f"snippet(messages, {body_col_idx}, "
            f"?, ?, ?, ?)"
        )
        snippet_params = [
            self._SNIPPET_OPEN,
            self._SNIPPET_CLOSE,
            self._SNIPPET_ELLIPSIS,
            self._SNIPPET_MAX_TOKENS,
        ]

        sql = (
            "SELECT conv_uuid, message_uuid, sender, created_at, "
            "       title, project_path, organization_id, source, "
            "       conv_created_at, conv_updated_at, "
            f"      {body_snippet_expr} "
            "FROM messages "
            f"WHERE {where_sql} "
            "ORDER BY bm25(messages) "
            "LIMIT ?"
        )
        full_params: list[Any] = list(snippet_params) + list(params) + [int(limit)]

        conn = self._get_read_conn()
        if use_allowed_join:
            assert conversation_uuids is not None
            self._populate_allowed_conv(conn, conversation_uuids)

        cur = conn.execute(sql, tuple(full_params))
        return [
            {
                "conv_uuid": row[0],
                "message_uuid": row[1],
                "sender": row[2],
                "created_at": row[3],
                "title": row[4],
                "project_path": row[5],
                "organization_id": row[6],
                "source": row[7],
                "conv_created_at": row[8],
                "conv_updated_at": row[9],
                "body_snippet": row[10],
            }
            for row in cur.fetchall()
        ]

    def query_with_full_body(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_tool_calls: bool = True,
        include_compactions: bool = True,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """FTS5 fast path that returns the FULL body text in-band.

        Mirrors :meth:`query_with_snippets` but selects the indexed
        ``body``/``body_text`` column directly instead of calling
        ``snippet()``. Used by the ``context_size='full'`` fast path
        (:func:`backend.search._search_via_index_fast_full`) to avoid
        the slow corpus walk that was the cold-cache "tens of seconds"
        bottleneck (2026-05-22 perf fix).

        Bytes-on-wire warning: a body can be 100KB+ for large
        conversations. The LIMIT (1000 by default) caps the worst-
        case response payload at ~100MB. The HTTP transport plus
        the frontend's already-deployed contextSize toggle render
        path absorb that volume fine for the current corpus, but if
        you raise the LIMIT you should also revisit the FastAPI
        response-streaming story.

        ``include_tool_calls=False`` returns ``body_text`` (the same
        text-only projection ``snippet()`` would target under the
        same flag). Otherwise returns ``body`` (full content
        including tool blocks).
        """
        built = self._build_match_where_clause(
            user_query,
            source=source, conversation_uuid=conversation_uuid,
            project_path=project_path, bookmarks=bookmarks,
            organization_id=organization_id,
            conversation_uuids=conversation_uuids,
            include_tool_calls=include_tool_calls,
            include_compactions=include_compactions,
        )
        if built is None:
            return []
        where_sql, params, use_allowed_join = built

        body_col = "body" if include_tool_calls else "body_text"

        sql = (
            "SELECT conv_uuid, message_uuid, sender, created_at, "
            "       title, project_path, organization_id, source, "
            "       conv_created_at, conv_updated_at, "
            f"      {body_col} "
            "FROM messages "
            f"WHERE {where_sql} "
            "ORDER BY bm25(messages) "
            "LIMIT ?"
        )
        full_params: list[Any] = list(params) + [int(limit)]

        conn = self._get_read_conn()
        if use_allowed_join:
            assert conversation_uuids is not None
            self._populate_allowed_conv(conn, conversation_uuids)

        cur = conn.execute(sql, tuple(full_params))
        return [
            {
                "conv_uuid": row[0],
                "message_uuid": row[1],
                "sender": row[2],
                "created_at": row[3],
                "title": row[4],
                "project_path": row[5],
                "organization_id": row[6],
                "source": row[7],
                "conv_created_at": row[8],
                "conv_updated_at": row[9],
                "body": row[10] or "",
            }
            for row in cur.fetchall()
        ]

    def count_matches(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_tool_calls: bool = True,
        include_compactions: bool = True,
    ) -> int:
        """COUNT(*) of FTS5 MATCH rows under the same WHERE clauses as
        ``query_with_snippets``.

        ~5-10 ms on a 13k-row index — FTS5 walks the inverted lists for
        the matched tokens and the WHERE-clause UNINDEXED filters happen
        on the matched rowids. No ``snippet()`` call, no ORDER BY, no
        LIMIT. The result drives the truncation envelope on the
        /api/search response so the UI can render "Showing first N of M"
        without a second round-trip.

        Risk #5 (plan): shares the WHERE-clause builder with
        ``query_with_snippets`` via ``_build_match_where_clause``, so
        the two queries can NEVER drift on what they're matching
        against. The shared helper is the single source of truth for
        scope filters + the column-scoped MATCH expression.
        """
        built = self._build_match_where_clause(
            user_query,
            source=source, conversation_uuid=conversation_uuid,
            project_path=project_path, bookmarks=bookmarks,
            organization_id=organization_id,
            conversation_uuids=conversation_uuids,
            include_tool_calls=include_tool_calls,
            include_compactions=include_compactions,
        )
        if built is None:
            return 0
        where_sql, params, use_allowed_join = built

        sql = f"SELECT COUNT(*) FROM messages WHERE {where_sql}"

        conn = self._get_read_conn()
        if use_allowed_join:
            assert conversation_uuids is not None
            self._populate_allowed_conv(conn, conversation_uuids)
        cur = conn.execute(sql, tuple(params))
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def title_match_snippets(
        self,
        user_query: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_compactions: bool = True,
    ) -> dict[str, str]:
        """Return ``{conv_uuid: title_snippet}`` for conversations whose
        TITLE matched the query as a LIKE substring.

        Mirrors the title-sweep in ``_search_via_index`` but produces
        the marked snippet at SQL time. The output substring is
        wrapped in the SAME ``\\x01\\x01MARK\\x01\\x01`` /
        ``\\x01\\x01/MARK\\x01\\x01`` sentinels so the caller can
        parse fragments with the same code path as ``body_snippet``.

        Why we don't reuse FTS5's ``snippet()`` for titles: the
        ``title`` column is FTS5-indexed (so MATCH works) but a
        LIKE-based substring sweep is what catches mid-token
        substrings (e.g. "edul" inside "scheduled") that the
        porter+unicode61 tokenizer rejects. The Python wrapper here
        builds the marked snippet by hand from a case-insensitive
        find() — identical semantics to the legacy linear-scan
        title sweep.
        """
        stripped = user_query.strip()
        if not stripped:
            return {}

        # Phrase-mode handling mirrors backend.search.parse_user_query:
        # when the whole query is wrapped in double quotes, treat the
        # quoted phrase as the literal title needle. Otherwise the
        # full string is the needle (matches linear-scan policy).
        if len(stripped) >= 3 and stripped[0] == '"' and stripped[-1] == '"':
            inner = stripped[1:-1].strip()
            needle = inner if inner else stripped
        else:
            needle = stripped

        title_clauses: list[str] = ["title LIKE ?"]
        title_params: list[Any] = [f"%{needle}%"]
        use_allowed_join = False

        if conversation_uuid is not None:
            title_clauses.append("conv_uuid = ?")
            title_params.append(conversation_uuid)
        else:
            if project_path is not None:
                title_clauses.append("project_path = ?")
                title_params.append(project_path)
            if bookmarks is not None:
                if not bookmarks:
                    return {}
                placeholders = ",".join("?" * len(bookmarks))
                title_clauses.append(f"conv_uuid IN ({placeholders})")
                title_params.extend(sorted(bookmarks))
            if conversation_uuids is not None:
                if not conversation_uuids:
                    return {}
                use_allowed_join = True
                title_clauses.append("conv_uuid IN (SELECT uuid FROM allowed_conv)")
        if source != "all":
            title_clauses.append("source = ?")
            title_params.append(source)
        if organization_id is not None:
            title_clauses.append("organization_id = ?")
            title_params.append(organization_id)
        # v14 (2026-05-26): compaction-titled gate. When the user has
        # Show Compactions OFF (``include_compactions=False``), suppress
        # title-only hits whose conversation TITLE is the canonical
        # compaction-summary prefix (fallback-derived from the first
        # 100 chars of an isCompactSummary row body when no
        # summary/custom-title/agent-name row was emitted). The flag
        # is per-conversation, populated in :meth:`upsert_conversation`
        # via :func:`is_compaction_prefix_text` and backfilled by the
        # v13→v14 fast migration. The conv's BODY matches still surface
        # via the separate body-MATCH path (see
        # ``_build_match_where_clause``'s ``is_compaction_summary = 0``
        # gate) — this filter only suppresses the title pseudo-message.
        if not include_compactions:
            title_clauses.append("is_compaction_titled = 0")

        # Per-conv metadata (timestamps, project_path) returned alongside
        # the marked title so the caller can build SearchResult objects
        # for title-only hits without loading the conversation body.
        #
        # v10 (2026-05-23): scan the ``conversations`` projection (1
        # row per conv, ~hundreds of rows) instead of the FTS5 ``messages``
        # virtual table (250K rows / 2.5GB). conv_uuid is the PK so
        # GROUP BY is unnecessary — every match is already unique.
        # See SCHEMA_VERSION=10 docstring for the cold-search bench.
        sql = (
            "SELECT conv_uuid, title, conv_created_at, conv_updated_at, "
            "       project_path, source, organization_id "
            "FROM conversations "
            f"WHERE {' AND '.join(title_clauses)}"
        )
        conn = self._get_read_conn()
        if use_allowed_join:
            assert conversation_uuids is not None
            self._populate_allowed_conv(conn, conversation_uuids)
        try:
            cur = conn.execute(sql, tuple(title_params))
            rows = cur.fetchall()
        except sqlite3.Error:
            logger.exception("search_index: title sweep failed")
            return {}

        out: dict[str, dict[str, Any]] = {}
        needle_lower = needle.lower()
        for conv_uuid, title, c_created, c_updated, proj, src, org in rows:
            if not title:
                continue
            tlow = title.lower()
            idx = tlow.find(needle_lower)
            if idx < 0:
                continue  # SQL caught case-folded but Python str.find missed? defensive
            marked = (
                title[:idx]
                + self._SNIPPET_OPEN
                + title[idx:idx + len(needle)]
                + self._SNIPPET_CLOSE
                + title[idx + len(needle):]
            )
            out[conv_uuid] = {
                "title": title,
                "marked_title": marked,
                "conv_created_at": c_created,
                "conv_updated_at": c_updated,
                "project_path": proj,
                "source": src,
                "organization_id": org,
            }
        return out

    def title_match_uuids(
        self,
        needle: str,
        *,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        conversation_uuid: str | None = None,
        project_path: str | None = None,
        bookmarks: set[str] | None = None,
        organization_id: str | None = None,
        conversation_uuids: set[str] | None = None,
        include_compactions: bool = True,
    ) -> set[str]:
        """Return the set of ``conv_uuid`` whose ``title`` contains
        ``needle`` as a case-insensitive LIKE substring, honoring all
        the same scope filters as :meth:`query`.

        Council A1, 2026-05-21: extracted as a public method so
        ``backend/search.py`` no longer reaches into private internals
        (``_get_read_conn`` + ``_populate_allowed_conv``). The body of
        this method preserves the exact SQL shape and semantics of the
        original inline reach-through — including the ``SELECT
        DISTINCT conv_uuid`` shape and the per-connection
        ``allowed_conv`` TEMP table population — so the
        :func:`_search_via_index` title sweep is byte-for-byte
        equivalent to the pre-refactor behavior.

        Unlike :meth:`title_match_snippets`, this method does NOT
        re-parse the query for quoted-phrase semantics — callers pass
        the already-extracted needle (the result of
        ``parse_user_query()``). It also returns only UUIDs (not
        snippets), which is what the legacy ``context_size="full"``
        path needs.

        Returns ``set()`` on:
          * empty / whitespace-only ``needle``;
          * ``conversation_uuids=set()`` (explicit "nothing allowed");
          * ``bookmarks=set()`` (explicit "nothing allowed");
          * SQL error (defensive — the body sweep still wins).
        """
        if not needle or not needle.strip():
            return set()

        title_sql_clauses: list[str] = ["title LIKE ?"]
        title_sql_params: list[Any] = [f"%{needle}%"]
        use_allowed_join = False

        if conversation_uuid is not None:
            title_sql_clauses.append("conv_uuid = ?")
            title_sql_params.append(conversation_uuid)
        else:
            if project_path is not None:
                title_sql_clauses.append("project_path = ?")
                title_sql_params.append(project_path)
            if bookmarks is not None:
                if not bookmarks:
                    return set()
                placeholders = ",".join("?" * len(bookmarks))
                title_sql_clauses.append(f"conv_uuid IN ({placeholders})")
                title_sql_params.extend(sorted(bookmarks))
            if conversation_uuids is not None:
                if not conversation_uuids:
                    return set()
                use_allowed_join = True
                title_sql_clauses.append(
                    "conv_uuid IN (SELECT uuid FROM allowed_conv)"
                )
        if source != "all":
            title_sql_clauses.append("source = ?")
            title_sql_params.append(source)
        if organization_id is not None:
            title_sql_clauses.append("organization_id = ?")
            title_sql_params.append(organization_id)
        # v14 (2026-05-26): mirror of the gate in
        # :meth:`title_match_snippets`. See that method for rationale.
        if not include_compactions:
            title_sql_clauses.append("is_compaction_titled = 0")

        try:
            conn = self._get_read_conn()
            if use_allowed_join:
                assert conversation_uuids is not None
                self._populate_allowed_conv(conn, conversation_uuids)
            # v10 (2026-05-23): same projection-table switch as
            # title_match_snippets. conv_uuid PK ⇒ DISTINCT unneeded.
            sql = (
                "SELECT conv_uuid FROM conversations "
                f"WHERE {' AND '.join(title_sql_clauses)}"
            )
            cur = conn.execute(sql, tuple(title_sql_params))
            return {row[0] for row in cur.fetchall()}
        except sqlite3.Error:
            # Fall back to empty — body matches still win.
            logger.exception("search_index: title_match_uuids sweep failed")
            return set()

    def _populate_allowed_conv(
        self, conn: sqlite3.Connection, uuids: set[str]
    ) -> None:
        """Idempotent populate of the per-connection TEMP TABLE
        ``allowed_conv(uuid TEXT PRIMARY KEY)``.

        Drops + recreates + executemany-inserts. This is called per query
        when ``conversation_uuids`` is set; per-connection means safe
        across threads (each thread has its own threading.local read conn).
        SQLite TEMP tables have ~zero file overhead; the PRIMARY KEY gives
        O(log n) for the JOIN.

        Spec §2 (2026-05-14, Council convergence): we do NOT use
        ``IN (?, ?, ..., ?N)`` because SQLITE_MAX_VARIABLE_NUMBER is
        often 999 on Linux distro builds — 1500-conv corpora would
        error out. The TEMP table avoids that limit AND preserves bm25
        ranking within the allowed set, which fixes the
        ``LIMIT 5000 + post-filter = silent drop`` correctness bug.
        """
        # DROP IF EXISTS then CREATE — order matters. We can't use
        # CREATE TEMP TABLE IF NOT EXISTS + DELETE because if a prior
        # call left rows in place (e.g., the test reused the same
        # connection across cases), DELETE would still need to fire
        # before INSERT, and the round-trip cost is the same as
        # DROP+CREATE. Single-statement is clearer.
        conn.execute("DROP TABLE IF EXISTS allowed_conv")
        conn.execute(
            "CREATE TEMP TABLE allowed_conv (uuid TEXT PRIMARY KEY)"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO allowed_conv (uuid) VALUES (?)",
            [(u,) for u in uuids],
        )

    def run_pragma_optimize(self) -> None:
        """Run ``PRAGMA optimize`` on the write connection.

        SQLite recommends this after large schema changes / bulk inserts —
        the pragma checks whether per-table statistics are stale and runs
        ``ANALYZE`` selectively where it would help the query planner. On
        a fresh full FTS5 rebuild the gain is real: the first search after
        a cold restart otherwise plans against empty stats.

        Goes through ``_write_lock`` because the pragma may write to
        ``sqlite_stat1``; running it without the lock could race with a
        concurrent upsert and trigger ``database is locked``.
        """
        with self._write_lock:
            self._write_conn.execute("PRAGMA optimize")
            self._write_conn.commit()

    def stats(self) -> dict[str, int]:
        """Return basic index size counters for diagnostics."""
        cur = self._write_conn.execute("SELECT COUNT(*) FROM messages")
        msg_count = cur.fetchone()[0]
        cur = self._write_conn.execute("SELECT COUNT(*) FROM indexed_files")
        file_count = cur.fetchone()[0]
        return {"messages": msg_count, "files": file_count}

    def close(self) -> None:
        """Close all connections. Idempotent."""
        try:
            self._write_conn.close()
        except sqlite3.Error:
            pass
        # threading.local cleanup happens when the thread dies; we can't
        # walk it from here. Per-thread connections are GC'd at thread end.


# ----- module-level singleton --------------------------------------

# Module-level singleton, mirrors the FileCache pattern in cache.py.
# Set to None when FTS5 isn't available so callers know to fall back.
_search_index: SearchIndex | None = None
_search_index_lock = threading.Lock()


def get_search_index() -> SearchIndex | None:
    """Return the process-wide SearchIndex, or None if FTS5 is unavailable.

    Lazy-initializes on first call. Returns the same instance on every
    subsequent call within this process. Test code may call
    :func:`reset_search_index_for_tests` to reset between tests.
    """
    global _search_index
    if _search_index is not None:
        return _search_index

    with _search_index_lock:
        if _search_index is not None:
            return _search_index
        if not fts5_available():
            logger.warning(
                "search_index: FTS5 not available in this sqlite3 build; "
                "search will use linear-scan fallback"
            )
            return None
        try:
            _search_index = SearchIndex(default_index_path())
        except sqlite3.Error as exc:
            logger.error("search_index: failed to open index: %s", exc, exc_info=True)
            return None
    return _search_index


def reset_search_index_for_tests() -> None:
    """Test-only: reset the module-level singleton.

    Production code MUST NOT call this. Used by pytest fixtures so each
    test starts with a fresh index pointed at its own tmp_path.

    Best-effort close: tests sometimes inject mock objects that don't
    implement ``close()``; we tolerate AttributeError so the fixture
    teardown doesn't crash.
    """
    global _search_index
    if _search_index is not None:
        try:
            _search_index.close()
        except (AttributeError, sqlite3.Error):
            pass
        _search_index = None


# ----- bulk indexing -----------------------------------------------


def _file_path_for_conv(conv: dict[str, Any], data_dir: Path, claude_dir: Path) -> Path | None:
    """Resolve the on-disk path for a conversation dict.

    For Desktop conversations: ``data_dir/by-org/<org>/<uuid>.json`` or
    legacy ``data_dir/<uuid>.json``. For CC sessions: walk
    ``claude_dir/projects/<encoded-cwd>/<uuid>.jsonl``.

    Returns None if no plausible path is found — the conversation will
    still be indexed (we use a synthetic path) but drift detection won't
    fire on it. This shouldn't happen for production data.
    """
    uuid = conv.get("uuid", "")
    if not uuid:
        return None

    source = conv.get("source", "CLAUDE_AI")
    if source == "CLAUDE_CODE":
        # CC files: claude_dir/projects/<encoded-cwd>/<uuid>.jsonl
        projects_dir = claude_dir / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                candidate = project_dir / f"{uuid}.jsonl"
                if candidate.exists():
                    return candidate
        return None

    if source == "CLAUDE_COWORK":
        # Cowork files: claude_desktop_app_dir/local-agent-mode-sessions/
        # <deployment>/<org>/local_<uuid>/audit.jsonl. We don't have the
        # cowork_root in this helper's signature (call sites are pinned
        # to data_dir + claude_dir for back-compat), so consult
        # Settings directly. Drift detection on Cowork without a tagged
        # path can fall back to the next backstop poll, so a missing
        # cowork_root just returns None.
        cowork_root = (
            get_settings().claude_desktop_app_dir / "local-agent-mode-sessions"
        )
        if cowork_root.exists():
            try:
                deployment_dirs = list(cowork_root.iterdir())
            except OSError:
                deployment_dirs = []
            for deployment_dir in deployment_dirs:
                if not deployment_dir.is_dir():
                    continue
                try:
                    org_dirs = list(deployment_dir.iterdir())
                except OSError:
                    continue
                for org_dir in org_dirs:
                    candidate = org_dir / f"local_{uuid}" / "audit.jsonl"
                    if candidate.exists():
                        return candidate
        return None

    # Desktop: by-org first, legacy flat last.
    by_org = data_dir / "by-org"
    if by_org.exists():
        for org_dir in by_org.iterdir():
            candidate = org_dir / f"{uuid}.json"
            if candidate.exists():
                return candidate
    legacy = data_dir / f"{uuid}.json"
    if legacy.exists():
        return legacy
    return None


def _enumerate_conversation_paths(store: Any) -> list[tuple[Path, str]]:
    """Stat-only enumeration of every on-disk conversation file.

    Returns ``[(path, source), ...]`` where ``source`` is one of
    ``"CLAUDE_AI"``, ``"CLAUDE_CODE"``, or ``"CLAUDE_COWORK"``. NO
    content is loaded — we only need the file list and (later)
    ``os.stat`` for mtime.

    Uses the existing path-discovery helpers
    (:meth:`ConversationStore._get_conversation_files` for Desktop and
    :func:`backend.claude_code_reader.discover_jsonl_files` for CC) so
    this stays the single source of truth for "what counts as a
    conversation file on disk."

    Cowork (2026-05-25): walks ``cowork_root / <deployment>/<org>/
    local_*/audit.jsonl``. Since Cowork's audit.jsonl shares the
    ``.jsonl`` extension with CC, the source tag in the returned
    tuple is LOAD-BEARING for :func:`_load_conversation_at` — that
    dispatcher routes on the tag, not the extension.
    """
    from .claude_code_reader import discover_jsonl_files

    paths: list[tuple[Path, str]] = []
    # Desktop JSONs (by-org + legacy flat, with dedup).
    for p in store._get_conversation_files():
        paths.append((p, "CLAUDE_AI"))
    # CC JSONLs.
    claude_dir = getattr(store, "claude_dir", None) or get_settings().claude_dir
    for p in discover_jsonl_files(claude_dir):
        paths.append((p, "CLAUDE_CODE"))
    # Cowork audit.jsonl files (always tagged CLAUDE_COWORK so the
    # source-tag dispatch in _load_conversation_at routes correctly —
    # extension-based dispatch would silently route Cowork through
    # the CC reader, which doesn't understand the _audit_timestamp
    # field rename).
    cowork_root = getattr(store, "cowork_root", None)
    if cowork_root is None:
        cowork_root = (
            get_settings().claude_desktop_app_dir / "local-agent-mode-sessions"
        )
    if cowork_root.exists():
        try:
            deployment_dirs = list(cowork_root.iterdir())
        except OSError:
            deployment_dirs = []
        for deployment_dir in deployment_dirs:
            if not deployment_dir.is_dir():
                continue
            try:
                org_dirs = list(deployment_dir.iterdir())
            except OSError:
                continue
            for org_dir in org_dirs:
                if not org_dir.is_dir():
                    continue
                try:
                    sess_dirs = list(org_dir.iterdir())
                except OSError:
                    continue
                for sess_dir in sess_dirs:
                    if sess_dir.is_dir() and sess_dir.name.startswith("local_"):
                        audit = sess_dir / "audit.jsonl"
                        if audit.exists():
                            paths.append((audit, "CLAUDE_COWORK"))
    return paths


def _load_conversation_at(
    path: Path, store: Any, source: str | None = None
) -> dict[str, Any] | None:
    """Load a single conversation's full content from its on-disk path.

    Dispatches by SOURCE TAG (when provided), not by file extension —
    Cowork's ``audit.jsonl`` shares the ``.jsonl`` extension with CC,
    so extension-based dispatch would silently route Cowork through
    the CC reader and corrupt every Cowork session in the index.

    Source dispatch:
      * ``CLAUDE_COWORK`` → :func:`backend.cowork_reader.read_cowork_conversation`
        (reads ``path.parent`` since the Cowork reader takes the
        session directory, not the audit.jsonl path).
      * ``CLAUDE_CODE`` → :func:`backend.claude_code_reader.read_claude_code_conversation`
        (CC streaming format; also runs the
        ``cache_all_markers`` image-warm side effect).
      * ``CLAUDE_AI`` (or any unrecognized source) →
        :meth:`ConversationStore._load_conversation` (Desktop JSON;
        mtime-cached via FileCache).

    Back-compat: when ``source`` is ``None`` (legacy callers that
    weren't updated when the source-tag dispatch landed), we fall
    back to the pre-Cowork extension-based behavior. New call sites
    MUST pass the source tag explicitly.

    Returns ``None`` on read failure (the caller logs and skips). The
    drift-first refactor calls this ONLY for paths the diff already
    identified as drifted, so a missing/corrupt file at this stage is
    rare and surfaces in logs.
    """
    if source == "CLAUDE_COWORK":
        from .cowork_reader import read_cowork_conversation
        try:
            return read_cowork_conversation(path.parent)
        except Exception:  # noqa: BLE001
            logger.exception("search_index: failed to read Cowork %s", path)
            return None

    if source == "CLAUDE_CODE" or (source is None and path.suffix.lower() == ".jsonl"):
        from .claude_code_reader import read_claude_code_conversation
        try:
            return read_claude_code_conversation(path)
        except Exception:  # noqa: BLE001
            logger.exception("search_index: failed to read CC %s", path)
            return None
    # Desktop JSON path — reuse the store's mtime-cached loader.
    try:
        return store._load_conversation(path)
    except Exception:  # noqa: BLE001
        logger.exception("search_index: failed to read Desktop %s", path)
        return None


def _drift_first_scan(
    store: Any, index: SearchIndex
) -> tuple[list[tuple[Path, str]], list[Path]]:
    """Diff the live file set against ``indexed_files`` WITHOUT loading
    content. Returns ``(drifted_paths, missing_paths)``.

    ``drifted_paths``: paths whose mtime no longer matches the indexed
    row, OR which aren't in ``indexed_files`` at all (new files /
    first install).

    ``missing_paths``: paths in ``indexed_files`` that no longer exist
    on disk. The caller deletes their rows via
    :meth:`SearchIndex.delete_by_path` (cleanup pass).

    Cost:
      * One ``os.stat`` per live path (~1 ms × 1,200 = 50–200 ms on
        SSD; possibly 1–2 s on slow network mounts).
      * One SELECT against ``indexed_files`` (full table dump into
        a Python dict) — 1.2k rows is ~10–30 ms.
      * One set diff for the missing pass.

    Threading:
      The SQL fetch goes through ``SearchIndex._read_indexed_files_map``,
      which uses the per-thread read connection (``threading.local``).
      Calling this helper from a watchdog Timer thread, an asyncio
      thread-pool thread, or the lifespan task all work; each thread
      gets its own SQLite handle on first call.

    Versus today's behavior (``get_all_conversations_raw`` walks every
    JSON/JSONL into memory): this drops warm-restart latency from
    ~10 s to ~100–300 ms.
    """
    live_paths_with_source = _enumerate_conversation_paths(store)
    live_set = {p for p, _ in live_paths_with_source}

    # Bulk-fetch the entire indexed_files table in one round-trip via
    # the per-thread read connection. The dict lookup below is O(1)
    # per live path and avoids the cross-thread sharing of _write_conn
    # that the old per-file needs_update() check had.
    indexed_mtimes = index._read_indexed_files_map()

    # Carry the source tag through with each drifted path — Cowork
    # dispatch in _load_conversation_at depends on it (extension
    # collides with CC's .jsonl).
    drifted: list[tuple[Path, str]] = []
    for path, source in live_paths_with_source:
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            # File vanished between enumeration and stat; ignore — the
            # next backstop pass will pick up the deletion via the
            # missing-pass below (path won't appear in live_set).
            continue
        indexed_mtime = indexed_mtimes.get(str(path))
        if indexed_mtime is None or float(indexed_mtime) != float(current_mtime):
            drifted.append((path, source))

    # Missing pass: any indexed_files row whose path is no longer on disk.
    missing: list[Path] = []
    for indexed_path_str in indexed_mtimes.keys():
        indexed_path = Path(indexed_path_str)
        if indexed_path not in live_set:
            missing.append(indexed_path)

    return drifted, missing


def build_full_index(
    store: Any,
    *,
    index: SearchIndex | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Walk every conversation and (re)populate the index.

    Idempotent — re-runs are no-ops for unchanged files because the
    drift-first scan returns an empty drifted set when ``indexed_files``
    is already in sync with disk.

    Returns ``(files_indexed, messages_indexed)``.

    Side effect: calls ``index.mark_ready()`` at the end so subsequent
    queries hit the index instead of falling back. The correctness
    invariant is that ``mark_ready()`` fires AFTER the drifted set has
    been absorbed, never before — otherwise FTS5 would serve stale
    rows between schema-rebuild and drift-absorption.
    """
    if index is None:
        index = get_search_index()
    if index is None:
        return (0, 0)

    drifted, missing = _drift_first_scan(store, index)

    # Cleanup pass first (cheap, no content reads).
    for path in missing:
        try:
            index.delete_by_path(path)
        except sqlite3.Error:
            logger.exception("search_index: cleanup-delete failed for %s", path)

    files_indexed = 0
    messages_indexed = 0
    total = len(drifted)
    for i, (path, source) in enumerate(drifted):
        # Hunt #8 TOCTOU fix: check-read-check (see update_drifted_files
        # for the full rationale). Stat before AND after the read; if
        # the file was mutated during the read, skip the upsert so the
        # index never stamps stale content with a fresh mtime.
        try:
            mtime_before = path.stat().st_mtime
        except OSError:
            mtime_before = None
        conv = _load_conversation_at(path, store, source=source)
        if conv is None:
            if on_progress is not None:
                on_progress(i + 1, total)
            continue
        try:
            mtime_after = path.stat().st_mtime
        except OSError:
            mtime_after = None
        # If we couldn't stat the file at all, fall back to 0.0 (legacy
        # behavior) — the file just disappeared and the next drift pass
        # will resolve via the cleanup branch.
        if mtime_before is None or mtime_after is None:
            mtime = 0.0
        elif mtime_before != mtime_after:
            logger.info(
                "search_index: file mtime drifted during initial-build "
                "read (%s → %s); skipping upsert, drift pass will retry: %s",
                mtime_before, mtime_after, path,
            )
            if on_progress is not None:
                on_progress(i + 1, total)
            continue
        else:
            mtime = mtime_before
        try:
            messages_indexed += index.upsert_conversation(conv, path, mtime)
            files_indexed += 1
        except sqlite3.Error:
            logger.exception("search_index: upsert failed for %s", path)
        if on_progress is not None:
            on_progress(i + 1, total)

    # Refresh query-planner statistics now that the inverted lists have
    # their final shape. Cheap (~ms on a small index; SQLite skips the
    # work for tables it deems already-analyzed) and pays back on every
    # search until the next big drift pass. Runs BEFORE mark_ready() so
    # the first query post-build sees fresh stats. (perf-polish A3.)
    try:
        index.run_pragma_optimize()
    except sqlite3.Error:
        logger.exception("search_index: PRAGMA optimize failed (non-fatal)")

    index.mark_ready()
    logger.info(
        "search_index: build complete: %d files / %d messages (drifted=%d, missing=%d)",
        files_indexed, messages_indexed, len(drifted), len(missing),
    )
    return files_indexed, messages_indexed


def update_drifted_files(
    store: Any,
    *,
    index: SearchIndex | None = None,
) -> int:
    """Re-index any file whose mtime no longer matches the indexed value.

    Also drops rows for files that have disappeared from disk (the
    cleanup pass). Returns the number of files re-indexed (does not
    count cleanup-only deletions).

    Thin wrapper over :func:`_drift_first_scan`. Cheap to call
    repeatedly — for unchanged files it does one ``os.stat`` per live
    path plus one ``SELECT`` against ``indexed_files`` and bails.
    Designed to be invoked from the watcher's event-driven and
    backstop-poll passes.
    """
    if index is None:
        index = get_search_index()
    if index is None:
        return 0

    drifted, missing = _drift_first_scan(store, index)

    # Cleanup pass.
    for path in missing:
        try:
            index.delete_by_path(path)
        except sqlite3.Error:
            logger.exception("search_index: cleanup-delete failed for %s", path)

    updated = 0
    for path, source in drifted:
        # Hunt #8 TOCTOU fix: check-read-check. Stat BEFORE the read so
        # the mtime we stamp into the index reflects the snapshot we
        # actually read, not a later one. If the file is mutated during
        # the read (CC appends a line between _load_conversation_at and
        # the upsert), the post-read stat will differ — skip this pass
        # and let the next drift fire pick up the post-race content.
        # Without this, the index would store stale content under a
        # fresh mtime and never re-detect the unread bytes.
        try:
            mtime_before = path.stat().st_mtime
        except OSError:
            continue
        conv = _load_conversation_at(path, store, source=source)
        if conv is None:
            continue
        try:
            mtime_after = path.stat().st_mtime
        except OSError:
            continue
        if mtime_before != mtime_after:
            logger.info(
                "search_index: file mtime drifted during read (%s → %s); "
                "skipping upsert, next drift pass will retry: %s",
                mtime_before, mtime_after, path,
            )
            continue
        try:
            index.upsert_conversation(conv, path, mtime_before)
            updated += 1
        except sqlite3.Error:
            logger.exception("search_index: drift-upsert failed for %s", path)

    return updated
