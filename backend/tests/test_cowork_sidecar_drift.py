"""A change to a Cowork sidecar alone reaches the search index (L1).

A Cowork session has two files: ``<session_dir>/audit.jsonl`` (the turns)
and the sibling sidecar ``<session_dir>.json`` (title, times, workspace).
Before L1 the drift pass looked only at the mtime of ``audit.jsonl``, so a
rename in the sidecar (Claude Desktop, or the remote Cowork sync) never
reached search.

The contract pinned here, for local and remote Cowork sessions:

* A change to the sidecar alone makes the next drift pass re-index the
  session, in both drift entry points (``build_full_index`` at startup and
  ``update_drifted_files`` in the watcher). A new sidecar and a removed
  sidecar are changes too.
* After that re-index, later passes do nothing, also after a restart.
* A sidecar that changes during the read reaches the index on a later pass.
* A sidecar change made while no Explorer process ran, or while only the
  released code ran, reaches the index at the next start of the new code.
* Old and new processes do not re-index each other. The supervised watcher
  can run released code while ``serve`` runs new code, on the same index
  file. The released drift rule is "re-index when ``indexed_files.mtime``
  differs from the mtime of the file", and "delete the rows of an
  ``indexed_files`` path that is not a live conversation file". So
  ``indexed_files.mtime`` keeps its meaning, no extra path enters
  ``indexed_files``, and ``SCHEMA_VERSION`` does not change (plan
  2026.11.18-codex-source §2.1).

The "old process" in these tests is a model of the released drift ledger:
it reads and writes ``indexed_files`` with the released SQL, and nothing
else.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.xfail(strict=True, reason="L1 not implemented yet")

ORG = "0f0f0f0f-1111-2222-3333-444444444444"
LOCAL_UUID = "c0c0c0c0-0000-4000-8000-000000000001"
REMOTE_ID = "cse_fakesidecar0001"

# Fixed mtimes, so that each change has a known and distinct mtime.
AUDIT_MTIME = 1_800_000_000
SIDECAR_MTIME = AUDIT_MTIME - 100

# The schema that the released code expects. A different version, or a
# different ``messages`` column set, makes released code drop and rebuild
# the whole index each time it opens the file. If a later node bumps the
# version on purpose, it changes these values.
RELEASED_SCHEMA_VERSION = 14
RELEASED_MESSAGES_COLS = {
    "conv_uuid", "message_uuid", "sender", "created_at", "source",
    "project_path", "organization_id", "conv_created_at", "conv_updated_at",
    "is_compaction_summary", "title", "body", "body_text",
}
RELEASED_TABLES = {
    "messages", "message_rowids", "indexed_files", "schema_version",
    "conversation_summaries", "conversation_summaries_meta", "conversations",
}


# ----- corpus ----------------------------------------------------------


@dataclass
class Corpus:
    tmp_path: Path
    store: Any
    index: Any
    db_path: Path
    session_dir: Path
    kind: str

    @property
    def audit(self) -> Path:
        return self.session_dir / "audit.jsonl"

    @property
    def sidecar(self) -> Path:
        return self.session_dir.with_suffix(".json")

    @property
    def uuid(self) -> str:
        return LOCAL_UUID if self.kind == "local" else REMOTE_ID

    def reopen(self) -> None:
        """Close the index and open it again, as a new process does."""
        from backend.search_index import SearchIndex

        self.index.close()
        self.index = SearchIndex(self.db_path)


def _entry(n: int, role: str, text: str, session_id: str) -> dict:
    ts = f"2026-05-25T10:00:{n:02d}.000Z"
    if role == "user":
        return {
            "type": "user", "uuid": f"u-{n}", "session_id": session_id,
            "parent_tool_use_id": None, "client_platform": "desktop_app",
            "message": {"role": "user", "content": text},
            "_audit_timestamp": ts,
        }
    return {
        "type": "assistant", "uuid": f"a-{n}", "session_id": session_id,
        "parent_tool_use_id": None,
        "message": {
            "model": "claude-opus-4-7", "id": f"msg_{n}", "type": "message",
            "role": "assistant", "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
        "_audit_timestamp": ts,
    }


def _write_audit(c: Corpus, turns: list[tuple[str, str]], mtime: float) -> None:
    lines = [_entry(i + 1, role, text, c.uuid) for i, (role, text) in enumerate(turns)]
    c.audit.write_text("".join(json.dumps(ln) + "\n" for ln in lines))
    os.utime(c.audit, (mtime, mtime))


def _write_sidecar(c: Corpus, title: str, mtime: float) -> None:
    sidecar: dict[str, Any] = {
        "title": title,
        "model": "claude-opus-4-7",
        "cwd": "/sessions/synthetic-sandbox",
        "createdAt": 1_748_167_200_000,
        "lastActivityAt": 1_748_167_265_000,
        "isArchived": False,
    }
    if c.kind == "local":
        sidecar["sessionId"] = f"local_{LOCAL_UUID}"
    else:
        sidecar["sessionId"] = REMOTE_ID
        sidecar["organizationId"] = ORG
    c.sidecar.write_text(json.dumps(sidecar))
    os.utime(c.sidecar, (mtime, mtime))


BASE_TURNS = [("user", "find the WOMBATWORD please"), ("assistant", "found WOMBATWORD")]


def _make_corpus(tmp_path: Path, kind: str, *, with_sidecar: bool = True) -> Corpus:
    from backend.search_index import SearchIndex
    from backend.store import ConversationStore

    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    cowork_root = tmp_path / "claude_desktop_app" / "local-agent-mode-sessions"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root.mkdir(parents=True)
    if kind == "local":
        session_dir = cowork_root / "d_test" / "o_test" / f"local_{LOCAL_UUID}"
    else:
        session_dir = data_dir / "cowork-remote" / ORG / f"remote_{REMOTE_ID}"
    session_dir.mkdir(parents=True)

    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    db_path = tmp_path / "search-index.sqlite"
    c = Corpus(tmp_path, store, SearchIndex(db_path), db_path, session_dir, kind)
    _write_audit(c, BASE_TURNS, AUDIT_MTIME)
    if with_sidecar:
        _write_sidecar(c, "ALPHATITLE session", SIDECAR_MTIME)
    return c


@pytest.fixture(params=["local", "remote"])
def corpus(request, tmp_path: Path):
    c = _make_corpus(tmp_path, request.param)
    from backend.search_index import build_full_index

    build_full_index(c.store, index=c.index)
    yield c
    c.index.close()


@pytest.fixture(params=["local", "remote"])
def corpus_without_sidecar(request, tmp_path: Path):
    c = _make_corpus(tmp_path, request.param, with_sidecar=False)
    from backend.search_index import build_full_index

    build_full_index(c.store, index=c.index)
    yield c
    c.index.close()


# ----- passes and observations -----------------------------------------


def _build(c: Corpus) -> int:
    from backend.search_index import build_full_index

    return build_full_index(c.store, index=c.index)[0]


def _update(c: Corpus) -> int:
    from backend.search_index import update_drifted_files

    return update_drifted_files(c.store, index=c.index)


PASSES = pytest.mark.parametrize(
    "run_pass", [_build, _update], ids=["build_full_index", "update_drifted_files"]
)


def _hits(c: Corpus, token: str) -> set[str]:
    return {row["conv_uuid"] for row in c.index.query(token, limit=50)}


def _indexed_title(c: Corpus) -> str | None:
    with closing(sqlite3.connect(c.db_path)) as conn:
        row = conn.execute(
            "SELECT title FROM conversations WHERE conv_uuid = ?", (c.uuid,)
        ).fetchone()
    return row[0] if row else None


def _rename(c: Corpus, title: str, mtime: float) -> None:
    """Change only the sidecar: the audit file keeps its bytes and mtime."""
    audit_before = (c.audit.read_bytes(), c.audit.stat().st_mtime)
    _write_sidecar(c, title, mtime)
    assert (c.audit.read_bytes(), c.audit.stat().st_mtime) == audit_before


# ----- the released drift ledger (the "old process") --------------------


def _old_backlog(c: Corpus) -> tuple[list[str], list[str]]:
    """What the released drift scan would do: (re-index, delete) paths.

    Released rule: re-index a live file whose ``indexed_files.mtime`` differs
    from its mtime; delete the rows of an ``indexed_files`` path that is not
    a live conversation file. The only live file here is ``audit.jsonl``.
    """
    live = {str(c.audit): c.audit.stat().st_mtime}
    with closing(sqlite3.connect(c.db_path)) as conn:
        indexed = dict(conn.execute("SELECT path, mtime FROM indexed_files"))
    drifted = [p for p, m in live.items() if indexed.get(p) is None or float(indexed[p]) != m]
    missing = [p for p in indexed if p not in live]
    return drifted, missing


def _old_process_writes_ledger(c: Corpus) -> None:
    """The ``indexed_files`` write of a released upsert of ``audit.jsonl``."""
    with closing(sqlite3.connect(c.db_path, timeout=30)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO indexed_files "
            "(path, mtime, indexed_at, conv_uuid) VALUES (?, ?, ?, ?)",
            (str(c.audit), float(c.audit.stat().st_mtime), int(time.time()), c.uuid),
        )


def _reduce_to_released_footprint(c: Corpus) -> None:
    """Make the index file look as if only released code wrote it.

    Rows in tables that released code does not know are removed, and each
    ``indexed_files`` row is written again with the released SQL, so a new
    column gets the value that a released write gives it.
    """
    c.index.close()
    with closing(sqlite3.connect(c.db_path, timeout=30)) as conn, conn:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        for name in tables:
            if name in RELEASED_TABLES or name.startswith(("messages_", "sqlite_")):
                continue
            conn.execute(f'DELETE FROM "{name}"')
        rows = conn.execute(
            "SELECT path, mtime, indexed_at, conv_uuid FROM indexed_files"
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO indexed_files "
                "(path, mtime, indexed_at, conv_uuid) VALUES (?, ?, ?, ?)",
                row,
            )
    from backend.search_index import SearchIndex

    c.index = SearchIndex(c.db_path)


# ----- a sidecar change alone causes a re-index -------------------------


@PASSES
def test_sidecar_change_alone_reindexes_session(corpus, run_pass):
    assert _hits(corpus, "ALPHATITLE") == {corpus.uuid}

    _rename(corpus, "BRAVOTITLE session", SIDECAR_MTIME + 50)

    assert run_pass(corpus) == 1
    assert _indexed_title(corpus) == "BRAVOTITLE session"
    assert _hits(corpus, "BRAVOTITLE") == {corpus.uuid}
    assert _hits(corpus, "ALPHATITLE") == set()


@PASSES
def test_sidecar_newer_than_audit_reindexes_only_once(corpus, run_pass):
    """The sidecar mtime passes the audit mtime; later passes do nothing."""
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)
    assert run_pass(corpus) == 1

    assert [run_pass(corpus) for _ in range(3)] == [0, 0, 0]
    assert _hits(corpus, "BRAVOTITLE") == {corpus.uuid}


def test_new_sidecar_reindexes_session_that_had_none(corpus_without_sidecar):
    c = corpus_without_sidecar
    assert _indexed_title(c) == "Untitled"
    assert _update(c) == 0

    _write_sidecar(c, "BRAVOTITLE session", AUDIT_MTIME + 50)

    assert _update(c) == 1
    assert _hits(c, "BRAVOTITLE") == {c.uuid}
    assert _update(c) == 0


def test_removed_sidecar_reindexes_session(corpus):
    corpus.sidecar.unlink()

    assert _update(corpus) == 1
    assert _indexed_title(corpus) == "Untitled"
    assert _hits(corpus, "ALPHATITLE") == set()
    assert _update(corpus) == 0


@PASSES
def test_sidecar_written_during_read_reaches_index_on_next_pass(
    corpus, run_pass, monkeypatch
):
    """Check-read-check covers the sidecar: content that the pass did not
    read is never stamped as indexed."""
    from backend import cowork_reader

    real_read = cowork_reader.read_cowork_conversation
    _rename(corpus, "BRAVOTITLE session", SIDECAR_MTIME + 50)

    def read_then_rename(session_dir: Path):
        conv = real_read(session_dir)
        _write_sidecar(corpus, "CHARLIETITLE session", SIDECAR_MTIME + 80)
        return conv

    monkeypatch.setattr(cowork_reader, "read_cowork_conversation", read_then_rename)
    run_pass(corpus)
    monkeypatch.setattr(cowork_reader, "read_cowork_conversation", real_read)

    run_pass(corpus)
    assert _indexed_title(corpus) == "CHARLIETITLE session"
    assert _hits(corpus, "CHARLIETITLE") == {corpus.uuid}
    assert run_pass(corpus) == 0


# ----- restarts ----------------------------------------------------------


def test_sidecar_change_while_stopped_is_found_at_restart(corpus):
    corpus.index.close()
    _rename(corpus, "BRAVOTITLE session", SIDECAR_MTIME + 50)
    corpus.reopen()

    assert _build(corpus) == 1
    assert _hits(corpus, "BRAVOTITLE") == {corpus.uuid}


def test_restart_after_sidecar_reindex_reindexes_nothing(corpus):
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)
    assert _update(corpus) == 1

    corpus.reopen()

    assert _build(corpus) == 0
    assert _update(corpus) == 0


# ----- old and new processes ---------------------------------------------


def test_sidecar_reindex_leaves_no_backlog_for_released_process(corpus):
    """``indexed_files`` keeps the audit mtime and no extra path."""
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)
    assert _update(corpus) == 1

    assert _old_backlog(corpus) == ([], [])


def test_old_and_new_processes_do_not_reindex_each_other(corpus):
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)
    assert _update(corpus) == 1
    for _ in range(3):
        assert _old_backlog(corpus) == ([], [])
        assert _update(corpus) == 0

    # A new turn arrives, and the released watcher indexes it first.
    _write_audit(
        corpus, BASE_TURNS + [("user", "and the DELTAWORD too")], AUDIT_MTIME + 100
    )
    assert _old_backlog(corpus) == ([str(corpus.audit)], [])
    _old_process_writes_ledger(corpus)
    assert _old_backlog(corpus) == ([], [])

    # The new code may read the session once more, then both stay quiet.
    assert _update(corpus) <= 1
    for _ in range(3):
        assert _old_backlog(corpus) == ([], [])
        assert _update(corpus) == 0


def test_sidecar_change_under_released_code_is_found_after_upgrade(corpus):
    """An index that only released code wrote, plus a rename that released
    code could not see: the new code finds the rename, then settles."""
    _reduce_to_released_footprint(corpus)
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)

    assert _build(corpus) == 1
    assert _hits(corpus, "BRAVOTITLE") == {corpus.uuid}
    assert _update(corpus) == 0
    assert _old_backlog(corpus) == ([], [])


def test_index_stays_openable_by_released_code_after_sidecar_reindex(corpus):
    """No schema change: released code would otherwise drop the index."""
    _rename(corpus, "BRAVOTITLE session", AUDIT_MTIME + 50)
    assert _update(corpus) == 1

    with closing(sqlite3.connect(corpus.db_path)) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    assert versions == [RELEASED_SCHEMA_VERSION]
    assert cols == RELEASED_MESSAGES_COLS
    _old_process_writes_ledger(corpus)  # the released write still works
