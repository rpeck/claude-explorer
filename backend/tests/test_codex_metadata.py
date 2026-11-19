"""N5: Codex metadata (name index, ``state_5``, imports, thread identity).

Plan: PLANS/2026.11.18-codex-source.md, §4.4 (the N5 part), §4.2 (the
current rollout and "newest"), §5 (N5), D17, and the N5 row of §6.1. The
``state_5`` read follows the 13 rules and the tests T1-T17 of
PLANS/reviews/2026.11.18-codex-source/council_n5_state_db.md.

The API that these tests call (the tester's contract for N5):

* ``backend.codex.home.is_inside(home, path) -> bool``: the real path of
  ``path`` is inside the real path of ``home``.
* ``backend.codex.name_index.read_session_index(home) -> dict[str, str]``:
  thread id to the last ``thread_name`` in ``session_index.jsonl``.
* ``backend.codex.imports.read_imports(home) -> dict[str, str]``: thread id
  to the Claude Code ``source_path``.
* ``backend.codex.state_db``:
  * ``read_threads(home) -> StateRead``; ``StateRead.rows`` is
    ``dict[thread id, dict[column, value]]``, or ``None`` on a fallback;
    ``StateRead.warnings`` is a ``list[str]``.
  * ``cleanup_stale_temp_dirs() -> None``: the startup sweep.
  * Constants ``CHUNK_SIZE``, ``DEADLINE_S``, ``MAX_BYTES``,
    ``MAX_ATTEMPTS``, ``CACHE_TTL_S``, ``TEMP_PREFIX``, each read at call
    time, so a test can change it.
  * The test seam ``_after_chunk(kind, copied)``: a no-op that the copy
    calls (through the module global) after each chunk that it reads from
    a Codex file. ``kind`` is ``"main"`` for the first read of
    ``state_5.sqlite``, ``"wal"`` for the copy of the ``-wal``, and
    ``"main_reread"`` for the second read of the main file. ``copied`` is
    the number of bytes read from that file so far in this attempt.
  * The copy is opened with ``sqlite3.connect`` from the ``sqlite3``
    module, so the trace in these tests sees its SQL.
* ``backend.codex.identity``:
  * ``rollout_stem(path) -> str | None``: the physical key.
  * ``thread_metadata(home, thread_id, rollout_paths, *, names, state,
    imports, previous=None) -> dict``: the thread fields of §4.2
    (``thread_id``, ``source_name``, ``title``, ``cwd``, ``archived``,
    ``history_mode``, ``thread_source``, ``parent_thread_id``,
    ``imported_from``, ``rollouts`` as ``{stem, session_meta_ts_ms}``,
    ``current_stem``, ``current_ambiguous``, ``updated_at_ms``).
    ``state`` is ``StateRead.rows`` (``None`` on a fallback). ``previous``
    is the dict that the last pass returned (the archived values, D6).
  * ``newest_copy(copies) -> dict``: the newest copy of one thread across
    homes; each copy has ``home_key``, ``updated_at_ms``, and ``rollouts``.

An audit hook checks every read: in the Codex home, only ``state_5.sqlite``
and ``state_5.sqlite-wal`` are opened, always read-only; ``-shm`` is never
opened; and ``sqlite3.connect`` never gets a path in the Codex home. No
test reads a real ``~/.codex``; the data comes from
``backend.tests.codex_fake`` or is built in the test.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests.codex_fake import (
    ALL_THREADS,
    IMPORTED_SOURCE_PATH,
    THREAD_ARCHIVED,
    THREAD_GUARDIAN,
    THREAD_IMPORTED,
    THREAD_LEGACY,
    THREAD_PAGINATED,
    THREAD_SUBAGENT,
    agent_message_item,
    build_codex_home,
    encode_lines,
    item_completed,
    ms,
    rollout_path,
    rollout_stem,
    session_meta,
    state_row,
    turn_context,
    user_message_item,
    write_rollout,
    write_session_index,
    write_state_db,
    zstd_compress,
)

STATE_FILES = frozenset({"state_5.sqlite", "state_5.sqlite-wal"})
TEMP_PREFIX = "claude-explorer-state5-"
SMALL_CHUNK = 4096

# The start times of the fake threads (backend/tests/codex_fake.py).
T_PAG = datetime(2026, 11, 10, 9, 0, 0, tzinfo=UTC)
T_LEG = datetime(2026, 10, 20, 14, 30, 0, tzinfo=UTC)
T_ARC = datetime(2026, 10, 5, 8, 15, 0, tzinfo=UTC)
T_IMP = datetime(2026, 11, 12, 16, 0, 0, tzinfo=UTC)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WRITE_VERBS = {
    "insert", "update", "delete", "replace", "create", "drop", "alter",
    "vacuum", "attach", "detach", "reindex", "analyze",
}


# ---------------------------------------------------------------------------
# The guard: an audit hook plus a trace of the SQL of every new connection
# ---------------------------------------------------------------------------


class _Guard:
    def __init__(self) -> None:
        self.active = False
        self.home_real = ""
        self.allowed: frozenset[str] | None = None
        self.violations: list[str] = []
        self.opens: list[str] = []
        self.connects: list[str] = []
        self.sql: list[str] = []
        self.traced = 0

    def reset(self, home: Path, allowed: frozenset[str] | None) -> None:
        self.home_real = os.path.realpath(home)
        self.allowed = allowed
        self.violations, self.opens, self.connects, self.sql = [], [], [], []
        self.traced = 0

    def under_home(self, path: str) -> bool:
        real = os.path.realpath(path)
        return real == self.home_real or real.startswith(self.home_real + os.sep)

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """Let a test hook change the Codex home, as Codex would."""
        was, self.active = self.active, False
        try:
            yield
        finally:
            self.active = was


_GUARD = _Guard()
_HOOK_INSTALLED = False


def _audit(event: str, args: tuple[Any, ...]) -> None:
    g = _GUARD
    if not g.active:
        return
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                return
            name = os.fsdecode(path)
            if not g.under_home(name):
                return
            base = os.path.basename(os.path.realpath(name))
            g.opens.append(base)
            writes = (isinstance(flags, int) and flags & _WRITE_FLAGS) or (
                isinstance(mode, str) and set(mode) & set("wax+")
            )
            if writes:
                g.violations.append(f"opened {base} for writing (mode={mode!r})")
            if base.endswith("-shm"):
                g.violations.append(f"opened {base} (rule 1: never open -shm)")
            if g.allowed is not None and base not in g.allowed:
                g.violations.append(f"opened {base} (rule 1: only {sorted(g.allowed)})")
        elif event == "sqlite3.connect":
            db = os.fsdecode(args[0])
            if db.startswith("file:"):
                db = db[len("file:"):].split("?", 1)[0]
            g.connects.append(db)
            if db != ":memory:" and g.under_home(db):
                g.violations.append(f"sqlite3.connect got a Codex-home path: {os.path.basename(db)}")
    except Exception as exc:  # noqa: BLE001 - an audit hook must never break the code under test
        g.violations.append(f"audit hook error: {exc!r}")


@contextlib.contextmanager
def _guarded(
    home: Path, allowed: frozenset[str] | None = None, *, check: bool = True
) -> Iterator[_Guard]:
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit)  # audit hooks cannot be removed; _GUARD.active gates it
        _HOOK_INSTALLED = True
    real_connect = sqlite3.connect

    def traced_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(_GUARD.sql.append)
        _GUARD.traced += 1
        return conn

    _GUARD.reset(home, allowed)
    sqlite3.connect = traced_connect
    _GUARD.active = True
    try:
        yield _GUARD
    finally:
        _GUARD.active = False
        sqlite3.connect = real_connect
    if check:
        assert _GUARD.violations == [], f"Codex home rule broken: {_GUARD.violations}"


def _read(home: Path) -> tuple[Any, _Guard]:
    from backend.codex import state_db

    with _guarded(home, STATE_FILES) as g:
        result = state_db.read_threads(home)
    return result, g


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def real_deadline() -> None:
    """Keep ``DEADLINE_S`` at its real value in this test."""


@pytest.fixture(autouse=True)
def _relaxed_deadline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Many test sessions share the machine; a loaded CPU must not turn a
    # test of another rule into a deadline fallback. Deadline tests opt out.
    from backend.codex import state_db

    if "real_deadline" not in request.fixturenames:
        monkeypatch.setattr(state_db, "DEADLINE_S", 5.0)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return build_codex_home(tmp_path / "home")


def _db(home: Path) -> Path:
    return home / "state_5.sqlite"


def _wal(home: Path) -> Path:
    return home / "state_5.sqlite-wal"


def _home_hash(home: Path) -> dict[str, tuple[Any, ...]]:
    """Name, size, ``mtime_ns``, and SHA-256 of every file in the home."""
    out: dict[str, tuple[Any, ...]] = {}
    for path in sorted(home.rglob("*")):
        rel = path.relative_to(home).as_posix()
        if path.is_dir():
            out[rel + "/"] = ()
        else:
            st = path.stat()
            out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def _codex_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("backend.codex") and r.levelno >= logging.WARNING
    ]


def _assert_fallback(result: Any, caplog: pytest.LogCaptureFixture, *secrets: str) -> None:
    """A fallback: no rows, one warning, and no path, title, or cwd in it."""
    assert result.rows is None, "want a fallback (rows is None)"
    logged = _codex_warnings(caplog)
    assert len(logged) == 1, f"want one WARNING log line per pass, got {logged!r}"
    assert len(result.warnings) == 1, f"want one warning in StateRead.warnings, got {result.warnings!r}"
    for text in (*logged, *result.warnings):
        for secret in secrets:
            assert secret not in text, f"the warning names {secret!r}: {text!r}"


def _connect(db: Path) -> sqlite3.Connection:
    """A test writer on the live database, as Codex holds it."""
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    return conn


def _insert(conn: sqlite3.Connection, thread_id: str, title: str) -> None:
    conn.execute(
        "INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider,"
        " cwd, title, sandbox_policy, approval_mode) VALUES (?, '', 0, 0, 'cli', 'openai',"
        " '/home/fixture/w', ?, '{}', 'on-request')",
        (thread_id, title),
    )


def _keep_mtime(*paths: Path) -> dict[Path, int]:
    return {p: p.stat().st_mtime_ns for p in paths if p.exists()}


def _restore_mtime(saved: dict[Path, int]) -> None:
    """Emulate coarse timestamps (HFS+, FAT, SMB): the stat shows no change."""
    for p, mtime_ns in saved.items():
        if p.exists():
            os.utime(p, ns=(p.stat().st_atime_ns, mtime_ns))


def _rewrite_state(home: Path, rows: list[dict[str, Any]]) -> None:
    for suffix in ("", "-wal", "-shm"):
        (home / f"state_5.sqlite{suffix}").unlink(missing_ok=True)
    write_state_db(home, rows)


def _rollouts_of(home: Path, thread_id: str) -> list[Path]:
    return sorted(home.rglob(f"rollout-*-{thread_id}.jsonl*"))


def _row(path: Path, thread_id: str, **kw: Any) -> dict[str, Any]:
    kw.setdefault("started", T_PAG)
    kw.setdefault("updated", kw["started"] + timedelta(minutes=1))
    kw.setdefault("cwd", "/home/fixture/proj-row")
    kw.setdefault("title", "State title")
    kw.setdefault("model", "gpt-5.5-codex")
    kw.setdefault("history_mode", "paginated")
    return state_row(thread_id, path, **kw)


def _extra_rollout(
    home: Path,
    thread_id: str,
    started: datetime,
    *,
    archived: bool = False,
    zst: bool = False,
    user: str | None = "Resume the work.",
) -> Path:
    """Another rollout of ``thread_id`` (a resume or a fork keeps the id)."""
    turn, cwd = "turn-extra", "/home/fixture/proj-extra"
    lines: list[dict[str, Any] | str] = [
        session_meta(started, thread_id, cwd=cwd, history_mode="paginated"),
        turn_context(started + timedelta(seconds=1), turn, cwd=cwd, model="gpt-5.5-codex"),
    ]
    if user is not None:
        lines.append(item_completed(started + timedelta(seconds=2), thread_id, turn,
                                    user_message_item("item-extra-user", user)))
    lines.append(item_completed(started + timedelta(seconds=3), thread_id, turn,
                                agent_message_item("item-extra-reply", "Done.")))
    return write_rollout(rollout_path(home, started, thread_id, archived=archived, zst=zst), lines)


def _missing_state(home: Path) -> Any:
    """Remove ``state_5`` and return the fallback read."""
    for suffix in ("", "-wal", "-shm"):
        (home / f"state_5.sqlite{suffix}").unlink(missing_ok=True)
    result, _ = _read(home)
    assert result.rows is None
    return result


def _meta(
    home: Path,
    thread_id: str,
    *,
    state: Any = None,
    previous: dict[str, Any] | None = None,
    paths: list[Path] | None = None,
) -> dict[str, Any]:
    from backend.codex import identity, imports, name_index

    if state is None:
        state, _ = _read(home)
    with _guarded(home):
        return identity.thread_metadata(
            home,
            thread_id,
            paths if paths is not None else _rollouts_of(home, thread_id),
            names=name_index.read_session_index(home),
            state=state.rows,
            imports=imports.read_imports(home),
            previous=previous,
        )


# A writer process: it commits ``n`` rows into the -wal only, then
# ``idle`` keeps the connection open, ``crash`` exits with os._exit, and
# ``txn`` holds an open write transaction until it reads a line.
_WRITER = r"""
import os, sqlite3, sys
db, mode, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
c = sqlite3.connect(db, isolation_level=None)
c.execute("PRAGMA wal_autocheckpoint=0")
def ins(tid, title):
    c.execute(
        "INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider,"
        " cwd, title, sandbox_policy, approval_mode) VALUES (?, '', 0, 0, 'cli', 'openai',"
        " '/home/fixture/w', ?, '{}', 'on-request')", (tid, title))
for i in range(n):
    c.execute("BEGIN IMMEDIATE"); ins(f"wal-{i}", f"WAL row {i}"); c.execute("COMMIT")
if mode == "crash":
    print("ready", flush=True)
    os._exit(0)
if mode == "txn":
    c.execute("BEGIN IMMEDIATE"); ins("wal-pending", "not committed")
    print("ready", flush=True)
    sys.stdin.readline()
    c.execute("COMMIT")
    print("committed", flush=True)
else:
    print("ready", flush=True)
sys.stdin.read()
c.close()
"""


@contextlib.contextmanager
def _writer(db: Path, mode: str, n: int = 5) -> Iterator[subprocess.Popen[str]]:
    proc = subprocess.Popen(
        [sys.executable, "-c", _WRITER, str(db), mode, str(n)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        assert proc.stdout.readline().strip() == "ready", "the writer process failed"
        yield proc
    finally:
        with contextlib.suppress(BrokenPipeError):
            proc.stdin.close()
        proc.wait(timeout=30)
        proc.stdout.close()


def _wal_ids(rows: dict[str, Any], n: int = 5) -> set[str]:
    return {f"wal-{i}" for i in range(n)} & set(rows)


# ---------------------------------------------------------------------------
# state_db: rules and constants
# ---------------------------------------------------------------------------


def test_state_db_constants_match_the_council_rules(real_deadline: None) -> None:
    from backend.codex import state_db

    assert state_db.CHUNK_SIZE == 1 << 20  # rule 5
    assert state_db.DEADLINE_S == 0.25  # rule 5
    assert state_db.MAX_BYTES == 64 << 20  # rule 4
    assert state_db.MAX_ATTEMPTS == 3  # rule 8
    assert state_db.CACHE_TTL_S == 60  # rule 12
    assert state_db.TEMP_PREFIX == TEMP_PREFIX  # rule 10


def test_state_db_source_names_no_unsafe_sqlite_flag() -> None:
    """T12, rules 1 and 2: no ``immutable``, ``readonly_shm``, or ``_winapi``."""
    from backend.codex import state_db

    source = Path(state_db.__file__).read_text(encoding="utf-8")
    for word in ("immutable", "readonly_shm", "_winapi"):
        assert word not in source, f"backend/codex/state_db.py names {word!r}"


# ---------------------------------------------------------------------------
# state_db: the home is never changed (T1, T2, T3, T9, T16)
# ---------------------------------------------------------------------------


def test_quiescent_home_rows_are_read_and_home_is_unchanged(home: Path, real_deadline: None) -> None:
    """T1: only ``state_5.sqlite``; no ``-wal`` or ``-shm`` appears."""
    assert not _wal(home).exists() and not (home / "state_5.sqlite-shm").exists()
    before = _home_hash(home)

    result, _ = _read(home)

    assert _home_hash(home) == before
    assert not _wal(home).exists() and not (home / "state_5.sqlite-shm").exists()
    assert result.warnings == []
    assert set(result.rows) == {
        THREAD_PAGINATED, THREAD_LEGACY, THREAD_ARCHIVED, THREAD_SUBAGENT, THREAD_GUARDIAN,
    }
    row = result.rows[THREAD_PAGINATED]
    assert row["name"] == "Alpha parser refactor"
    assert row["title"] == "Refactor the parser module and list the files first."
    assert row["history_mode"] == "paginated"
    assert row["thread_source"] == "user"
    assert row["updated_at_ms"] == ms(T_PAG + timedelta(minutes=30))
    assert row["rollout_path"] == str(_rollouts_of(home, THREAD_PAGINATED)[0])
    assert bool(result.rows[THREAD_ARCHIVED]["archived"]) is True
    assert bool(row["archived"]) is False
    assert result.rows[THREAD_LEGACY]["history_mode"] == "legacy"
    assert json.loads(result.rows[THREAD_SUBAGENT]["source"])["subagent"]["thread_spawn"][
        "parent_thread_id"
    ] == THREAD_PAGINATED


def test_idle_live_writer_rows_in_wal_are_read_and_home_is_unchanged(home: Path) -> None:
    """T2: a writer commits into the ``-wal`` only and keeps its connection open."""
    with _writer(_db(home), "idle"):
        assert _wal(home).stat().st_size > 32
        before = _home_hash(home)
        result, _ = _read(home)
        after = _home_hash(home)

    assert after == before
    assert result.rows is not None
    assert _wal_ids(result.rows) == {f"wal-{i}" for i in range(5)}
    assert len(result.rows) == 10


def test_crashed_writer_wal_rows_are_read_and_home_is_unchanged(home: Path) -> None:
    """T3: the writer left ``-wal`` and ``-shm``; the reader reads the ``-wal``
    and keeps no handle open on any Codex file."""
    with _writer(_db(home), "crash"):
        pass
    assert _wal(home).exists() and (home / "state_5.sqlite-shm").exists()
    before = _home_hash(home)

    result, g = _read(home)

    assert _home_hash(home) == before
    assert result.rows is not None
    assert _wal_ids(result.rows) == {f"wal-{i}" for i in range(5)}
    assert "state_5.sqlite-shm" not in g.opens
    if sys.platform != "win32":
        from backend.tests._fd_count import fds_on

        assert fds_on(_db(home)) == 0, "a handle on a Codex file stayed open"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder modes")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_read_only_home_reads_rows_and_creates_nothing(home: Path) -> None:
    """T9: the home folder is ``chmod 0555``."""
    before = _home_hash(home)
    os.chmod(home, 0o555)
    try:
        result, _ = _read(home)
    finally:
        os.chmod(home, 0o755)

    assert _home_hash(home) == before
    assert result.rows is not None and len(result.rows) == 5


def test_open_write_transaction_reads_last_commit_and_does_not_block_writer(home: Path) -> None:
    """T16: Codex holds an open write transaction (the AC5 "locked" case)."""
    with _writer(_db(home), "txn") as proc:
        result, _ = _read(home)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write("commit\n")
        proc.stdin.flush()
        assert proc.stdout.readline().strip() == "committed", "the reader blocked the writer"

    assert result.rows is not None
    assert _wal_ids(result.rows) == {f"wal-{i}" for i in range(5)}
    assert "wal-pending" not in result.rows


# ---------------------------------------------------------------------------
# state_db: SQLite only sees the private copy (T12, rule 9)
# ---------------------------------------------------------------------------


def test_sqlite_never_receives_a_codex_home_path(home: Path, tmp_path: Path) -> None:
    """T12: a guard on ``sqlite3.connect`` sees only the copy."""
    with _writer(_db(home), "crash"):
        pass

    result, g = _read(home)

    assert result.rows is not None
    assert g.connects, "the guard saw no sqlite3.connect (it did not check the read)"
    assert not any(g.under_home(c) for c in g.connects)
    # The guard catches the read that the council rejected.
    with _guarded(home, STATE_FILES, check=False) as g2:
        sqlite3.connect(f"file:{_db(home)}?mode=ro", uri=True).close()
    assert g2.violations


def test_copy_runs_query_only_and_quick_check_and_no_other_pragma_or_write(home: Path) -> None:
    """Rule 9."""
    result, g = _read(home)

    assert result.rows is not None
    assert g.traced >= 1, "open the copy with sqlite3.connect from the sqlite3 module"
    stmts = [s.strip().lower() for s in g.sql if s.strip()]
    pragmas = [s for s in stmts if s.startswith("pragma")]
    query_only = [
        i for i, s in enumerate(stmts)
        if re.match(r"pragma\s+(main\.)?query_only\s*(=\s*|\(\s*)(1|on|true)\b", s)
    ]
    assert query_only, f"no PRAGMA query_only=1 in {stmts!r}"
    assert any(re.match(r"pragma\s+(main\.)?quick_check\b", s) for s in pragmas), stmts
    others = [s for s in pragmas if not re.match(r"pragma\s+(main\.)?(query_only|quick_check)\b", s)]
    assert others == [], f"rule 9 allows no other PRAGMA: {others!r}"
    writes = [s for s in stmts if s.split()[0] in _WRITE_VERBS]
    assert writes == [], f"rule 9 allows no SQL that writes: {writes!r}"
    selects = [i for i, s in enumerate(stmts) if s.startswith("select")]
    assert selects and query_only[0] < selects[0], "PRAGMA query_only=1 must come first"


# ---------------------------------------------------------------------------
# state_db: a changing source is never accepted (T4, T5, T6, T7)
# ---------------------------------------------------------------------------


def test_main_file_change_during_copy_is_rejected_and_retried(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5: the hook changes the main file once; attempt 2 reads the new commit."""
    from backend.codex import state_db

    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    w = _connect(_db(home))
    attempts: list[float] = []

    def hook(kind: str, copied: int) -> None:
        if kind == "main" and copied <= SMALL_CHUNK:
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                w.execute("UPDATE threads SET title = 'T5 changed' WHERE id = ?", (THREAD_LEGACY,))
                w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    try:
        result, _ = _read(home)
    finally:
        w.close()

    assert len(attempts) == 2, f"want a rejected attempt and one retry, got {len(attempts)} attempts"
    assert result.rows is not None and result.warnings == []
    assert result.rows[THREAD_LEGACY]["title"] == "T5 changed"


def test_main_file_change_on_every_attempt_falls_back_after_three(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T5 and rule 8: 3 attempts, a backoff of 10 ms and then 40 ms, then a fallback."""
    from backend.codex import state_db

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    w = _connect(_db(home))
    attempts: list[float] = []

    def hook(kind: str, copied: int) -> None:
        if kind == "main" and copied <= SMALL_CHUNK:
            attempts.append(time.monotonic())
            w.execute("UPDATE threads SET title = ? WHERE id = ?", (f"v{len(attempts)}", THREAD_LEGACY))
            w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    try:
        result, _ = _read(home)
    finally:
        w.close()

    assert len(attempts) == 3
    assert attempts[1] - attempts[0] >= 0.009, "want a 10 ms backoff before attempt 2"
    assert attempts[2] - attempts[1] >= 0.039, "want a 40 ms backoff before attempt 3"
    _assert_fallback(result, caplog, str(home))


def test_wal_restart_during_wal_copy_is_rejected(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T6: ``wal_checkpoint(TRUNCATE)`` and a new commit during the ``-wal`` copy,
    with the timestamps put back, so only the salts and the second read see it."""
    from backend.codex import state_db

    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    w = _connect(_db(home))
    for i in range(3):
        _insert(w, f"t6-{i}", f"T6 row {i}")
    assert _wal(home).stat().st_size > SMALL_CHUNK
    saved = _keep_mtime(_db(home), _wal(home))
    attempts: list[int] = []
    fired: list[bool] = []

    def hook(kind: str, copied: int) -> None:
        if kind == "main" and copied <= SMALL_CHUNK:
            attempts.append(1)
        if kind == "wal" and len(attempts) == 1 and not fired:
            w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            _insert(w, "t6-new", "T6 new generation")
            _restore_mtime(saved)
            fired.append(True)

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    try:
        result, _ = _read(home)
    finally:
        w.close()

    assert fired, "the -wal copy never reached the hook"
    assert len(attempts) == 2, f"want attempt 1 rejected and attempt 2 accepted, got {len(attempts)}"
    assert result.rows is not None
    assert {"t6-0", "t6-1", "t6-2", "t6-new"} <= set(result.rows)


def _replay_home(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """The GPT round 2 setup: 20 rows of 3000 bytes ('old'), a clean close,
    then a writer commits T: title = 'new' for t02 and t19, into the -wal."""
    home = tmp_path / "home"
    home.mkdir()
    rows = []
    for i in range(1, 21):
        row = _row(home / "sessions" / f"r{i}.jsonl", f"t{i:02d}", title="old")
        row["first_user_message"] = "x" * 3000
        rows.append(row)
    write_state_db(home, rows)
    w = _connect(_db(home))
    w.execute("UPDATE threads SET title = 'new' WHERE id IN ('t02', 't19')")
    return home, w


def test_gpt_replay_never_returns_a_torn_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T4: the council appendix sequence with coarse (put-back) timestamps.

    A checkpoint tears the main copy, then a WAL restart begins a new
    generation at the same inode. Every committed state has
    title(t02) == title(t19); E2 accepted ('old', 'new').
    """
    from backend.codex import state_db

    home, w = _replay_home(tmp_path)
    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    main_size = _db(home).stat().st_size
    saved = _keep_mtime(_db(home), _wal(home))
    attempts: list[int] = []
    done: set[str] = set()

    def hook(kind: str, copied: int) -> None:
        if kind == "main" and copied <= SMALL_CHUNK:
            attempts.append(1)
        if len(attempts) != 1:
            return
        if kind == "main" and "mid" not in done and copied >= main_size // 2:
            w.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            _restore_mtime(saved)
            done.add("mid")
        elif kind == "wal" and "wal" not in done:
            w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            w.execute("UPDATE threads SET title = 'new' WHERE id = 't10'")
            _restore_mtime(saved)
            done.add("wal")

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    try:
        result, _ = _read(home)
    finally:
        w.close()

    assert done == {"mid", "wal"}, f"the replay did not run all its steps: {done}"
    assert len(attempts) == 2, f"want attempt 1 rejected, got {len(attempts)} attempts"
    assert result.rows is not None
    torn = (result.rows["t02"]["title"], result.rows["t19"]["title"])
    assert torn[0] == torn[1], f"a state that never committed: t02, t19 = {torn}"


def test_gpt_replay_on_every_attempt_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T4: when the sequence repeats in each attempt, the read falls back."""
    from backend.codex import state_db

    caplog.set_level(logging.WARNING)
    home, w = _replay_home(tmp_path)
    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    main_size = _db(home).stat().st_size
    saved = _keep_mtime(_db(home), _wal(home))
    attempts: list[set[str]] = []

    def hook(kind: str, copied: int) -> None:
        if kind == "main" and copied <= SMALL_CHUNK:
            attempts.append(set())
            w.execute("UPDATE threads SET title = ? WHERE id IN ('t02', 't19')", (f"v{len(attempts)}",))
            _restore_mtime(saved)
        if not attempts:
            return
        done = attempts[-1]
        if kind == "main" and "mid" not in done and copied >= main_size // 2:
            w.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            _restore_mtime(saved)
            done.add("mid")
        elif kind == "wal" and "wal" not in done:
            w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            w.execute("UPDATE threads SET title = ? WHERE id = 't10'", (f"v{len(attempts)}",))
            _restore_mtime(saved)
            done.add("wal")

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    try:
        result, _ = _read(home)
    finally:
        w.close()

    assert len(attempts) == 3
    assert all(a == {"mid", "wal"} for a in attempts), attempts
    _assert_fallback(result, caplog, str(home))


def test_rollback_journal_at_start_falls_back(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    """T7 and rule 3."""
    caplog.set_level(logging.WARNING)
    (home / "state_5.sqlite-journal").write_bytes(b"\0" * 512)

    result, _ = _read(home)

    _assert_fallback(result, caplog, str(home))


def test_rollback_journal_created_during_copy_falls_back(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T7 and rule 3: a ``-journal`` that appears before the end check."""
    from backend.codex import state_db

    caplog.set_level(logging.WARNING)

    def hook(kind: str, copied: int) -> None:
        if kind == "main":
            with _GUARD.paused():
                (home / "state_5.sqlite-journal").write_bytes(b"\0" * 512)

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    result, _ = _read(home)

    _assert_fallback(result, caplog, str(home))


# ---------------------------------------------------------------------------
# state_db: missing, unreadable, or unknown files (T8)
# ---------------------------------------------------------------------------


def test_missing_state5_falls_back_with_a_warning(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    _db(home).unlink()

    result, _ = _read(home)

    _assert_fallback(result, caplog, str(home))


def test_missing_home_falls_back_and_creates_nothing(tmp_path: Path) -> None:
    absent = tmp_path / "no-codex-home"

    result, _ = _read(absent)

    assert result.rows is None
    assert not absent.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_unreadable_state5_falls_back_with_a_warning(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    os.chmod(_db(home), 0o000)
    try:
        result, _ = _read(home)
    finally:
        os.chmod(_db(home), 0o644)

    _assert_fallback(result, caplog, str(home))


def test_garbage_state5_falls_back_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    home = build_codex_home(tmp_path / "home-SECRETPATH")
    _db(home).write_bytes(b"this is not a SQLite database\n" * 200)

    result, _ = _read(home)

    _assert_fallback(result, caplog, str(home), "SECRETPATH")


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE other (id TEXT, title TEXT, cwd TEXT)",
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, cwd TEXT)",
    ],
    ids=["no-threads-table", "missing-columns"],
)
def test_unknown_schema_falls_back_with_a_clean_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, schema: str
) -> None:
    """T8 and rule 11: the warning names no path, title, or cwd value."""
    caplog.set_level(logging.WARNING)
    home = build_codex_home(tmp_path / "home-SECRETPATH")
    _db(home).unlink()
    conn = sqlite3.connect(_db(home))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(schema)
        table = schema.split()[2]
        conn.execute(f"INSERT INTO {table} VALUES ('id-1', 'SECRET-TITLE', '/SECRET-CWD')")
        conn.commit()
    finally:
        conn.close()

    result, _ = _read(home)

    _assert_fallback(result, caplog, str(home), "SECRETPATH", "SECRET-TITLE", "SECRET-CWD")


# ---------------------------------------------------------------------------
# state_db: the private temp folder (T10, rule 10)
# ---------------------------------------------------------------------------


@pytest.fixture
def system_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "system-tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def test_temp_folder_is_private_and_removed_after_success(
    home: Path, system_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex import state_db

    seen: list[tuple[str, int]] = []

    def hook(kind: str, copied: int) -> None:
        for p in system_tmp.iterdir():
            seen.append((p.name, stat.S_IMODE(p.lstat().st_mode)))

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    result, _ = _read(home)

    assert result.rows is not None
    assert seen, "no temp folder in the system temp folder during the copy"
    assert {name.startswith(TEMP_PREFIX) for name, _ in seen} == {True}, seen
    if sys.platform != "win32":
        assert {mode for _, mode in seen} == {0o700}, seen
    assert list(system_tmp.iterdir()) == []


def test_temp_folder_is_removed_after_an_exception(
    home: Path, system_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex import state_db

    def hook(kind: str, copied: int) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    with contextlib.suppress(RuntimeError):
        _read(home)

    assert list(system_tmp.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder modes and symlinks")
def test_startup_sweep_removes_only_owned_private_leftovers(tmp_path: Path, system_tmp: Path) -> None:
    from backend.codex import state_db

    leftover = system_tmp / f"{TEMP_PREFIX}leftover"
    leftover.mkdir()
    (leftover / "state_5.sqlite").write_bytes(b"old copy")
    os.chmod(leftover, 0o700)
    loose = system_tmp / f"{TEMP_PREFIX}loose"
    loose.mkdir()
    os.chmod(loose, 0o755)
    other = system_tmp / "other-prefix-folder"
    other.mkdir()
    os.chmod(other, 0o700)
    target = tmp_path / "symlink-target"
    target.mkdir()
    os.chmod(target, 0o700)
    (target / "keep.txt").write_text("keep")
    link = system_tmp / f"{TEMP_PREFIX}link"
    link.symlink_to(target, target_is_directory=True)
    plain_file = system_tmp / f"{TEMP_PREFIX}file"
    plain_file.write_text("a file, not a folder")

    state_db.cleanup_stale_temp_dirs()

    assert not leftover.exists()
    assert loose.is_dir(), "a folder that is not mode 0700 must stay"
    assert other.is_dir(), "a folder of another name must stay"
    assert link.is_symlink() and (target / "keep.txt").read_text() == "keep"
    assert plain_file.is_file()


# ---------------------------------------------------------------------------
# state_db: the cache (T11, rule 12)
# ---------------------------------------------------------------------------


@pytest.fixture
def chunks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The kind of every chunk that the copy reads."""
    from backend.codex import state_db

    seen: list[str] = []
    monkeypatch.setattr(state_db, "_after_chunk", lambda kind, copied: seen.append(kind))
    return seen


def test_cache_reuses_the_result_for_the_same_key(home: Path, chunks: list[str]) -> None:
    first, _ = _read(home)
    assert "main" in chunks
    chunks.clear()
    second, g = _read(home)

    assert first.rows is not None and second.rows == first.rows
    assert chunks == [], "a cache hit must not copy"
    assert "state_5.sqlite" not in g.opens


def test_cache_copies_again_after_the_key_changes(home: Path, chunks: list[str]) -> None:
    first, _ = _read(home)
    chunks.clear()
    w = _connect(_db(home))
    try:
        _insert(w, "t11-new", "T11 new row")
        second, _ = _read(home)
    finally:
        w.close()

    assert "main" in chunks
    assert second.rows is not None and "t11-new" in second.rows
    assert first.rows is not None and "t11-new" not in first.rows


def test_cache_copies_again_after_the_ttl(
    home: Path, chunks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex import state_db

    _read(home)
    chunks.clear()
    monkeypatch.setattr(state_db, "CACHE_TTL_S", 0)
    second, _ = _read(home)

    assert "main" in chunks, "a result older than CACHE_TTL_S must not be reused"
    assert second.rows is not None


# ---------------------------------------------------------------------------
# state_db: the deadline and the size cap (T13, rules 4 and 5)
# ---------------------------------------------------------------------------


def test_slow_copy_past_the_deadline_falls_back(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, real_deadline: None
) -> None:
    from backend.codex import state_db

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    slept: list[bool] = []

    def hook(kind: str, copied: int) -> None:
        if not slept:
            slept.append(True)
            time.sleep(0.3)

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    start = time.monotonic()
    result, _ = _read(home)
    elapsed = time.monotonic() - start

    _assert_fallback(result, caplog, str(home))
    assert elapsed < 1.5, f"the read went on for {elapsed:.2f} s after its deadline"


def test_main_file_over_the_size_cap_is_not_copied(
    tmp_path: Path, chunks: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    home = tmp_path / "home"
    home.mkdir()
    with open(_db(home), "wb") as f:
        f.truncate((64 << 20) + 1)  # sparse

    result, g = _read(home)

    _assert_fallback(result, caplog, str(home))
    assert chunks == [] and "state_5.sqlite" not in g.opens


def test_main_plus_wal_over_the_size_cap_is_not_copied(
    home: Path, chunks: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    with open(_wal(home), "wb") as f:
        f.truncate((64 << 20) - _db(home).stat().st_size + 1)

    result, g = _read(home)

    _assert_fallback(result, caplog, str(home))
    assert chunks == [] and "state_5.sqlite" not in g.opens


# ---------------------------------------------------------------------------
# state_db: Windows share modes (T14)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
@pytest.mark.parametrize("action", ["delete", "truncate"])
def test_windows_writer_can_delete_or_truncate_the_wal_during_the_copy(
    home: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from backend.codex import state_db

    with _writer(_db(home), "crash"):
        pass
    monkeypatch.setattr(state_db, "CHUNK_SIZE", SMALL_CHUNK)
    done: list[bool] = []

    def hook(kind: str, copied: int) -> None:
        if kind == "wal" and not done:
            with _GUARD.paused():
                if action == "delete":
                    os.remove(_wal(home))
                else:
                    os.truncate(_wal(home), 0)
            done.append(True)

    monkeypatch.setattr(state_db, "_after_chunk", hook)
    _read(home)

    assert done == [True], f"Codex could not {action} its -wal while Explorer read it"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_windows_failed_share_delete_open_falls_back(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    import ctypes
    from ctypes import wintypes

    caplog.set_level(logging.WARNING)
    with _writer(_db(home), "crash"):
        pass
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    # GENERIC_READ with no sharing, OPEN_EXISTING: every other open fails.
    handle = k32.CreateFileW(str(_wal(home)), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result, _ = _read(home)
    finally:
        k32.CloseHandle(handle)

    _assert_fallback(result, caplog, str(home))


# ---------------------------------------------------------------------------
# state_db: stress (T17, opt-in)
# ---------------------------------------------------------------------------

_CHURN = r"""
import sqlite3, sys, time
db, seconds = sys.argv[1], float(sys.argv[2])
INSERT = ("INSERT INTO threads (id, rollout_path, created_at, updated_at, source, model_provider,"
          " cwd, title, sandbox_policy, approval_mode) VALUES (?, '', 0, 0, 'cli', 'openai',"
          " '/home/fixture/w', ?, '{}', 'on-request')")
def connect():
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA wal_autocheckpoint=0")
    return c
def txn(c, k):
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE threads SET title = ?", (f"v{k}",))
    c.execute(INSERT, (f"churn-{k}", f"v{k}"))
    c.execute("COMMIT")
c = connect()
txn(c, 0)
print("ready", flush=True)
end, k = time.monotonic() + seconds, 1
while time.monotonic() < end:
    txn(c, k)
    if k % 10 == 0:
        c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
    if k % 25 == 0:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    if k % 6 == 0:
        c.close()
        c = connect()
    k += 1
c.close()
"""


@pytest.mark.serial
@pytest.mark.skipif(
    not os.environ.get("CLAUDE_EXPLORER_STATE5_STRESS"),
    reason="opt-in: set CLAUDE_EXPLORER_STATE5_STRESS=<seconds>",
)
def test_stress_churning_writer_never_breaks_the_invariant(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T17: in every committed state, all titles are equal."""
    from backend.codex import state_db

    monkeypatch.setattr(state_db, "CACHE_TTL_S", 0)
    seconds = os.environ["CLAUDE_EXPLORER_STATE5_STRESS"]
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHURN, str(_db(home)), seconds], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    accepted, bad = 0, []
    try:
        assert proc.stdout.readline().strip() == "ready"
        while proc.poll() is None:
            result = state_db.read_threads(home)
            if result.rows is not None:
                accepted += 1
                titles = {r["title"] for r in result.rows.values()}
                if len(titles) != 1:
                    bad.append(sorted(titles)[:4])
    finally:
        proc.wait(timeout=60)
        proc.stdout.close()

    assert bad == [], f"{len(bad)} accepted copies broke the invariant"
    assert accepted > 0


# ---------------------------------------------------------------------------
# name_index, imports, home
# ---------------------------------------------------------------------------


def test_session_index_last_name_wins(home: Path) -> None:
    from backend.codex import name_index

    with _guarded(home):
        names = name_index.read_session_index(home)

    assert names == {
        THREAD_PAGINATED: "Alpha parser refactor",
        THREAD_SUBAGENT: "Scout: callers of parse",
    }


def test_session_index_skips_damaged_lines(tmp_path: Path) -> None:
    from backend.codex import name_index

    home = tmp_path / "home"
    home.mkdir()
    when = T_PAG
    write_session_index(home, [(THREAD_LEGACY, "First name", when)])
    with open(home / "session_index.jsonl", "ab") as f:
        f.write(b"not json\n")
        f.write(b'{"id": "' + THREAD_LEGACY.encode() + b'", "thread_name": 7}\n')
        f.write(b'["a", "list"]\n')
        f.write(encode_lines([{"id": THREAD_ARCHIVED, "thread_name": "Other", "updated_at": "x"}]))
        f.write(encode_lines([{"id": THREAD_LEGACY, "thread_name": "Second name", "updated_at": "y"}]))

    names = name_index.read_session_index(home)

    assert names == {THREAD_LEGACY: "Second name", THREAD_ARCHIVED: "Other"}


def test_session_index_missing_is_empty(tmp_path: Path) -> None:
    from backend.codex import name_index

    assert name_index.read_session_index(tmp_path) == {}


def test_imports_map_the_thread_to_its_claude_code_source(home: Path) -> None:
    from backend.codex import imports

    with _guarded(home):
        found = imports.read_imports(home)

    assert found == {THREAD_IMPORTED: IMPORTED_SOURCE_PATH}


@pytest.mark.parametrize(
    "text",
    [
        None,
        "{not json",
        '["a list"]',
        '{"records": [{"source_path": "/x.jsonl"}, {"imported_thread_id": "t-1"}, 5]}',
    ],
    ids=["missing", "damaged", "not-an-object", "incomplete-records"],
)
def test_imports_missing_or_damaged_is_empty(tmp_path: Path, text: str | None) -> None:
    from backend.codex import imports

    if text is not None:
        (tmp_path / "external_agent_session_imports.json").write_text(text)

    assert imports.read_imports(tmp_path) == {}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_is_inside_home(tmp_path: Path) -> None:
    from backend.codex.home import is_inside

    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "link").symlink_to(outside, target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)

    assert is_inside(home, home / "sessions" / "2026" / "rollout-x.jsonl")  # need not exist
    assert is_inside(home, str(home / "archived_sessions" / "rollout-x.jsonl"))
    assert is_inside(alias, home / "sessions" / "rollout-x.jsonl")
    assert not is_inside(home, outside / "rollout-x.jsonl")
    assert not is_inside(home, home / "sessions" / ".." / ".." / "outside" / "rollout-x.jsonl")
    assert not is_inside(home, home / "link" / "rollout-x.jsonl")
    assert not is_inside(home, tmp_path / "home-other" / "rollout-x.jsonl")


# ---------------------------------------------------------------------------
# identity: the physical key and "newest" (§4.2)
# ---------------------------------------------------------------------------


def test_rollout_stem_is_the_same_for_every_file_form(tmp_path: Path) -> None:
    from backend.codex.identity import rollout_stem as stem_of

    stem = rollout_stem(T_PAG, THREAD_PAGINATED)
    for path in (
        tmp_path / "sessions" / "2026" / "11" / "10" / f"{stem}.jsonl",
        tmp_path / "sessions" / "2026" / "11" / "10" / f"{stem}.jsonl.zst",
        tmp_path / "archived_sessions" / f"{stem}.jsonl",
        tmp_path / "archived_sessions" / f"{stem}.jsonl.zst",
    ):
        assert stem_of(path) == stem
        assert stem_of(str(path)) == stem


@pytest.mark.parametrize(
    "name",
    ["session_index.jsonl", "state_5.sqlite", "notes.txt", "rollout-x.json", "rollout-a b.jsonl"],
)
def test_rollout_stem_of_another_name_is_none(tmp_path: Path, name: str) -> None:
    from backend.codex.identity import rollout_stem as stem_of

    assert stem_of(tmp_path / name) is None


def _copy(home_key: str, updated_at_ms: int | None, *meta_ts: int) -> dict[str, Any]:
    return {
        "home_key": home_key,
        "updated_at_ms": updated_at_ms,
        "rollouts": [{"stem": f"rollout-{i}", "session_meta_ts_ms": t} for i, t in enumerate(meta_ts)],
    }


def test_newest_copy_has_the_largest_updated_at() -> None:
    from backend.codex.identity import newest_copy

    old, new = _copy("aaaaaaaaaaaa", 1_000, 5_000), _copy("bbbbbbbbbbbb", 2_000, 10)
    assert newest_copy([old, new]) is new
    assert newest_copy([new, old]) is new


def test_newest_copy_without_updated_at_uses_its_newest_session_meta() -> None:
    from backend.codex.identity import newest_copy

    dated, undated = _copy("aaaaaaaaaaaa", 2_000), _copy("bbbbbbbbbbbb", None, 1_000, 3_000)
    assert newest_copy([dated, undated]) is undated
    assert newest_copy([undated, dated]) is undated


def test_newest_copy_tie_goes_to_the_smaller_home_key() -> None:
    from backend.codex.identity import newest_copy

    a, b = _copy("0aaaaaaaaaaa", 2_000), _copy("1bbbbbbbbbbb", None, 2_000)
    assert newest_copy([b, a]) is a
    assert newest_copy([a, b]) is a


# ---------------------------------------------------------------------------
# identity: name, title, archived, parents, imports (§4.4)
# ---------------------------------------------------------------------------


def test_thread_metadata_fields_for_a_state_row(home: Path) -> None:
    meta = _meta(home, THREAD_PAGINATED)
    stem = rollout_stem(T_PAG, THREAD_PAGINATED)

    assert meta["thread_id"] == THREAD_PAGINATED
    assert meta["cwd"] == "/home/fixture/proj-alpha"
    assert meta["history_mode"] == "paginated"
    assert meta["thread_source"] == "user"
    assert meta["updated_at_ms"] == ms(T_PAG + timedelta(minutes=30))
    assert [(r["stem"], r["session_meta_ts_ms"]) for r in meta["rollouts"]] == [(stem, ms(T_PAG))]
    assert meta["current_stem"] == stem
    assert meta["current_ambiguous"] is False
    assert json.loads(json.dumps(meta)) == meta, "the fields go into threads/<id>.json"


def test_source_name_prefers_the_session_index_then_the_state_name(home: Path) -> None:
    path = _rollouts_of(home, THREAD_PAGINATED)[0]
    _rewrite_state(home, [
        _row(path, THREAD_PAGINATED, name="State name"),
        _row(_rollouts_of(home, THREAD_ARCHIVED)[0], THREAD_ARCHIVED, name="Gamma release notes", archived=True),
        _row(_rollouts_of(home, THREAD_LEGACY)[0], THREAD_LEGACY, history_mode="legacy"),
    ])
    state, _ = _read(home)

    assert _meta(home, THREAD_PAGINATED, state=state)["source_name"] == "Alpha parser refactor"
    assert _meta(home, THREAD_ARCHIVED, state=state)["source_name"] == "Gamma release notes"
    assert _meta(home, THREAD_LEGACY, state=state)["source_name"] is None


@pytest.mark.parametrize(
    ("thread_id", "title"),
    [
        (THREAD_PAGINATED, "Alpha parser refactor"),  # the session index name
        (THREAD_SUBAGENT, "Scout: callers of parse"),
        (THREAD_ARCHIVED, "Gamma release notes"),  # state_5.name
        (THREAD_GUARDIAN, "Review the proposed command: rm -rf build/"),  # state_5.title
        (THREAD_IMPORTED, "Continue the migration from the Claude Code session."),  # no row
    ],
)
def test_title_chain(home: Path, thread_id: str, title: str) -> None:
    assert _meta(home, thread_id)["title"] == title


def test_title_uses_the_state_title_before_the_first_message(home: Path) -> None:
    _rewrite_state(home, [
        _row(_rollouts_of(home, THREAD_LEGACY)[0], THREAD_LEGACY, title="Build failure triage",
             history_mode="legacy"),
    ])

    assert _meta(home, THREAD_LEGACY)["title"] == "Build failure triage"


def test_title_without_name_row_or_human_message_is_codex_session_date(tmp_path: Path) -> None:
    """D2: a session with only an assistant turn still gets a title."""
    home = tmp_path / "home"
    path = _extra_rollout(home, THREAD_IMPORTED, T_IMP, user=None)
    write_state_db(home, [])

    meta = _meta(home, THREAD_IMPORTED, paths=[path])

    assert meta["title"].startswith("Codex session ")
    assert meta["title"] != "Codex session "
    assert meta["source_name"] is None


def test_first_message_title_skips_injected_context(home: Path) -> None:
    """Legacy: the ``<environment_context>`` user record is not a human message."""
    state = _missing_state(home)

    assert _meta(home, THREAD_LEGACY, state=state)["title"] == "Why does the build fail? Here is the error."


def test_archived_comes_from_the_state_row_over_the_folder(home: Path) -> None:
    _rewrite_state(home, [
        _row(_rollouts_of(home, THREAD_ARCHIVED)[0], THREAD_ARCHIVED, archived=False),
        _row(_rollouts_of(home, THREAD_PAGINATED)[0], THREAD_PAGINATED, archived=True),
    ])
    state, _ = _read(home)

    assert _meta(home, THREAD_ARCHIVED, state=state)["archived"] is False
    assert _meta(home, THREAD_PAGINATED, state=state)["archived"] is True


def test_archived_without_a_row_comes_from_the_folder(home: Path) -> None:
    _rewrite_state(home, [])
    state, _ = _read(home)
    assert state.rows == {}

    assert _meta(home, THREAD_ARCHIVED, state=state)["archived"] is True
    assert _meta(home, THREAD_PAGINATED, state=state)["archived"] is False


@pytest.mark.parametrize("fallback", [False, True], ids=["state-row", "session-meta"])
def test_parent_links_and_thread_source(home: Path, fallback: bool) -> None:
    state = _missing_state(home) if fallback else None

    sub = _meta(home, THREAD_SUBAGENT, state=state)
    guardian = _meta(home, THREAD_GUARDIAN, state=state)
    main = _meta(home, THREAD_PAGINATED, state=state)

    assert (sub["thread_source"], sub["parent_thread_id"]) == ("subagent", THREAD_PAGINATED)
    assert (guardian["thread_source"], guardian["parent_thread_id"]) == ("guardian_review", None)
    assert (main["thread_source"], main["parent_thread_id"]) == ("user", None)


def test_imported_from_claude_code(home: Path) -> None:
    assert _meta(home, THREAD_IMPORTED)["imported_from"] == IMPORTED_SOURCE_PATH
    assert _meta(home, THREAD_PAGINATED)["imported_from"] is None


# ---------------------------------------------------------------------------
# identity: current_stem and current_ambiguous (§4.2)
# ---------------------------------------------------------------------------


def test_current_stem_follows_the_state_rollout_path(home: Path) -> None:
    _extra_rollout(home, THREAD_PAGINATED, T_PAG + timedelta(days=2))

    meta = _meta(home, THREAD_PAGINATED)

    assert meta["current_stem"] == rollout_stem(T_PAG, THREAD_PAGINATED)
    assert meta["current_ambiguous"] is False
    assert len(meta["rollouts"]) == 2


def test_current_stem_matches_after_codex_compresses_and_archives(home: Path) -> None:
    """The physical key: state_5 names the old plain path in ``sessions/``."""
    (plain,) = _rollouts_of(home, THREAD_PAGINATED)
    moved = home / "archived_sessions" / (plain.name + ".zst")
    moved.write_bytes(zstd_compress(plain.read_bytes()))
    plain.unlink()
    _extra_rollout(home, THREAD_PAGINATED, T_PAG + timedelta(days=2))

    meta = _meta(home, THREAD_PAGINATED)

    assert meta["current_stem"] == rollout_stem(T_PAG, THREAD_PAGINATED)
    assert meta["current_ambiguous"] is False


def test_current_stem_outside_the_home_uses_the_newest_session_meta(home: Path, tmp_path: Path) -> None:
    (path,) = _rollouts_of(home, THREAD_PAGINATED)
    newer = T_PAG + timedelta(days=2)
    _extra_rollout(home, THREAD_PAGINATED, newer)
    _rewrite_state(home, [_row(tmp_path / "elsewhere" / "sessions" / path.name, THREAD_PAGINATED)])

    meta = _meta(home, THREAD_PAGINATED)

    assert meta["current_stem"] == rollout_stem(newer, THREAD_PAGINATED)


def test_two_rollouts_without_a_row_are_ambiguous(home: Path) -> None:
    newer = T_IMP + timedelta(days=1)
    _extra_rollout(home, THREAD_IMPORTED, newer)

    meta = _meta(home, THREAD_IMPORTED)

    assert meta["current_stem"] == rollout_stem(newer, THREAD_IMPORTED)
    assert meta["current_ambiguous"] is True


def test_one_rollout_without_a_row_is_not_ambiguous(home: Path) -> None:
    meta = _meta(home, THREAD_IMPORTED)

    assert meta["current_stem"] == rollout_stem(T_IMP, THREAD_IMPORTED)
    assert meta["current_ambiguous"] is False


# ---------------------------------------------------------------------------
# identity: a fallback pass keeps the archived values (T15, rule 11)
# ---------------------------------------------------------------------------


def _triage_home(home: Path) -> dict[str, Any]:
    """THREAD_LEGACY with state values that the rollout cannot give."""
    _rewrite_state(home, [
        _row(_rollouts_of(home, THREAD_LEGACY)[0], THREAD_LEGACY, title="Build failure triage",
             archived=True, history_mode="legacy", started=T_LEG),
    ])
    good = _meta(home, THREAD_LEGACY)
    assert (good["title"], good["archived"]) == ("Build failure triage", True)
    return json.loads(json.dumps(good))  # as threads/<id>.json stores it


def test_fallback_keeps_the_archived_state_values(home: Path) -> None:
    good = _triage_home(home)
    state = _missing_state(home)

    kept = _meta(home, THREAD_LEGACY, state=state, previous=good)
    fresh = _meta(home, THREAD_LEGACY, state=state)

    assert kept["title"] == "Build failure triage"
    assert kept["archived"] is True
    assert kept["current_stem"] == good["current_stem"]
    assert kept["current_ambiguous"] is False
    # Without archived values the same pass uses the rollout.
    assert fresh["title"] == "Why does the build fail? Here is the error."
    assert fresh["archived"] is False


def test_fallback_with_a_changed_rollout_set_is_ambiguous(home: Path) -> None:
    good = _triage_home(home)
    _extra_rollout(home, THREAD_LEGACY, T_LEG + timedelta(days=3))
    state = _missing_state(home)

    meta = _meta(home, THREAD_LEGACY, state=state, previous=good)

    assert len(meta["rollouts"]) == 2
    assert meta["current_ambiguous"] is True
    assert meta["title"] == "Build failure triage"


def test_fallback_without_archived_values_uses_session_meta_and_the_title_chain(home: Path) -> None:
    state = _missing_state(home)

    arc = _meta(home, THREAD_ARCHIVED, state=state)
    pag = _meta(home, THREAD_PAGINATED, state=state)

    assert (arc["source_name"], arc["title"]) == (None, "Summarize the release notes.")
    assert arc["archived"] is True
    assert (pag["source_name"], pag["title"]) == ("Alpha parser refactor", "Alpha parser refactor")
    assert pag["cwd"] == "/home/fixture/proj-alpha"
    assert pag["history_mode"] == "paginated"


def test_every_thread_gets_metadata_when_state5_is_missing(home: Path) -> None:
    """T8: rollouts never depend on ``state_5``."""
    state = _missing_state(home)

    for thread_id in ALL_THREADS:
        meta = _meta(home, thread_id, state=state)
        assert meta["thread_id"] == thread_id
        assert meta["title"]
