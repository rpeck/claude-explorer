"""Codex: the rollouts of OpenAI Codex, copied into the Explorer archive.

PLANS/2026.11.18-codex-source.md §4.2 (layout), §4.4 (projection), §4.5
(output fields). The copier is the only part that reads a Codex home; it
never writes there (§1.2). Every reader reads the archive copy (D7):

    <data_dir>/codex/<home_key>/home.json
    <data_dir>/codex/<home_key>/rollouts/<stem>.jsonl    (+ .meta.json, .rev-<n>.jsonl)
    <data_dir>/codex/<home_key>/threads/<thread_id>.json  (the conversation file)
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

from backend.archive_sync import (
    SyncContext,
    SyncReport,
    home_key,
    run_locked_pass,
    sync_stream,
)
from backend.codex.home import (
    STEM_RE,
    THREAD_ID_RE,
    RolloutFile,
    find_rollouts,
    is_inside,
)
from backend.codex.identity import newest_copy, thread_metadata
from backend.codex.imports import read_imports
from backend.codex.name_index import read_session_index
from backend.codex.projection import project_rollout
from backend.codex.rollout import read_rollout
from backend.codex.state_db import read_threads
from backend.codex.zstd import open_decompressed
from fetcher.atomic import atomic_write_bytes

from .base import SourceDescriptor, WatchRoot

if TYPE_CHECKING:
    from backend.store import ConversationStore

log = logging.getLogger(__name__)

DESCRIPTOR = SourceDescriptor(
    id="CODEX",
    label="Codex",
    short_label="Codex",
    chronological=True,
    has_projects=True,
    has_archive_state=True,
    has_organizations=False,
    refetchable=False,
    # A later snapshot of an item replaces the earlier one (§4.4).
    messages_change_in_place=True,
    assistant_label="Codex",
)

#: The search index reindexes the Codex rows when this changes (§4.4).
CODEX_PROJECTION_VERSION = 3

ARCHIVE_DIR = "codex"
HOME_FILE = "home.json"
ROLLOUTS_DIR = "rollouts"
THREADS_DIR = "threads"
FILE_VERSION = 1

# A home folder name: a home_key, or a fixed fixture key (N9).
_HOME_DIR_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CHUNK = 1 << 20


def _decode(path: Path) -> Iterator[bytes]:
    """The decoded bytes of a plain or ``.zst`` rollout, as a stream."""
    if path.suffix == ".zst":
        with open_decompressed(path) as stream:
            while chunk := stream.read(_CHUNK):
                yield chunk
    else:
        with open(path, "rb") as stream:
            while chunk := stream.read(_CHUNK):
                yield chunk


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _newer_version(data: dict[str, Any]) -> bool:
    version = data.get("version")
    return not isinstance(version, int) or version > FILE_VERSION


def _write_if_changed(path: Path, data: dict[str, Any]) -> bool:
    payload = orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n"
    try:
        if path.read_bytes() == payload:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, payload)
    return True


def _display_home(path: str) -> str:
    """``~/...`` for a path under the user's home folder (D14), else ``…/<last name>``.

    Never an absolute path: it can hold the user name, and MCP returns this text.
    """
    home, real = Path(os.path.realpath(Path.home())).parts, Path(os.path.realpath(path))
    parts = real.parts
    # The default file systems of macOS and Windows ignore case.
    if sys.platform in ("darwin", "win32"):
        same = [p.casefold() for p in parts[: len(home)]] == [p.casefold() for p in home]
    else:
        same = parts[: len(home)] == home
    if same:
        return "/".join(("~", *parts[len(home):]))
    return "…/" + real.name


def _iso(ms: object) -> str | None:
    if not isinstance(ms, int) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


# --- The copy pass (§4.2) ------------------------------------------------------

# (archive folder, thread id) -> (inputs, metadata, model). The metadata step
# reads every rollout of a thread, and it runs on every pass (§4.2 step 3), so
# a pass reuses the result while its inputs stay the same.
# shortcut: in memory only, so each new process reads every rollout once;
# persist the inputs in the thread JSON if MCP start-up gets slow.
_metadata_memo: dict[tuple[str, str], tuple[Any, dict[str, Any], str | None]] = {}


def _write_home_json(home: Path, archive: Path) -> bool:
    path = archive / HOME_FILE
    old = _read_json(path)
    if old is not None and _newer_version(old):
        return False
    home_path = os.path.abspath(home)
    first_seen = old.get("first_seen_ms") if old else None
    if not isinstance(first_seen, int):
        first_seen = int(time.time() * 1000)
    return _write_if_changed(
        path, {"version": FILE_VERSION, "home_path": home_path, "first_seen_ms": first_seen}
    )


def _sync_home(home: Path, archive: Path, report: SyncReport) -> None:
    rollouts = find_rollouts(home)
    if not rollouts:
        return  # nothing to show yet; keep the archive free of an empty home
    if _write_home_json(home, archive):
        report.wrote_any = True
    rollouts_dir = archive / ROLLOUTS_DIR
    by_thread: dict[str, list[RolloutFile]] = {}
    for r in rollouts:
        if not r.thread_id or not is_inside(home, r.path):
            continue
        try:
            sync_stream(
                report,
                dest_dir=rollouts_dir,
                stem=r.stem,
                source_path=r.path,
                decode=_decode,
                meta_extra={"thread_id": r.thread_id},
            )
        except ValueError as exc:
            report.warn(f"Codex rollout skipped: {exc}")
            continue
        by_thread.setdefault(r.thread_id, []).append(r)

    # Step 3: on every pass, so a rename or an archive change reaches the archive.
    names = read_session_index(home, report.warnings)
    imports = read_imports(home, report.warnings)
    state = read_threads(home)
    report.warnings.extend(state.warnings)
    for thread_id, files in sorted(by_thread.items()):
        try:
            if _sync_thread(home, archive, thread_id, files, names, imports, state.rows):
                report.wrote_any = True
        except Exception as exc:  # noqa: BLE001 - one bad thread must not stop the others
            report.warn(f"Codex thread {thread_id} not updated: {type(exc).__name__}: {exc}")


def _sync_thread(
    home: Path,
    archive: Path,
    thread_id: str,
    files: list[RolloutFile],
    names: dict[str, str],
    imports: dict[str, str],
    rows: dict[str, dict[str, Any]] | None,
) -> bool:
    path = archive / THREADS_DIR / f"{thread_id}.json"
    previous = _read_json(path)
    if previous is not None and _newer_version(previous):
        log.warning("Codex thread %s skipped: a newer Explorer wrote it", thread_id)
        return False
    rollouts_dir = archive / ROLLOUTS_DIR
    stats = []
    for f in files:
        st = os.stat(f.path)
        stats.append((str(f.path), st.st_size, st.st_mtime_ns))
    row = rows.get(thread_id) if rows is not None else None
    inputs = (tuple(stats), names.get(thread_id), imports.get(thread_id), repr(row))
    memo_key = (str(archive), thread_id)
    hit = _metadata_memo.get(memo_key)
    if rows is not None and hit is not None and hit[0] == inputs:
        meta, model = hit[1], hit[2]
    else:
        meta = thread_metadata(
            home, thread_id, [f.path for f in files],
            names=names, state=rows, imports=imports, previous=previous,
        )
        # The model of the last turn of the current rollout (N4 projection).
        current = rollouts_dir / f"{meta['current_stem']}.jsonl"
        model = None
        if current.exists():
            model = project_rollout(read_rollout(current).records, thread_id=thread_id)["model"] or None
        if rows is not None:  # a fallback depends on the archived values
            _metadata_memo[memo_key] = (inputs, meta, model)

    entries = []
    for r in meta["rollouts"]:
        try:
            copied = os.stat(rollouts_dir / f"{r['stem']}.jsonl").st_size
        except OSError:
            copied = 0
        entries.append({"stem": r["stem"], "copied_bytes": copied, "session_meta_ts_ms": r["session_meta_ts_ms"]})
    # D1: a rollout that Codex lost stays in the list while its copy exists.
    stems = {e["stem"] for e in entries}
    for old in (previous or {}).get("rollouts") or []:
        stem = old.get("stem") if isinstance(old, dict) else None
        if (
            isinstance(stem, str) and STEM_RE.fullmatch(stem) and stem not in stems
            and (rollouts_dir / f"{stem}.jsonl").exists()
        ):
            entries.append(old)
    entries.sort(key=lambda e: e["stem"])

    out = {
        "version": FILE_VERSION,
        "thread_id": thread_id,
        "home_key": archive.name,
        "source_name": meta.get("source_name"),
        "title": meta.get("title"),
        "cwd": meta.get("cwd"),
        "model": model if model is not None else (previous or {}).get("model"),
        "archived": meta.get("archived"),
        "history_mode": meta.get("history_mode"),
        "thread_source": meta.get("thread_source"),
        "parent_thread_id": meta.get("parent_thread_id"),
        "imported_from": meta.get("imported_from"),
        "rollouts": entries,
        "current_stem": meta.get("current_stem"),
        "current_ambiguous": bool(meta.get("current_ambiguous")),
        "updated_at_ms": meta.get("updated_at_ms"),
    }
    return _write_if_changed(path, out)


# --- The reader ------------------------------------------------------------------

# Thread file -> ((mtime_ns, size), value). A thread file changes whenever
# its rollout copy grows (``copied_bytes``), so its stat keys every cache.
_thread_cache: dict[Path, tuple[tuple[int, int], dict[str, Any] | None]] = {}
_row_cache: dict[Path, tuple[tuple[int, int], dict[str, Any] | None]] = {}
_cache_lock = threading.Lock()


def clear_codex_caches() -> None:
    """Forget every cached thread file and list row (tests)."""
    with _cache_lock:
        _thread_cache.clear()
        _row_cache.clear()
    _metadata_memo.clear()


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached(cache: dict, path: Path, load: Any) -> dict[str, Any] | None:
    key = _stat_key(path)
    if key is None:
        return None
    with _cache_lock:
        hit = cache.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    value = load(path)
    with _cache_lock:
        cache[path] = (key, value)
    return value


def _load_thread_file(path: Path) -> dict[str, Any] | None:
    data = _read_json(path)
    if data is None:
        return None
    if _newer_version(data):
        log.warning("Codex thread file %s skipped: version %r is newer than %d",
                    path.name, data.get("version"), FILE_VERSION)
        return None
    if data.get("thread_id") != path.stem:
        return None
    return data


def _notices(thread: dict[str, Any], conv: dict[str, Any]) -> list[str]:
    out: list[str] = []
    warnings = len(conv.get("warnings") or [])
    if warnings:
        out.append(f"{warnings} warning{'s' if warnings != 1 else ''} while reading this session")
    out.extend(f"incomplete: {r}" for r in conv.get("incomplete_reasons") or [] if r != "current_ambiguous")
    if thread.get("current_ambiguous"):
        out.append("ambiguous current rollout")
    if thread.get("imported_from"):
        out.append("Imported from Claude Code")
    older = len(thread.get("rollouts") or []) - 1
    if older > 0:
        out.append(f"{older} older rollout{'s are' if older != 1 else ' is'} kept in the archive")
    return out


class CodexAdapter:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    @property
    def _root(self) -> Path:
        return self._store.data_dir / ARCHIVE_DIR

    def descriptor(self) -> SourceDescriptor:
        return DESCRIPTOR

    def watch_roots(self) -> list[WatchRoot]:
        # The store has no Codex root, so the home comes from the settings.
        from backend.config import get_settings

        home = get_settings().codex_home
        patterns = ("*.jsonl", "*.jsonl.zst", "session_index.jsonl", "state_5.sqlite*")
        return [WatchRoot(home, patterns, create=False)]

    # The copy -------------------------------------------------------------

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        home = Path(ctx.settings.codex_home)
        archive = self._root / home_key(home)
        return run_locked_pass(
            ctx, source_home=home, archive_dir=archive,
            body=lambda report: _sync_home(home, archive, report),
        )

    # The readers ------------------------------------------------------------

    def _thread_files(self) -> list[Path]:
        try:
            homes = sorted(p for p in self._root.iterdir() if _HOME_DIR_RE.fullmatch(p.name) and p.is_dir())
        except OSError:
            return []
        out = []
        for home_dir in homes:
            try:
                names = sorted(os.listdir(home_dir / THREADS_DIR))
            except OSError:
                continue
            out.extend(
                home_dir / THREADS_DIR / n for n in names
                if n.endswith(".json") and THREAD_ID_RE.fullmatch(n[:-5])
            )
        return out

    def _newest(self, paths: list[Path]) -> Path | None:
        """The newest copy of one thread id across homes (§4.2)."""
        copies = []
        for p in paths:
            data = _cached(_thread_cache, p, _load_thread_file)
            if data is not None:
                copies.append({**data, "home_key": p.parent.parent.name, "_path": p})
        return newest_copy(copies)["_path"] if copies else None

    def discover(self) -> list[Path]:
        by_id: dict[str, list[Path]] = {}
        for p in self._thread_files():
            by_id.setdefault(p.stem, []).append(p)
        out = []
        for paths in by_id.values():
            best = paths[0] if len(paths) == 1 else self._newest(paths)
            if best is not None:
                out.append(best)
        return out

    def _home_path(self, home_dir: Path) -> str | None:
        data = _read_json(home_dir / HOME_FILE)
        if data is None or _newer_version(data):
            return None
        path = data.get("home_path")
        return path if isinstance(path, str) and path else None

    def load(self, path: Path) -> dict[str, Any] | None:
        """The conversation dict of one thread file; ``None`` without a turn (D2)."""
        thread = _cached(_thread_cache, path, _load_thread_file)
        if thread is None:
            return None
        stem = thread.get("current_stem")
        if not isinstance(stem, str) or not STEM_RE.fullmatch(stem):
            return None
        home_dir = path.parent.parent
        try:
            read = read_rollout(home_dir / ROLLOUTS_DIR / f"{stem}.jsonl")
        except OSError:
            return None
        thread_id = thread["thread_id"]
        conv = project_rollout(
            read.records,
            thread_id=thread_id,
            history_mode=thread.get("history_mode"),
            current_ambiguous=bool(thread.get("current_ambiguous")),
            damaged_lines=read.damaged_lines,
        )
        if not conv["chat_messages"]:
            return None
        home_path = self._home_path(home_dir)
        conv.update(
            uuid=thread_id,
            name=thread.get("title"),
            summary="",
            source=DESCRIPTOR.id,
            model=thread.get("model") or conv["model"],
            project_path=thread.get("cwd") or conv["project_path"] or None,
            updated_at=_iso(thread.get("updated_at_ms")) or conv["updated_at"],
            is_archived=bool(thread.get("archived")),
            source_name=thread.get("source_name"),
            source_home=_display_home(home_path) if home_path else None,
            source_home_abs=home_path,
            notices=_notices(thread, conv),
            parent_uuid=thread.get("parent_thread_id"),
        )
        return conv

    def _row(self, path: Path) -> dict[str, Any] | None:
        conv = self.load(path)
        if conv is None:
            return None
        messages = conv.pop("chat_messages")
        conv.pop("compact_markers", None)
        conv["message_count"] = len(messages)
        conv["human_message_count"] = sum(1 for m in messages if m.get("sender") == "human")
        return conv

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        out = []
        for path in self.discover():
            if full_content:
                row = self.load(path)
            else:
                row = _cached(_row_cache, path, self._row)
                row = dict(row) if row is not None else None  # callers must not change the cache
            if row is not None:
                out.append(row)
        return out

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        if not isinstance(uuid, str) or not THREAD_ID_RE.fullmatch(uuid):
            return None
        paths = [p for p in self._thread_files() if p.stem == uuid]
        best = self._newest(paths) if paths else None
        data = self.load(best) if best is not None else None
        return (data, best) if data is not None else None

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        return self.load(path)

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        return False  # one chronological stream
