"""Copy append-only source files into the Explorer archive (plan §4.2, §4.3).

The archive keeps every source file even after the source tool deletes it
(CLAUDE.md principle 2). This module holds the generic parts of a copy pass:

- :class:`ArchiveLock`: one writer per archive folder, across threads and
  processes.
- :func:`run_locked_pass`: the home check and the lock around a pass.
- :func:`sync_stream`: steps 2.1-2.7 of §4.2 for one file stem.

A source adapter supplies the file list and the decoder. The copy only grows:
new bytes are appended (D12), a diverged source makes a ``.rev-<n>`` copy, and
nothing is ever deleted.

The MCP closure forbids ``backend.cc_watcher`` and ``backend.deps``, so this
module imports neither.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal

import portalocker

from fetcher.atomic import atomic_replace, atomic_write_bytes

if TYPE_CHECKING:
    from backend.config import Settings

log = logging.getLogger(__name__)

META_VERSION = 1
LOCK_FILE_NAME = ".sync.lock"

SyncReason = Literal["watcher", "backstop", "refresh", "cli", "mcp"]
SkippedReason = Literal["busy", "timeout", "corrupt_config", "cloud_sync_gate", "no_home"]

# A stem names files in the archive, so it gets no dots, slashes, or "..".
_STEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
_REV_RE = re.compile(r"\.rev-([0-9]+)\.jsonl")
_LOCK_POLL_S = 0.05
_CHUNK = 1 << 20
# New decoded bytes stay in memory up to this size, then go to a temp file.
_SPOOL_MAX = 8 << 20
# A decode over either cap is a decode error. Real rollouts are far smaller,
# and the caps bound the memory and disk that one bad file can use.
MAX_DECODED_BYTES = 1 << 30
MAX_LINE_BYTES = 64 << 20


@dataclass
class SyncContext:
    settings: Settings
    reason: SyncReason
    lock_timeout_s: float


def _zero_counts() -> dict[str, int]:
    return {"copied": 0, "unchanged": 0, "appended": 0, "revisions": 0, "decode_errors": 0}


@dataclass
class SyncReport:
    wrote_any: bool = False
    skipped_reason: SkippedReason | None = None
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=_zero_counts)

    def warn(self, message: str) -> None:
        log.warning("%s", message)
        self.warnings.append(message)


def home_key(home: Path) -> str:
    """The archive folder name for a source home: 12 hex chars of SHA-256."""
    real = os.path.realpath(home)
    if sys.platform in ("darwin", "win32"):
        real = real.casefold()
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]


# --- Lock ----------------------------------------------------------------

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock_for(key: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class ArchiveLock:
    """An exclusive lock on one archive folder, for threads and processes.

    A module ``threading.Lock`` per resolved path excludes other threads of
    this process. A portalocker lock on a fresh handle of the lock file
    excludes other processes: ``flock`` and ``msvcrt.locking`` lock per
    handle, so a shared handle would not block. The file holds no holder
    data, and the OS releases the lock of a process that dies.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)
        key = os.path.realpath(self.lock_path)
        if sys.platform in ("darwin", "win32"):
            key = key.casefold()
        self._thread_lock = _thread_lock_for(key)
        self._fh: Any = None

    def acquire(self, timeout_s: float) -> bool:
        """Take the lock. Wait at most ``timeout_s`` seconds (0 = one try)."""
        deadline = time.monotonic() + max(timeout_s, 0.0)
        if timeout_s > 0:
            got = self._thread_lock.acquire(timeout=timeout_s)
        else:
            got = self._thread_lock.acquire(blocking=False)
        if not got:
            return False
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.lock_path, "a+b")  # noqa: SIM115 - held until release()
        except BaseException:
            self._thread_lock.release()
            raise
        flags = portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
        while True:
            try:
                portalocker.lock(fh, flags)
                self._fh = fh
                return True
            except portalocker.LockException:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_S)
            except BaseException:
                fh.close()
                self._thread_lock.release()
                raise
        fh.close()
        self._thread_lock.release()
        return False

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            portalocker.unlock(fh)
        finally:
            fh.close()
            self._thread_lock.release()


def run_locked_pass(
    ctx: SyncContext,
    *,
    source_home: Path,
    archive_dir: Path,
    body: Callable[[SyncReport], None],
) -> SyncReport:
    """Run ``body`` under the archive lock, after the home check (§4.2 step 1).

    A missing ``source_home`` stops the pass and creates nothing. When the
    lock is not free within ``ctx.lock_timeout_s``, ``body`` does not run.
    """
    report = SyncReport()
    if not source_home.is_dir():
        report.skipped_reason = "no_home"
        return report
    lock = ArchiveLock(archive_dir / LOCK_FILE_NAME)
    if not lock.acquire(ctx.lock_timeout_s):
        report.skipped_reason = "busy" if ctx.lock_timeout_s <= 0 else "timeout"
        return report
    try:
        body(report)
    finally:
        lock.release()
    return report


# --- One stem --------------------------------------------------------------


def _checked_path(dest_dir: Path, name: str) -> Path:
    path = dest_dir / name
    real_dir = os.path.realpath(dest_dir)
    if os.path.dirname(os.path.realpath(path)) != real_dir:
        raise ValueError(f"archive path escapes {dest_dir}: {name!r}")
    return path


def _read_meta(path: Path) -> dict | None:
    try:
        meta = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _newest_revision(dest_dir: Path, stem: str) -> tuple[int, Path | None]:
    newest, newest_path = 0, None
    try:
        names = os.listdir(dest_dir)
    except OSError:
        return 0, None
    for name in names:
        if not name.startswith(stem):
            continue
        m = _REV_RE.fullmatch(name[len(stem) :])
        if m and int(m.group(1)) > newest:
            newest, newest_path = int(m.group(1)), dest_dir / name
    return newest, newest_path


class _DecodeError(Exception):
    pass


def _complete_lines(decode: Callable[[Path], Iterable[bytes]], path: Path) -> Iterator[bytes]:
    """Yield the decoded bytes up to each last newline (step 2.3).

    The source tool can still write to a last line that has no newline, so
    that line is dropped. Memory stays bounded by the longest line. A decode
    over ``MAX_DECODED_BYTES`` or a line over ``MAX_LINE_BYTES`` is a decode error.
    """
    rest = bytearray()
    total = 0
    try:
        for chunk in decode(path):
            total += len(chunk)
            if total > MAX_DECODED_BYTES:
                raise _DecodeError(f"the decoded file is over {MAX_DECODED_BYTES} bytes")
            if len(rest) + len(chunk) > MAX_LINE_BYTES and _has_long_line(rest, chunk):
                raise _DecodeError(f"a line is over {MAX_LINE_BYTES} bytes")
            cut = chunk.rfind(b"\n") + 1
            if not cut:
                rest += chunk
                continue
            yield bytes(rest) + chunk[:cut]
            rest = bytearray(chunk[cut:])
    except _DecodeError:
        raise
    except Exception as exc:  # any decoder failure is a decode error
        raise _DecodeError(exc) from exc


def _has_long_line(rest: bytes | bytearray, chunk: bytes) -> bool:
    """True when a line of ``rest + chunk`` (``rest`` has no newline) is over the cap."""
    start = -len(rest)  # the line start, in the positions of ``chunk``
    while (end := chunk.find(b"\n", max(start, 0))) != -1:
        if end + 1 - start > MAX_LINE_BYTES:
            return True
        start = end + 1
    return len(chunk) - start > MAX_LINE_BYTES


def _same_content(spool: IO[bytes], path: Path) -> bool:
    spool.seek(0)
    with open(path, "rb") as fh:
        while True:
            a, b = spool.read(_CHUNK), fh.read(_CHUNK)
            if a != b:
                return False
            if not a:
                return True


def _append_from(path: Path, spool: IO[bytes]) -> None:
    spool.seek(0)
    with open(path, "ab") as fh:
        shutil.copyfileobj(spool, fh, _CHUNK)
        fh.flush()
        os.fsync(fh.fileno())


def sync_stream(
    report: SyncReport,
    *,
    dest_dir: Path,
    stem: str,
    source_path: Path,
    decode: Callable[[Path], Iterable[bytes]],
    meta_extra: Mapping[str, object] | None = None,
    side_dir: Path | None = None,
) -> None:
    """Bring ``dest_dir/<stem>.jsonl`` up to date with ``source_path``.

    ``decode(source_path)`` yields the decoded bytes. Any exception from it is
    a decode error: the archive stays as it is, and the next pass tries again.
    An invalid ``stem`` raises ``ValueError`` and writes nothing.

    The ``.meta.json`` and ``.rev-<n>.jsonl`` files go to ``side_dir``
    (default ``dest_dir``). A copy that mirrors a live tree keeps them
    outside the mirror, so that the readers of the tree never see them.
    """
    if not isinstance(stem, str) or not _STEM_RE.fullmatch(stem):
        raise ValueError(f"invalid archive stem: {stem!r}")
    side_dir = dest_dir if side_dir is None else side_dir
    copy_path = _checked_path(dest_dir, f"{stem}.jsonl")
    meta_path = _checked_path(side_dir, f"{stem}.meta.json")

    # 2.1: an unchanged source needs no read.
    try:
        st = os.stat(source_path)
    except OSError as exc:
        report.warn(f"cannot stat {source_path}: {exc}")
        return
    meta = _read_meta(meta_path)
    version = meta.get("version", 0) if meta is not None else 0
    if not isinstance(version, int) or version > META_VERSION:
        # A newer Explorer wrote this stem. Do not overwrite its files.
        report.warn(f"skipping {meta_path.name}: version {version!r} is newer than {META_VERSION}")
        return
    if (
        meta is not None
        and meta.get("source_path") == str(source_path)
        and meta.get("source_size") == st.st_size
        and meta.get("source_mtime_ns") == st.st_mtime_ns
    ):
        report.counts["unchanged"] += 1
        return

    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX) as spool:
        # 2.2: decode as a stream, and compare it with the copy as it comes.
        # The bytes that the copy does not have go to the spool. Nothing is
        # written to the archive until the decode is complete.
        try:
            with contextlib.ExitStack() as stack:
                archive = stack.enter_context(open(copy_path, "rb")) if copy_path.exists() else None
                mode = "new" if archive is None else "prefix"
                matched = total = 0
                for chunk in _complete_lines(decode, source_path):
                    total += len(chunk)
                    if mode == "prefix":
                        old = archive.read(len(chunk))
                        if old == chunk:
                            matched += len(chunk)
                            continue
                        if len(old) < len(chunk) and chunk.startswith(old):
                            # The copy ends inside this chunk; the rest is new.
                            matched += len(old)
                            mode = "grow"
                            spool.write(chunk[len(old) :])
                            continue
                        # The source diverged: the spool gets the whole new content.
                        mode = "diverged"
                        archive.seek(0)
                        left = matched
                        while left and (block := archive.read(min(left, _CHUNK))):
                            spool.write(block)
                            left -= len(block)
                    spool.write(chunk)
                short_read = mode == "prefix" and archive.read(1) != b""
        except _DecodeError as exc:
            report.counts["decode_errors"] += 1
            report.warn(f"cannot decode {source_path}: {exc}; the archive copy stays as it is")
            return
        except OSError as exc:
            report.warn(f"cannot read the archive copy of {stem}: {exc}")
            return

        try:
            if mode == "prefix":
                # Equal, or 2.4: a short read, where the copy already has more.
                report.counts["unchanged"] += 1
                if short_read:
                    return
            elif mode == "new":
                if total:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    _append_from(copy_path, spool)
                    report.wrote_any = True
                    report.counts["copied"] += 1
                else:
                    report.counts["unchanged"] += 1
            elif mode == "grow":
                # 2.5: the source grew.
                _append_from(copy_path, spool)
                report.wrote_any = True
                report.counts["appended"] += 1
            else:
                # 2.6: the source diverged. Keep the old copy as the next revision.
                newest, newest_path = _newest_revision(side_dir, stem)
                if newest_path is not None and _same_content(spool, newest_path):
                    report.counts["unchanged"] += 1
                    return
                rev_path = _checked_path(side_dir, f"{stem}.rev-{newest + 1}.jsonl")
                side_dir.mkdir(parents=True, exist_ok=True)
                atomic_replace(copy_path, rev_path)
                _append_from(copy_path, spool)
                report.wrote_any = True
                report.counts["revisions"] += 1
                report.warn(f"{source_path} diverged from the archive copy; the old copy is {rev_path.name}")

            # 2.7: a crash before this write only makes the next pass read the source again.
            meta_out = {
                **(meta_extra or {}),
                "version": META_VERSION,
                "stem": stem,
                "copied_bytes": total,
                "source_path": str(source_path),
                "source_size": st.st_size,
                "source_mtime_ns": st.st_mtime_ns,
                "last_sync_ms": int(time.time() * 1000),
            }
            side_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(meta_path, json.dumps(meta_out, indent=2, ensure_ascii=False).encode("utf-8"))
        except OSError as exc:
            # The copy only grows, so the next pass continues from what is on disk.
            report.warn(f"cannot write the archive copy of {stem}: {exc}")


def sync_small_file(report: SyncReport, *, source_path: Path, dest_path: Path) -> None:
    """Copy a small JSON side file (a sidecar) when its bytes differ.

    Such a file changes in place (a new title), so the newest bytes replace
    the copy. A source that is gone or empty leaves the copy as it is.
    """
    try:
        data = source_path.read_bytes()
    except OSError:
        return
    if not data:
        return
    try:
        if dest_path.read_bytes() == data:
            return
    except OSError:
        pass
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(dest_path, data)
    except OSError as exc:
        report.warn(f"cannot write the archive copy of {source_path.name}: {exc}")
        return
    report.wrote_any = True


def _is_inside(root: Path, path: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))
    except (OSError, ValueError):
        return False


def _plain(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            yield chunk


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def sync_tree(
    report: SyncReport,
    *,
    live_root: Path,
    archive_root: Path,
    side_root: Path,
    streams: Iterable[Path],
    side_files: Iterable[Path] = (),
    deadline: float | None = None,
) -> tuple[int, int, bool]:
    """Mirror plain append-only files of ``live_root`` under ``archive_root``.

    Each stream keeps its path relative to ``live_root``; its copy metadata
    and revisions go to the same relative folder under ``side_root``. A
    file whose real path is outside ``live_root`` is skipped. The oldest
    files go first: the source tool deletes those first. After ``deadline``
    (``time.monotonic``) the pass stops; the next pass goes on from what is
    on disk. ``side_files`` (sidecars) are byte copies at the same relative path.

    Returns ``(files written, bytes written, finished)``.
    """
    files = written = 0
    for path in sorted(streams, key=_mtime):
        if deadline is not None and time.monotonic() >= deadline:
            return files, written, False
        if not _is_inside(live_root, path):
            continue
        rel = path.relative_to(live_root)
        dest_dir = archive_root / rel.parent
        copy = dest_dir / path.name
        before = _size(copy)
        was, report.wrote_any = report.wrote_any, False
        try:
            sync_stream(
                report, dest_dir=dest_dir, stem=path.name.removesuffix(".jsonl"),
                source_path=path, decode=_plain, side_dir=side_root / rel.parent,
            )
        except ValueError as exc:
            report.warn(f"archive copy skipped: {exc}")
        if report.wrote_any:
            files += 1
            written += max(_size(copy) - before, 0)
        report.wrote_any = report.wrote_any or was
    for path in side_files:
        if _is_inside(live_root, path):
            sync_small_file(report, source_path=path, dest_path=archive_root / path.relative_to(live_root))
    return files, written, True


# --- Machine folders (#92) ----------------------------------------------------

MACHINES_DIR = "machines"
MACHINE_FILE = "machine.json"
_MACHINE_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _identity_file() -> Path:
    # Not in data_dir: a shared data folder must not give two machines one id.
    from backend.config import canonical_home_dir

    return canonical_home_dir() / MACHINE_FILE


def _read_machine_id(path: Path) -> str | None:
    data = _read_meta(path)
    mid = data.get("machine_id") if data is not None else None
    return mid if isinstance(mid, str) and _MACHINE_ID_RE.fullmatch(mid) else None


# The identity file -> ((mtime_ns, size), id, label); every list row asks for the id.
_identity_cache: tuple[tuple[str, int, int], str | None, str | None] | None = None


def read_identity() -> tuple[str | None, str | None]:
    """``(machine_id, label)`` of this machine; ``(None, None)`` before the first copy."""
    global _identity_cache
    ident = _identity_file()
    try:
        st = ident.stat()
        key = (str(ident), st.st_mtime_ns, st.st_size)
    except OSError:
        return None, None
    hit = _identity_cache
    if hit is not None and hit[0] == key:
        return hit[1], hit[2]
    data = _read_meta(ident) or {}
    mid = _read_machine_id(ident)
    label = data.get("label") if isinstance(data.get("label"), str) else None
    _identity_cache = (key, mid, label)
    return mid, label


def own_machine_id(create: bool = False) -> str | None:
    """This machine's id. With ``create``, make the identity file on first use.

    The id is a random UUID, never the host name. ``None`` when there is
    no id yet (or it cannot be written).
    """
    ident = _identity_file()
    mid = _read_machine_id(ident)
    if mid is not None or not create:
        return mid
    lock = ArchiveLock(ident.with_name(ident.name + ".lock"))
    try:
        got = lock.acquire(5.0)
    except OSError as exc:  # the folder of the identity file cannot be made
        log.warning("cannot write the machine identity file %s: %s", ident, exc)
        return None
    if not got:
        return None
    try:
        mid = _read_machine_id(ident)  # another process can have made it
        if mid is None:
            mid = str(uuid.uuid4())
            body = {"version": META_VERSION, "machine_id": mid, "label": None}
            atomic_write_bytes(ident, json.dumps(body, indent=2).encode("utf-8"))
    except OSError as exc:
        log.warning("cannot write the machine identity file %s: %s", ident, exc)
        return None
    finally:
        lock.release()
    return mid


def write_machine_marker(machine: Path, mid: str, label: str | None) -> None:
    """Write ``<machine>/machine.json`` when its id or label differs."""
    marker = machine / MACHINE_FILE
    old = _read_meta(marker) or {}
    if old.get("machine_id") == mid and old.get("label") == label and old.get("version") == META_VERSION:
        return
    body = {"version": META_VERSION, "machine_id": mid, "label": label}
    machine.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(marker, json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8"))


def this_machine_dir(data_dir: Path) -> Path | None:
    """``<data_dir>/machines/<machine_id>`` of this machine; makes the id on first use.

    Call this only when there is something to copy: it writes the identity
    file and ``machine.json`` (with the label of the identity file).
    ``None`` when the identity file cannot be written.
    """
    mid = own_machine_id(create=True)
    if mid is None:
        return None
    machine = data_dir / MACHINES_DIR / mid
    write_machine_marker(machine, mid, read_identity()[1])
    return machine


def machine_of(path: Path) -> str | None:
    """The machine of a session file: the ``machines/<id>`` folder it is in, else this machine."""
    return _folder_machine(path) or read_identity()[0]


#: A pass that a person waits for stops after this time; the watcher passes do not stop.
_PASS_BUDGET_S: dict[str, float] = {"refresh": 20.0, "cli": 20.0}


def sync_machine_tree(
    ctx: SyncContext,
    *,
    data_dir: Path,
    part: str,
    label: str,
    live_root: Path,
    mirror: str | None,
    streams: Callable[[], list[Path]],
    side_files: Callable[[list[Path]], list[Path]],
) -> SyncReport | None:
    """Copy one live tree into ``machines/<id>/<part>`` of this machine.

    ``mirror`` is the folder under ``<part>`` that mirrors ``live_root``
    (``None`` = ``<part>`` itself). The copy metadata and revisions go to
    ``<part>/sync``. MCP does not copy: a first copy can be gigabytes. A
    pass with nothing to copy creates nothing and gives no report.
    """
    if ctx.reason == "mcp":
        return None
    if not live_root.is_dir():
        return SyncReport(skipped_reason="no_home")
    files = streams()
    if not files:
        return None
    machine = this_machine_dir(data_dir)
    if machine is None:
        report = SyncReport()
        report.warn(f"{label} archive copy skipped: this machine has no id")
        return report
    part_dir = machine / part
    archive_root = part_dir / mirror if mirror else part_dir
    budget = _PASS_BUDGET_S.get(ctx.reason)

    def body(report: SyncReport) -> None:
        deadline = time.monotonic() + budget if budget is not None else None
        n, size, finished = sync_tree(
            report, live_root=live_root, archive_root=archive_root, side_root=part_dir / "sync",
            streams=files, side_files=side_files(files), deadline=deadline,
        )
        if n or not finished:
            log.info(
                "archive copy (%s): %s: %d file(s), %d byte(s)%s", ctx.reason, label, n, size,
                "" if finished else "; stopped at the time limit, the next pass goes on",
            )

    return run_locked_pass(ctx, source_home=live_root, archive_dir=part_dir, body=body)


MACHINE_ID_RE = _MACHINE_ID_RE


def archive_roots(data_dir: Path, part: str) -> list[Path]:
    """``<data_dir>/machines/<id>/<part>`` of every machine, in name order."""
    try:
        machines = sorted(os.listdir(data_dir / MACHINES_DIR))
    except OSError:
        return []
    out = []
    for name in machines:
        root = data_dir / MACHINES_DIR / name / part
        if _MACHINE_ID_RE.fullmatch(name) and root.is_dir():
            out.append(root)
    return out


def live_first(groups: Iterable[Iterable[Path]], key: Callable[[Path], object]) -> list[Path]:
    """Every path of each group whose key no earlier group has.

    The live files are the first group, so an archive copy shows only when
    its live file is gone. Repeated keys inside one group all stay, as the
    readers had them before the copy.
    """
    seen: set[object] = set()
    out: list[Path] = []
    for group in groups:
        kept = [p for p in group if key(p) not in seen]
        seen.update(key(p) for p in kept)
        out.extend(kept)
    return out


# The chosen copy -> every machine that holds its prefix, the chosen one first.
# shortcut: filled by the last pick_copies call of each path, in memory; the
# list and the viewer both call it before they build rows.
_holders: dict[Path, list[str]] = {}
_prefix_memo: dict[tuple[Path, Path], tuple[tuple[int, int, int, int], bool]] = {}


def _is_prefix(a: Path, b: Path) -> bool:
    """True when the bytes of ``a`` start ``b`` (``a`` is not longer)."""
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    key = (sa.st_size, sa.st_mtime_ns, sb.st_size, sb.st_mtime_ns)
    hit = _prefix_memo.get((a, b))
    if hit is not None and hit[0] == key:
        return hit[1]
    result = sa.st_size <= sb.st_size
    if result:
        try:
            with open(a, "rb") as fa, open(b, "rb") as fb:
                left = sa.st_size
                while left > 0 and result:
                    n = min(left, _CHUNK)
                    result = fa.read(n) == fb.read(n)
                    left -= n
        except OSError:
            result = False
    _prefix_memo[(a, b)] = (key, result)
    return result


def pick_copies(
    groups: Iterable[Iterable[Path]],
    key: Callable[[Path], object],
    content: Callable[[Path], Path] = lambda p: p,
) -> list[Path]:
    """:func:`live_first`, plus the rule for one session on two machines (#92).

    This machine's copy (live, or in its own archive folder) always shows.
    Among the copies of other machines: when one copy is a prefix of the
    other, the longer copy shows; diverged copies: the first one (folder name
    order). :func:`machine_fields` names every machine whose copy is a prefix
    of the shown one. Nothing changes on disk. ``content`` maps a path to the
    file to compare.
    """
    first: dict[object, list[Path]] = {}
    others: dict[object, list[Path]] = {}
    order: list[object] = []
    for i, group in enumerate(groups):
        mine: dict[object, list[Path]] = {}
        for p in group:
            mine.setdefault(key(p), []).append(p)
        for k, paths in mine.items():
            if k not in first:
                first[k] = paths
                order.append(k)
            elif i:
                others.setdefault(k, []).append(paths[0])
    out: list[Path] = []
    for k in order:
        paths = first[k]
        alts = others.get(k)
        if len(paths) != 1 or not alts:
            for p in paths:
                _holders.pop(p, None)
            out.extend(paths)
            continue
        chosen = paths[0]
        by_machine: dict[str | None, Path] = {machine_of(chosen): chosen}
        for alt in alts:
            by_machine.setdefault(machine_of(alt), alt)
        if len(by_machine) == 1:
            _holders.pop(chosen, None)
            out.append(chosen)
            continue
        mine = [p for p in (chosen, *alts) if _is_own_copy(p)]
        if mine:  # this machine's copy always shows (S5)
            chosen = mine[0]
        else:
            for alt in by_machine.values():
                if alt is not chosen and _is_prefix(content(chosen), content(alt)):
                    chosen = alt
        holders = [m for m, p in by_machine.items() if m and p is not chosen and _is_prefix(content(p), content(chosen))]
        _holders[chosen] = [m for m in (machine_of(chosen), *holders) if m]
        out.append(chosen)
    return out


def _folder_machine(path: Path) -> str | None:
    """The id of the ``machines/<id>`` folder that holds ``path``; None for a live file."""
    parts = Path(path).parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == MACHINES_DIR and _MACHINE_ID_RE.fullmatch(parts[i + 1]):
            return parts[i + 1]
    return None


def _is_own_copy(path: Path) -> bool:
    """A live file of this machine, or a copy in this machine's own archive folder."""
    folder = _folder_machine(path)
    return folder is None or folder == read_identity()[0]


def machine_fields(path: Path) -> dict[str, object]:
    """``machine_id`` and ``machine_ids`` (every holder, the shown copy first) of a session file."""
    mid = machine_of(path)
    ids = _holders.get(Path(path)) or ([mid] if mid else [])
    return {"machine_id": mid, "machine_ids": list(ids)}


__all__ = [
    "LOCK_FILE_NAME",
    "MACHINES_DIR",
    "MACHINE_FILE",
    "MAX_DECODED_BYTES",
    "MAX_LINE_BYTES",
    "META_VERSION",
    "ArchiveLock",
    "machine_fields",
    "machine_of",
    "own_machine_id",
    "pick_copies",
    "read_identity",
    "write_machine_marker",
    "SyncContext",
    "SyncReport",
    "archive_roots",
    "home_key",
    "live_first",
    "run_locked_pass",
    "sync_machine_tree",
    "sync_small_file",
    "sync_stream",
    "sync_tree",
    "this_machine_dir",
]
