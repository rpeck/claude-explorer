"""Machines: labels, export rules, bundles, and the shared sync folder (issue #92).

One archive can hold the sessions of several machines. Each machine has a
random id (``~/.claude-explorer/machine.json``) and writes only its own
folder, ``machines/<id>/``. Two transports carry a machine's sessions to
another machine, on the same layout:

- **Bundles:** ``archive export`` writes a zip (optionally encrypted with a
  passphrase); ``archive import`` adds its files under ``machines/<id>/``.
- **The shared sync folder** (``shared_archive_dir``): the watcher writes
  this machine's allowed files to ``<shared>/machines/<own id>/`` and copies
  every other machine's folder into ``<data_dir>/machines/<id>/``.

Nothing leaves a machine by default: the export rules
(``~/.claude-explorer/export-rules.json``) start in mode "off". An import
and a pull only add and append (CLAUDE.md principle 2).

The MCP closure imports this module through ``backend.write_gate``, so the
heavy imports (``cryptography``, the store, the readers) stay inside the
functions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.archive_sync import (
    LOCK_FILE_NAME,
    MACHINE_FILE,
    MACHINE_ID_RE,
    MACHINES_DIR,
    ArchiveLock,
    SyncReason,
    SyncReport,
    _read_meta,
    own_machine_id,
    read_identity,
    sync_small_file,
    sync_stream,
    write_machine_marker,
)
from backend.config import canonical_home_dir, get_settings
from fetcher.atomic import atomic_write_bytes

if TYPE_CHECKING:
    from backend.config import Settings

log = logging.getLogger(__name__)

RULES_FILE = "export-rules.json"
STATE_FILE = "export-state.json"
LABELS_FILE = "machine-labels.json"
MANIFEST_FILE = "manifest.json"
BUNDLE_MAGIC = b"CEXB1"
BUNDLE_SUFFIX = ".cebundle"
MAX_LABEL = 64
#: The folders of a machine that a bundle or the shared folder carries.
PARTS = ("claude-code", "cowork", "codex")
#: Under this machine's folder: the copy metadata of the push to the shared folder.
PUSH_STATE_DIR = "shared-push"

Source = Literal["claude-code", "cowork", "codex"]
BundleReason = Literal[
    "export_off", "not_a_bundle", "bad_hash", "passphrase_required",
    "bad_passphrase", "own_machine", "missing_base",
]

_CHUNK = 1 << 20
_SALT = 16
_NONCE_PREFIX = 8
_FRAME_MAX = _CHUNK + 16
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 << 20, "dklen": 32}
_HOME_DIR_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A pass that a person waits for stops after this time (as in archive_sync).
_PASS_BUDGET_S: dict[str, float] = {"refresh": 20.0, "cli": 20.0}


class BundleError(Exception):
    """A refused export or import. ``reason`` is a stable code; nothing was written."""

    MESSAGES: ClassVar[dict[str, str]] = {
        "export_off": "Export is off. Set the export mode to \"Export\" first.",
        "not_a_bundle": "This file is not a Claude Explorer bundle.",
        "bad_hash": "The bundle is damaged: a file does not match its hash.",
        "passphrase_required": "This bundle is encrypted. Give its passphrase.",
        "bad_passphrase": "The passphrase is wrong.",
        "own_machine": "This bundle comes from this machine.",
        "missing_base": "The bundle continues files that this archive does not have.",
    }

    def __init__(self, reason: BundleReason, detail: str | None = None) -> None:
        self.reason = reason
        super().__init__(detail or self.MESSAGES[reason])


# --- Export rules -----------------------------------------------------------


class AllowRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A missing field matches every source.
    source: Source | None = None
    #: A glob on the project folder; "~" expands, "**" crosses "/".
    project: str | None = None


class ExportRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    mode: Literal["off", "export", "receive-only"] = "off"
    allow: list[AllowRule] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    #: "YYYY-MM-DD": only sessions whose last record is on or after this day.
    since: str | None = None

    @field_validator("since")
    @classmethod
    def _check_since(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not _DATE_RE.fullmatch(value):
            raise ValueError("since must be YYYY-MM-DD")
        date.fromisoformat(value)
        return value


def _rules_path() -> Path:
    return canonical_home_dir() / RULES_FILE


def load_export_rules() -> ExportRules:
    """The export rules; defaults (mode "off") for a missing or bad file. Never raises."""
    path = _rules_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ExportRules()
    except OSError as exc:
        log.warning("cannot read %s: %s; nothing is exported", path, exc)
        return ExportRules()
    try:
        return ExportRules.model_validate_json(raw)
    except ValueError as exc:
        log.warning("%s is not valid: %s; nothing is exported", path, exc)
        return ExportRules()


def save_export_rules(rules: ExportRules) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, (rules.model_dump_json(indent=2) + "\n").encode("utf-8"))


def _fold(text: str) -> str:
    return text.casefold() if sys.platform in ("darwin", "win32") else text


def _glob_re(pattern: str) -> re.Pattern[str]:
    """A glob as a regex: "*" and "?" stay inside one folder, "**" crosses "/"."""
    p = pattern.strip().replace("\\", "/")
    if p == "~" or p.startswith("~/"):
        p = Path.home().as_posix() + p[1:]
    out, i = [], 0
    while i < len(p):
        if p.startswith("/**", i) and (i + 3 == len(p) or p[i + 3] == "/"):
            out.append("(?:/.*)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            c = p[i]
            out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
            i += 1
    return re.compile(_fold("".join(out)))


def _glob_match(pattern: str, project: str | None) -> bool:
    if not project:
        return False
    norm = project.replace("\\", "/").rstrip("/") or "/"
    return _glob_re(pattern).fullmatch(_fold(norm)) is not None


def session_allowed(rules: ExportRules, source: str, project: str | None, last: datetime | None) -> bool:
    """True when the rules let this session leave the machine (deny wins)."""
    if rules.mode != "export":
        return False
    if not any(
        (r.source is None or r.source == source) and (r.project is None or _glob_match(r.project, project))
        for r in rules.allow
    ):
        return False
    if any(_glob_match(d, project) for d in rules.deny):
        return False
    if rules.since:
        if last is None:
            return False
        last = last if last.tzinfo else last.replace(tzinfo=UTC)
        if last.astimezone(UTC).date() < date.fromisoformat(rules.since):
            return False
    return True


# --- The sessions of this machine ---------------------------------------------


@dataclass
class _Session:
    source: Source
    project: str | None
    last: datetime | None
    #: (bundle path under machines/<id>/, the file to read)
    files: list[tuple[str, Path]]


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


# (kind, path) -> ((size, mtime_ns), (project, last)); the rules read every session.
_info_memo: dict[tuple[str, Path], tuple[tuple[int, int], tuple[str | None, datetime | None] | None]] = {}


def _info(kind: str, path: Path, stat_path: Path, read: Any) -> tuple[str | None, datetime | None] | None:
    try:
        st = stat_path.stat()
    except OSError:
        return None
    key = (st.st_size, st.st_mtime_ns)
    hit = _info_memo.get((kind, path))
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        data = read(path)
    except Exception:  # one bad session must not stop an export
        log.exception("export: cannot read %s", path)
        data = None
    value = (data.get("project_path") or None, _parse_time(data.get("updated_at"))) if data else None
    _info_memo[(kind, path)] = (key, value)
    return value


def _own_folder(data_dir: Path) -> Path | None:
    mid = own_machine_id()
    return data_dir / MACHINES_DIR / mid if mid else None


def _cc_sessions(claude_dir: Path, own: Path | None) -> list[_Session]:
    from backend.cc_jsonl_io import discover_jsonl_files, read_conversation_summary_fast
    from backend.cc_subagents import iter_agent_files, owner_of

    roots = [claude_dir, *([own / "claude-code"] if own else [])]
    mains: dict[str, Path] = {}
    agents: dict[str, dict[str, Path]] = {}
    for root in roots:
        projects = root / "projects"
        for p in discover_jsonl_files(root):
            mains.setdefault(p.relative_to(projects).as_posix(), p)
        for a in iter_agent_files(root):
            found = owner_of(a)
            if found is None:
                continue
            try:
                owner = found[0].relative_to(projects).as_posix()
                rel = a.relative_to(projects).as_posix()
            except ValueError:
                continue
            agents.setdefault(owner, {}).setdefault(rel, a)
    out = []
    for rel, path in sorted(mains.items()):
        info = _info("cc", path, path, read_conversation_summary_fast)
        if info is None:
            continue
        files = [(f"claude-code/projects/{rel}", path)]
        for arel, apath in sorted(agents.get(rel, {}).items()):
            files.append((f"claude-code/projects/{arel}", apath))
            meta = apath.with_suffix(".meta.json")
            if meta.is_file():
                files.append((f"claude-code/projects/{arel[:-6]}.meta.json", meta))
        out.append(_Session("claude-code", info[0], info[1], files))
    return out


def _cowork_sessions(cowork_root: Path | None, own: Path | None) -> list[_Session]:
    from backend.cowork_reader import iter_cowork_session_dirs, read_cowork_conversation

    dirs: dict[str, Path] = {}
    for root in (cowork_root, *([own / "cowork"] if own else [])):
        if root is None:
            continue
        for d in iter_cowork_session_dirs(root):
            dirs.setdefault(d.relative_to(root).as_posix(), d)
    out = []
    for rel, d in sorted(dirs.items()):
        audit = d / "audit.jsonl"
        info = _info("cowork", d, audit, read_cowork_conversation)
        if info is None:
            continue
        files = [(f"cowork/{rel}/audit.jsonl", audit)]
        sidecar = d.with_suffix(".json")
        if sidecar.is_file():
            files.append((f"cowork/{rel}.json", sidecar))
        out.append(_Session("cowork", info[0], info[1], files))
    return out


def _codex_sessions(data_dir: Path) -> list[_Session]:
    from backend.sources.codex import ARCHIVE_DIR, HOME_FILE, ROLLOUTS_DIR, THREADS_DIR

    root = data_dir / ARCHIVE_DIR
    try:
        homes = sorted(p for p in root.iterdir() if _HOME_DIR_RE.fullmatch(p.name) and p.is_dir())
    except OSError:
        return []
    out = []
    for home in homes:
        home_file = home / HOME_FILE
        try:
            threads = sorted((home / THREADS_DIR).glob("*.json"))
        except OSError:
            continue
        for t in threads:
            data = _read_meta(t)
            if data is None:
                continue
            files = [(f"codex/{home.name}/{HOME_FILE}", home_file)] if home_file.is_file() else []
            files.append((f"codex/{home.name}/{THREADS_DIR}/{t.name}", t))
            for r in data.get("rollouts") or []:
                stem = r.get("stem") if isinstance(r, dict) else None
                rollout = home / ROLLOUTS_DIR / f"{stem}.jsonl"
                if isinstance(stem, str) and _STEM_RE.fullmatch(stem) and rollout.is_file():
                    files.append((f"codex/{home.name}/{ROLLOUTS_DIR}/{stem}.jsonl", rollout))
            ms = data.get("updated_at_ms")
            last = datetime.fromtimestamp(ms / 1000, UTC) if isinstance(ms, int) and not isinstance(ms, bool) else None
            cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
            out.append(_Session("codex", cwd, last, files))
    return out


def _sessions() -> list[_Session]:
    from backend.store import ConversationStore

    store = ConversationStore()
    own = _own_folder(store.data_dir)
    return [
        *_cc_sessions(store.claude_dir, own),
        *_cowork_sessions(store.cowork_root, own),
        *_codex_sessions(store.data_dir),
    ]


def _allowed_files(rules: ExportRules) -> list[tuple[str, Path, _Session]]:
    """Every file that leaves this machine, once each, in session order."""
    out, seen = [], set()
    for s in _sessions():
        if not session_allowed(rules, s.source, s.project, s.last):
            continue
        for bpath, src in s.files:
            if bpath not in seen:
                seen.add(bpath)
                out.append((bpath, src, s))
    return out


# --- The export plan ----------------------------------------------------------


class PreviewFile(BaseModel):
    path: str
    bytes: int
    source: Source
    project: str | None = None


class ExportPreview(BaseModel):
    files: list[PreviewFile]
    file_count: int
    total_bytes: int


class ExportResult(BaseModel):
    path: str
    file_count: int
    total_bytes: int
    encrypted: bool


@dataclass
class _Part:
    path: str
    src: Path
    session: _Session
    offset: int
    length: int
    size: int
    mtime_ns: int
    hasher: Any  # hashlib sha256 of the bytes before offset
    data: bytes | None = None  # a small file, read whole


def _state_path() -> Path:
    return canonical_home_dir() / STATE_FILE


def _load_state(mid: str) -> dict[str, dict[str, Any]]:
    data = _read_meta(_state_path())
    if not data or data.get("machine_id") != mid or not isinstance(data.get("files"), dict):
        return {}
    return {k: v for k, v in data["files"].items() if isinstance(v, dict)}


def _complete_length(path: Path, size: int) -> int:
    """The byte count up to the last newline: a last line without one can still grow."""
    with open(path, "rb") as fh:
        end = size
        while end > 0:
            start = max(0, end - 65536)
            fh.seek(start)
            block = fh.read(end - start)
            cut = block.rfind(b"\n")
            if cut != -1:
                return start + cut + 1
            end = start
    return 0


def _hash_prefix(path: Path, length: int) -> Any:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        left = length
        while left > 0:
            block = fh.read(min(left, _CHUNK))
            if not block:
                break
            h.update(block)
            left -= len(block)
    return h


def _plan_file(bpath: str, src: Path, session: _Session, entry: dict[str, Any] | None) -> _Part | None:
    try:
        st = src.stat()
    except OSError:
        return None
    if not bpath.endswith(".jsonl"):
        try:
            data = src.read_bytes()
        except OSError:
            return None
        if entry and entry.get("sha256") == hashlib.sha256(data).hexdigest() and entry.get("bytes") == len(data):
            return None
        return _Part(bpath, src, session, 0, len(data), st.st_size, st.st_mtime_ns, hashlib.sha256(), data)
    if entry and entry.get("size") == st.st_size and entry.get("mtime_ns") == st.st_mtime_ns:
        return None
    try:
        complete = _complete_length(src, st.st_size)
    except OSError:
        return None
    done = entry.get("bytes") if entry else None
    if isinstance(done, int) and 0 < done <= complete:
        h = _hash_prefix(src, done)
        if h.hexdigest() == entry.get("sha256"):
            if done == complete:
                return None
            return _Part(bpath, src, session, done, complete - done, st.st_size, st.st_mtime_ns, h)
    if complete == 0:
        return None
    return _Part(bpath, src, session, 0, complete, st.st_size, st.st_mtime_ns, hashlib.sha256())


def _plan(full: bool) -> tuple[str | None, list[_Part]]:
    rules = load_export_rules()
    if rules.mode != "export":
        return None, []
    mid = own_machine_id(create=True)
    if mid is None:
        return None, []
    state = {} if full else _load_state(mid)
    parts = []
    for bpath, src, session in _allowed_files(rules):
        part = _plan_file(bpath, src, session, state.get(bpath))
        if part is not None:
            parts.append(part)
    return mid, parts


def preview_export(*, full: bool = False) -> ExportPreview:
    """What :func:`export_bundle` would write now; empty unless the mode is "export"."""
    _, parts = _plan(full)
    files = [PreviewFile(path=p.path, bytes=p.length, source=p.session.source, project=p.session.project)
             for p in parts]
    return ExportPreview(files=files, file_count=len(files), total_bytes=sum(f.bytes for f in files))


def default_bundle_name(now: datetime | None = None) -> str:
    """``claude-explorer-<label or id8>-<YYYYMMDD>.cebundle``, safe as a file name."""
    mid, label = read_identity()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", label or "").strip("-.") or (mid or "machine")[:8]
    return f"claude-explorer-{name}-{(now or datetime.now(UTC)).strftime('%Y%m%d')}{BUNDLE_SUFFIX}"


def _write_part(zf: zipfile.ZipFile, name: str, part: _Part) -> str:
    h = hashlib.sha256()
    with zf.open(name, "w", force_zip64=True) as out:
        if part.data is not None:
            out.write(part.data)
            h.update(part.data)
            return h.hexdigest()
        with open(part.src, "rb") as fh:
            fh.seek(part.offset)
            left = part.length
            while left > 0:
                block = fh.read(min(left, _CHUNK))
                if not block:
                    raise OSError(f"{part.src} became shorter during the export")
                out.write(block)
                h.update(block)
                part.hasher.update(block)
                left -= len(block)
    return h.hexdigest()


def export_bundle(dest: Path, *, passphrase: str | None = None, full: bool = False) -> ExportResult:
    """Write a bundle of this machine's allowed sessions to ``dest`` (a file or a folder).

    Incremental by default: a file that an earlier export carried gets only
    its new bytes (``export-state.json``). ``full`` carries everything.
    """
    if load_export_rules().mode != "export":
        raise BundleError("export_off")
    mid, parts = _plan(full)
    if mid is None:
        raise BundleError("export_off", "This machine has no id yet.")
    dest = Path(dest)
    if dest.is_dir():
        dest = dest / default_bundle_name()
    label = read_identity()[1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".bundle-", dir=dest.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        entries = []
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            base = f"{MACHINES_DIR}/{mid}"
            zf.writestr(f"{base}/{MACHINE_FILE}", json.dumps(
                {"version": 1, "machine_id": mid, "label": label}, indent=2, ensure_ascii=False))
            for part in parts:
                sha = _write_part(zf, f"{base}/{part.path}", part)
                entries.append({"path": part.path, "sha256": sha, "bytes": part.length, "offset": part.offset})
            manifest = {
                "version": 1, "machine_id": mid, "label": label,
                "exported_at": datetime.now(UTC).isoformat(),
                "encrypted": bool(passphrase), "files": entries,
            }
            zf.writestr(f"{base}/{MANIFEST_FILE}", json.dumps(manifest, indent=2, ensure_ascii=False))
        if passphrase:
            enc = tmp.with_name(tmp.name + ".enc")
            try:
                with open(tmp, "rb") as src, open(enc, "wb") as out:
                    _encrypt(src, out, passphrase)
                os.replace(enc, dest)
            finally:
                enc.unlink(missing_ok=True)
        else:
            os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    _save_state(mid, parts, full)
    return ExportResult(path=str(dest), file_count=len(parts), total_bytes=sum(p.length for p in parts),
                        encrypted=bool(passphrase))


def _save_state(mid: str, parts: list[_Part], full: bool) -> None:
    files = {} if full else _load_state(mid)
    for p in parts:
        if p.data is not None:
            files[p.path] = {"bytes": p.length, "sha256": hashlib.sha256(p.data).hexdigest()}
        else:
            files[p.path] = {"bytes": p.offset + p.length, "sha256": p.hasher.hexdigest(),
                             "size": p.size, "mtime_ns": p.mtime_ns}
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, json.dumps({"version": 1, "machine_id": mid, "files": files}).encode("utf-8"))


# --- Encryption: scrypt key, AES-GCM frames ---------------------------------------


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, **_SCRYPT)


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + index.to_bytes(4, "big") + (b"\x01" if final else b"\x00")


def _encrypt(src: IO[bytes], out: IO[bytes], passphrase: str) -> None:
    """``MAGIC, salt, nonce prefix``, then frames of ``length, AES-GCM(chunk)``.

    The last frame is marked in its associated data, so a cut file fails.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, prefix = os.urandom(_SALT), os.urandom(_NONCE_PREFIX)
    header = BUNDLE_MAGIC + salt + prefix
    aes = AESGCM(_key(passphrase, salt))
    out.write(header)
    index, chunk = 0, src.read(_CHUNK)
    while True:
        following = src.read(_CHUNK) if chunk else b""
        final = not following
        sealed = aes.encrypt(prefix + index.to_bytes(4, "big"), chunk, _aad(header, index, final))
        out.write(len(sealed).to_bytes(4, "big") + sealed)
        if final:
            return
        chunk, index = following, index + 1


def _decrypt(src: IO[bytes], out: IO[bytes], passphrase: str) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header = src.read(len(BUNDLE_MAGIC) + _SALT + _NONCE_PREFIX)
    if len(header) != len(BUNDLE_MAGIC) + _SALT + _NONCE_PREFIX:
        raise BundleError("not_a_bundle")
    salt, prefix = header[len(BUNDLE_MAGIC):-_NONCE_PREFIX], header[-_NONCE_PREFIX:]
    aes = AESGCM(_key(passphrase, salt))
    index = 0
    size = src.read(4)
    while True:
        if len(size) != 4:
            raise BundleError("bad_hash", "The encrypted bundle is cut short.")
        n = int.from_bytes(size, "big")
        if n > _FRAME_MAX:
            raise BundleError("bad_hash")
        sealed = src.read(n)
        size = src.read(4)
        final = size == b""
        try:
            out.write(aes.decrypt(prefix + index.to_bytes(4, "big"), sealed, _aad(header, index, final)))
        except InvalidTag:
            raise BundleError("bad_passphrase" if index == 0 else "bad_hash") from None
        if final:
            return
        index += 1


# --- Import -----------------------------------------------------------------------


class ImportResult(BaseModel):
    machine_id: str
    label: str | None = None
    counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


def valid_relpath(rel: str) -> bool:
    """True for a path that a machine folder may hold (a bundle entry or a shared file).

    The path comes from another machine, so it must stay inside the
    folder and match the layout; the copier's own files never travel.
    """
    if not isinstance(rel, str) or "\\" in rel or ":" in rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts) or parts[0] not in PARTS:
        return False
    name = parts[-1]
    if not name.endswith((".jsonl", ".json")) or ".rev-" in name:
        return False
    if name.endswith(".jsonl") and not _STEM_RE.fullmatch(name[:-6]):
        return False
    if parts[0] == "claude-code":
        if len(parts) < 4 or parts[1] != "projects":
            return False
        return not name.endswith(".meta.json") or name.startswith("agent-")
    if parts[0] == "cowork":
        return len(parts) >= 3 and parts[1] != "sync" and not name.endswith(".meta.json")
    # codex/<home>/home.json, codex/<home>/threads/<id>.json, codex/<home>/rollouts/<stem>.jsonl
    if not _HOME_DIR_RE.fullmatch(parts[1]):
        return False
    if len(parts) == 3:
        return name == "home.json"
    return len(parts) == 4 and (
        (parts[2] == "threads" and name.endswith(".json") and not name.endswith(".meta.json"))
        or (parts[2] == "rollouts" and name.endswith(".jsonl"))
    )


def _side_dir(machine: Path, rel: PurePosixPath) -> Path:
    """Where the copy metadata and revisions of ``rel`` go (outside the mirrors)."""
    if rel.parts[0] == "claude-code":
        return machine.joinpath("claude-code", "sync", *rel.parts[2:-1])
    if rel.parts[0] == "cowork":
        return machine.joinpath("cowork", "sync", *rel.parts[1:-1])
    return machine.joinpath(*rel.parts[:-1])


def _inside(root: Path, path: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))
    except (OSError, ValueError):
        return False


def _clean_label(value: object) -> str | None:
    try:
        return _sanitize_label(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _read_bundle(zf: zipfile.ZipFile) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """``(machine id, manifest, entries)`` of a bundle, checked; else ``not_a_bundle``."""
    found = [m for n in zf.namelist()
             if (m := re.fullmatch(rf"{MACHINES_DIR}/([0-9a-f-]{{36}})/{MANIFEST_FILE}", n))]
    if len(found) != 1 or not MACHINE_ID_RE.fullmatch(found[0].group(1)):
        raise BundleError("not_a_bundle")
    mid = found[0].group(1)
    try:
        manifest = json.loads(zf.read(found[0].group(0)))
        machine = json.loads(zf.read(f"{MACHINES_DIR}/{mid}/{MACHINE_FILE}"))
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise BundleError("not_a_bundle") from exc
    if (not isinstance(manifest, dict) or not isinstance(machine, dict)
            or manifest.get("version") != 1 or manifest.get("machine_id") != mid
            or machine.get("machine_id") != mid or not isinstance(manifest.get("files"), list)):
        raise BundleError("not_a_bundle")
    entries, seen = [], set()
    for e in manifest["files"]:
        ok = (isinstance(e, dict) and valid_relpath(e.get("path"))
              and isinstance(e.get("sha256"), str) and _SHA_RE.fullmatch(e["sha256"])
              and all(isinstance(e.get(k), int) and not isinstance(e.get(k), bool) and e[k] >= 0
                      for k in ("bytes", "offset"))
              and (e["offset"] == 0 or e["path"].endswith(".jsonl"))
              and e["path"] not in seen)
        if not ok:
            raise BundleError("not_a_bundle")
        seen.add(e["path"])
        entries.append(e)
    return mid, {**manifest, "label": _clean_label(machine.get("label"))}, entries


def _member_chunks(zf: zipfile.ZipFile, name: str) -> Iterator[bytes]:
    with zf.open(name) as fh:
        while block := fh.read(_CHUNK):
            yield block


def _verify(zf: zipfile.ZipFile, mid: str, entries: list[dict[str, Any]]) -> None:
    for e in entries:
        h, n = hashlib.sha256(), 0
        try:
            for block in _member_chunks(zf, f"{MACHINES_DIR}/{mid}/{e['path']}"):
                h.update(block)
                n += len(block)
        except (KeyError, zipfile.BadZipFile, OSError, EOFError) as exc:
            raise BundleError("bad_hash") from exc
        if n != e["bytes"] or h.hexdigest() != e["sha256"]:
            raise BundleError("bad_hash")


def _counts() -> dict[str, int]:
    return {"copied": 0, "appended": 0, "unchanged": 0, "revisions": 0, "skipped": 0}


def _apply(zf: zipfile.ZipFile, bundle: Path, data_dir: Path, mid: str,
           manifest: dict[str, Any], entries: list[dict[str, Any]]) -> ImportResult:
    machine = data_dir / MACHINES_DIR / mid
    lock = ArchiveLock(machine / LOCK_FILE_NAME)
    if not lock.acquire(60.0):
        raise TimeoutError("another copy of this machine's folder is running; try again")
    counts, warnings = _counts(), []
    report = SyncReport()
    try:
        for e in entries:
            rel = PurePosixPath(e["path"])
            dest = machine.joinpath(*rel.parts)
            name = f"{MACHINES_DIR}/{mid}/{e['path']}"
            if not _inside(machine, dest.parent if dest.parent.exists() else machine):
                counts["skipped"] += 1
                warnings.append(f"skipped {e['path']}: it leaves the machine folder")
                continue
            if rel.suffix == ".json":
                data = zf.read(name)
                try:
                    same = dest.read_bytes() == data
                except OSError:
                    same = False
                if same:
                    counts["unchanged"] += 1
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_bytes(dest, data)
                    counts["copied"] += 1
            elif e["offset"] == 0:
                before = dict(report.counts)
                sync_stream(report, dest_dir=dest.parent, stem=rel.name[:-6], source_path=bundle,
                            decode=lambda _p, n=name: _member_chunks(zf, n),
                            side_dir=_side_dir(machine, rel))
                for k in ("copied", "appended", "unchanged", "revisions"):
                    counts[k] += report.counts[k] - before[k]
                if report.counts["decode_errors"] > before["decode_errors"]:
                    counts["skipped"] += 1
            else:
                _apply_part(zf, name, dest, e, counts, warnings)
        write_machine_marker(machine, mid, manifest.get("label"))
        atomic_write_bytes(machine / MANIFEST_FILE, json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
    finally:
        lock.release()
    warnings.extend(report.warnings)
    return ImportResult(machine_id=mid, label=manifest.get("label"), counts=counts, warnings=warnings)


def _apply_part(zf: zipfile.ZipFile, name: str, dest: Path, e: dict[str, Any],
                counts: dict[str, int], warnings: list[str]) -> None:
    """An incremental part: append it when the copy ends at its offset."""
    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    if size == e["offset"]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "ab") as out:
            out.writelines(_member_chunks(zf, name))
            out.flush()
            os.fsync(out.fileno())
        counts["appended"] += 1
        return
    if size >= e["offset"] + e["bytes"]:
        with open(dest, "rb") as fh:
            fh.seek(e["offset"])
            if all(fh.read(len(block)) == block for block in _member_chunks(zf, name)):
                counts["unchanged"] += 1
                return
    counts["skipped"] += 1
    warnings.append(f"missing_base: {e['path']} continues at byte {e['offset']}, "
                    f"but this archive has {size} bytes of it; import the earlier bundle first")


def import_bundle(src: Path, *, passphrase: str | None = None) -> ImportResult:
    """Add the files of a bundle under ``machines/<its id>/``, append-only.

    Every check runs before the first write, so a refused bundle
    (:class:`BundleError`) writes nothing.
    """
    src = Path(src)
    data_dir = get_settings().data_dir
    with tempfile.TemporaryDirectory(prefix="claude-explorer-import-") as tmp:
        try:
            with open(src, "rb") as fh:
                head = fh.read(len(BUNDLE_MAGIC))
                if head == BUNDLE_MAGIC:
                    if not passphrase:
                        raise BundleError("passphrase_required")
                    fh.seek(0)
                    zpath = Path(tmp) / "bundle.zip"
                    with open(zpath, "wb") as out:
                        _decrypt(fh, out, passphrase)
                else:
                    zpath = src
        except OSError as exc:
            raise BundleError("not_a_bundle", f"Cannot read the bundle: {exc}") from exc
        try:
            zf = zipfile.ZipFile(zpath)
        except (zipfile.BadZipFile, OSError) as exc:
            raise BundleError("not_a_bundle") from exc
        with zf:
            mid, manifest, entries = _read_bundle(zf)
            if mid == own_machine_id():
                raise BundleError("own_machine")
            _verify(zf, mid, entries)
            return _apply(zf, src, data_dir, mid, manifest, entries)


# --- Labels -----------------------------------------------------------------------


class MachineInfo(BaseModel):
    machine_id: str
    label: str
    is_this_machine: bool
    session_count: int = 0


def _sanitize_label(label: str | None) -> str | None:
    if label is None:
        return None
    text = label.strip()
    if not text:
        return None
    if len(text) > MAX_LABEL:
        raise ValueError(f"a label has at most {MAX_LABEL} characters")
    if any(unicodedata.category(c).startswith("C") for c in text):
        raise ValueError("a label cannot hold control characters")
    return text


def _labels_path() -> Path:
    return canonical_home_dir() / LABELS_FILE


def _local_labels() -> dict[str, str]:
    labels = _read_meta(_labels_path()) or {}  # {machine_id: label}
    return {k: v for k, v in labels.items() if MACHINE_ID_RE.fullmatch(k) and isinstance(v, str) and v}


# data_dir -> (time, {id: shown label}); every row asks. The store clears it at
# the start of each list and viewer request, so a request never shows an old label.
_label_memo: dict[str, tuple[float, dict[str, str]]] = {}


def _machine_ids(data_dir: Path) -> list[str]:
    try:
        return sorted(n for n in os.listdir(data_dir / MACHINES_DIR) if MACHINE_ID_RE.fullmatch(n))
    except OSError:
        return []


def shown_labels(data_dir: Path) -> dict[str, str]:
    """The shown label of each known machine: the local name, its own label, or "Machine <id8>"."""
    hit = _label_memo.get(str(data_dir))
    if hit is not None and time.monotonic() - hit[0] < 1.0:
        return hit[1]
    own, own_label = read_identity()
    local = _local_labels()
    out = {}
    for mid in {*_machine_ids(data_dir), *([own] if own else []), *local}:
        if mid == own:
            label = own_label
        else:
            label = _clean_label((_read_meta(data_dir / MACHINES_DIR / mid / MACHINE_FILE) or {}).get("label"))
        out[mid] = local.get(mid) if mid != own and local.get(mid) else (label or f"Machine {mid[:8]}")
    _label_memo[str(data_dir)] = (time.monotonic(), out)
    return out


def shown_label(data_dir: Path, mid: str) -> str:
    return shown_labels(data_dir).get(mid) or f"Machine {mid[:8]}"


def clear_label_memo() -> None:
    _label_memo.clear()


def set_machine_label(label: str | None) -> MachineInfo:
    """Set this machine's own label (the identity file and ``machines/<own id>/machine.json``)."""
    clean = _sanitize_label(label)
    mid = own_machine_id(create=True)
    if mid is None:
        raise OSError("cannot write the machine identity file")
    ident = canonical_home_dir() / MACHINE_FILE
    body = {**(_read_meta(ident) or {}), "version": 1, "machine_id": mid, "label": clean}
    atomic_write_bytes(ident, json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8"))
    machine = get_settings().data_dir / MACHINES_DIR / mid
    if machine.is_dir():  # made by the first copy, which passed the write gates
        write_machine_marker(machine, mid, clean)
    clear_label_memo()
    return _info_of(mid)


def set_local_label(machine_id: str, label: str | None) -> MachineInfo:
    """A local name for another machine; it never leaves this machine."""
    if not MACHINE_ID_RE.fullmatch(machine_id or ""):
        raise ValueError("not a machine id")
    if machine_id == own_machine_id():
        raise ValueError("use the own label for this machine")
    clean = _sanitize_label(label)
    labels = _local_labels()
    if clean is None:
        labels.pop(machine_id, None)
    else:
        labels[machine_id] = clean
    path = _labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, json.dumps(labels, indent=2, ensure_ascii=False).encode("utf-8"))
    clear_label_memo()
    return _info_of(machine_id)


def _session_counts() -> dict[str | None, int]:
    from backend.store import ConversationStore

    counts: dict[str | None, int] = {}
    for row in ConversationStore().list_conversations(show_archived=True):
        counts[row.machine_id] = counts.get(row.machine_id, 0) + 1
    return counts


def _info_of(mid: str, counts: dict[str | None, int] | None = None) -> MachineInfo:
    data_dir = get_settings().data_dir
    return MachineInfo(machine_id=mid, label=shown_label(data_dir, mid),
                       is_this_machine=mid == own_machine_id(),
                       session_count=(counts or {}).get(mid, 0))


def list_machines() -> list[MachineInfo]:
    """This machine first, then the others by label, then by id."""
    data_dir = get_settings().data_dir
    own = own_machine_id(create=True)
    ids = [m for m in _machine_ids(data_dir) if m != own]
    counts = _session_counts()
    others = sorted((_info_of(m, counts) for m in ids), key=lambda i: (_fold(i.label), i.machine_id))
    return [*([_info_of(own, counts)] if own else []), *others]


# --- The shared sync folder ---------------------------------------------------------


def _deadline(reason: SyncReason) -> float | None:
    budget = _PASS_BUDGET_S.get(reason)
    return time.monotonic() + budget if budget is not None else None


def _copy_file(report: SyncReport, machine: Path, rel: PurePosixPath, src: Path, side: Path) -> None:
    dest = machine.joinpath(*rel.parts)
    if rel.suffix == ".jsonl":
        try:
            sync_stream(report, dest_dir=dest.parent, stem=rel.name[:-6], source_path=src,
                        decode=_plain, side_dir=side)
        except ValueError as exc:
            report.warn(f"shared folder: skipped {rel}: {exc}")
    else:
        sync_small_file(report, source_path=src, dest_path=dest)


def _plain(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while block := fh.read(_CHUNK):
            yield block


def _push(settings: Settings, own: str, report: SyncReport, lock_timeout_s: float, deadline: float | None) -> None:
    """Write this machine's allowed files to ``<shared>/machines/<own>/``."""
    target = settings.shared_archive_dir / MACHINES_DIR / own
    lock = ArchiveLock(target / LOCK_FILE_NAME)
    if not lock.acquire(lock_timeout_s):
        report.skipped_reason = "busy" if lock_timeout_s <= 0 else "timeout"
        return
    try:
        write_machine_marker(target, own, read_identity()[1])
        state = settings.data_dir / MACHINES_DIR / own / PUSH_STATE_DIR
        for bpath, src, _session in _allowed_files(load_export_rules()):
            if deadline is not None and time.monotonic() >= deadline:
                return
            rel = PurePosixPath(bpath)
            _copy_file(report, target, rel, src, state.joinpath(*rel.parts[:-1]))
    finally:
        lock.release()


def _shared_files(folder: Path) -> Iterator[tuple[PurePosixPath, Path]]:
    for part in PARTS:
        for dirpath, dirnames, filenames in os.walk(folder / part):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(folder).as_posix()
                if valid_relpath(rel) and not path.is_symlink():
                    yield PurePosixPath(rel), path


def _pull(settings: Settings, own: str | None, report: SyncReport, lock_timeout_s: float,
          deadline: float | None) -> None:
    """Copy every other machine's folder of the shared folder into ``data_dir``, append-only."""
    root = settings.shared_archive_dir / MACHINES_DIR
    for mid in _machine_ids(settings.shared_archive_dir):
        folder = root / mid
        if mid == own or folder.is_symlink() or not folder.is_dir():
            continue
        machine = settings.data_dir / MACHINES_DIR / mid
        lock = ArchiveLock(machine / LOCK_FILE_NAME)
        if not lock.acquire(lock_timeout_s):
            report.warn(f"shared folder: machine {mid[:8]} is busy; the next pass copies it")
            continue
        try:
            marker = _read_meta(folder / MACHINE_FILE) or {}
            if marker.get("machine_id") == mid:
                write_machine_marker(machine, mid, _clean_label(marker.get("label")))
            for rel, path in _shared_files(folder):
                if deadline is not None and time.monotonic() >= deadline:
                    return
                _copy_file(report, machine, rel, path, _side_dir(machine, rel))
        finally:
            lock.release()


def sync_shared_folder(settings: Settings, reason: SyncReason, lock_timeout_s: float) -> SyncReport | None:
    """One pass over the shared folder: push (mode "export" only), then pull (every mode).

    ``None`` without a shared folder, and for MCP (it does not copy). The
    push waits for the D13 consent of the shared folder; the pull writes only
    ``data_dir``, which the caller's gates already checked.
    """
    if reason == "mcp":
        return None
    # A corrupt config can hide the shared folder, so this check comes first.
    if settings.config_corrupt_reason:
        return SyncReport(skipped_reason="corrupt_config")
    shared = settings.shared_archive_dir
    if shared is None:
        return None
    from backend.write_gate import cloud_sync_message, shared_archive_block

    report = SyncReport()
    deadline = _deadline(reason)
    mode = load_export_rules().mode
    own = own_machine_id(create=mode == "export")
    if mode == "export" and own:
        block = shared_archive_block(settings)
        if block is not None:
            report.skipped_reason = "cloud_sync_gate"
            report.warn(f"shared folder: {cloud_sync_message(block)}")
        else:
            try:
                _push(settings, own, report, lock_timeout_s, deadline)
            except OSError as exc:
                report.warn(f"shared folder: cannot write {shared}: {exc}")
    if shared.is_dir():
        try:
            _pull(settings, own, report, lock_timeout_s, deadline)
        except OSError as exc:
            report.warn(f"shared folder: cannot read {shared}: {exc}")
    return report


def remove_own_shared_folder(settings: Settings, *, confirm: str) -> Path:
    """Delete ``<shared>/machines/<own id>/`` (only that folder) after a confirmation.

    ``confirm`` must equal this machine's id. What other machines already
    copied stays with them. With mode "export", the next pass sends again.
    """
    own = own_machine_id()
    if not own or confirm != own:
        raise ValueError("confirm must equal this machine's id")
    if settings.shared_archive_dir is None:
        raise ValueError("no shared folder is set")
    target = settings.shared_archive_dir / MACHINES_DIR / own
    if target.is_symlink():
        raise ValueError(f"{target} is a symbolic link")
    if target.exists():
        shutil.rmtree(target)
    # The push metadata says what the folder held; without it the next push starts again.
    shutil.rmtree(settings.data_dir / MACHINES_DIR / own / PUSH_STATE_DIR, ignore_errors=True)
    return target


__all__ = [
    "AllowRule",
    "BundleError",
    "ExportPreview",
    "ExportResult",
    "ExportRules",
    "ImportResult",
    "MachineInfo",
    "PreviewFile",
    "default_bundle_name",
    "export_bundle",
    "import_bundle",
    "list_machines",
    "load_export_rules",
    "preview_export",
    "remove_own_shared_folder",
    "save_export_rules",
    "session_allowed",
    "set_local_label",
    "set_machine_label",
    "shown_label",
    "shown_labels",
    "sync_shared_folder",
    "valid_relpath",
]
