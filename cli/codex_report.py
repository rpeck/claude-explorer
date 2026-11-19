"""The Codex shape report: counts only, for work-machine verification (plan §9).

It reads the Codex home and the Explorer archive, and it writes nothing: the
archive part is a dry run of the copier (§4.2 step 2). The ``state_5`` read
uses N5's private temp copy, which is gone before ``read_threads`` returns.
The output holds no message text, no path, no title, and no id, so a tester
can paste it into a public issue. Every key is a fixed name or a type name
or version that matches a strict pattern.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson

from backend.archive_sync import _newest_revision, home_key
from backend.codex.home import STEM_RE, THREAD_ID_RE, RolloutFile, find_rollouts
from backend.codex.identity import thread_metadata
from backend.codex.imports import read_imports
from backend.codex.name_index import read_session_index
from backend.codex.projection import _KNOWN_RECORD_TYPES, project_rollout
from backend.codex.rollout import RolloutRead, read_rollout
from backend.codex.state_db import read_threads
from backend.codex.zstd import open_decompressed
from backend.sources.codex import ARCHIVE_DIR, ROLLOUTS_DIR, THREADS_DIR

REPORT_VERSION = 1

# ThreadItemPayload at openai/codex f2d3086 (plan §3).
KNOWN_ITEM_TYPES = frozenset({
    "UserMessage", "AgentMessage", "Reasoning", "CommandExecution", "ContextCompaction",
    "HookPrompt", "FunctionCallOutput", "Plan", "FileChange", "McpToolCall",
    "DynamicToolCall", "CollabAgentToolCall", "SubAgentActivity", "WebSearch",
    "ImageView", "Sleep", "ImageGeneration", "EnteredReviewMode", "ExitedReviewMode",
})
WARNING_KINDS = (
    "damaged_line", "unsupported_item", "orphan_tool_output",
    "state_db", "session_index", "imports", "other",
)
REASON_KINDS = ("compacted", "rollback", "history_base", "current_ambiguous", "unknown_record_type")

# A key must never carry text from a file, so anything else counts as "invalid".
_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")
_REV_RE = re.compile(rf"{STEM_RE.pattern}\.rev-[0-9]+\.jsonl")
_INVALID = "invalid"


def _key(value: object, pattern: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else _INVALID


def _add(counts: dict[str, int], key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


def _warning_kind(warning: str) -> str:
    # The projection's texts; the part after ":" can hold an id (a call_id).
    if warning.startswith("damaged line"):
        return "damaged_line"
    if warning.startswith("unsupported Codex item"):
        return "unsupported_item"
    if warning.startswith("tool output without a call"):
        return "orphan_tool_output"
    return "other"


def _reason_kind(reason: str) -> str:
    if reason.startswith("unknown record type"):
        return "unknown_record_type"
    return reason if reason in REASON_KINDS else "other"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _decoded(path: Path) -> bytes:
    # shortcut: holds one decoded rollout in memory (tens of MB at most in
    # the §3 corpus); stream the compare if a work corpus has far larger files.
    if path.suffix != ".zst":
        return path.read_bytes()
    with open_decompressed(path) as stream:
        return stream.read()


def _empty_report(home_found: bool) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "home_found": home_found,
        "rollouts": {"total": 0, "plain": 0, "zst": 0, "archived": 0},
        "threads": 0,
        "cli_versions": {},
        "history_modes": {"paginated": 0, "legacy": 0},
        "record_types": {},
        "item_types": {},
        "unknown_record_types": {},
        "unknown_item_types": {},
        "warnings": dict.fromkeys(WARNING_KINDS, 0),
        "incomplete": {"sessions": 0, "incomplete": 0, "share": 0.0, "by_reason": dict.fromkeys(REASON_KINDS, 0)},
        "archive": {
            "exists": False, "unchanged": 0, "would_copy": 0, "would_append": 0,
            "diverged": 0, "decode_errors": 0, "revision_files": 0,
        },
        "sizes": {"stored_bytes": 0, "decoded_bytes": 0},
    }


def build_report(home: Path, data_dir: Path) -> dict[str, Any]:
    """Count the shape of one Codex home and of its archive copy. JSON-safe; reads only."""
    report = _empty_report(home.is_dir())
    if not report["home_found"]:
        return report
    archive = data_dir / ARCHIVE_DIR / home_key(home)
    rollouts_dir = archive / ROLLOUTS_DIR
    report["archive"]["exists"] = archive.is_dir()

    rollouts = find_rollouts(home)
    report["threads"] = len({r.thread_id for r in rollouts})
    by_thread: dict[str, dict[str, tuple[RolloutFile, RolloutRead]]] = {}
    for r in rollouts:
        counts = report["rollouts"]
        counts["total"] += 1
        counts["zst" if r.path.suffix == ".zst" else "plain"] += 1
        counts["archived"] += r.archived
        try:
            report["sizes"]["stored_bytes"] += r.path.stat().st_size
            data = _decoded(r.path)
            read = read_rollout(r.path)
        except Exception:  # noqa: BLE001 - zstd and OS errors; a bad file is a count, not a stop
            report["archive"]["decode_errors"] += 1
            continue
        report["sizes"]["decoded_bytes"] += len(data)
        _count_records(report, read)
        _count_archive(report["archive"], rollouts_dir, r.stem, data)
        by_thread.setdefault(r.thread_id, {})[r.stem] = (r, read)

    warnings = report["warnings"]
    side: list[str] = []
    names = read_session_index(home, side)
    _add(warnings, "session_index", len(side))
    side = []
    imports = read_imports(home, side)
    _add(warnings, "imports", len(side))
    state = read_threads(home)
    _add(warnings, "state_db", len(state.warnings))

    for thread_id, files in sorted(by_thread.items()):
        try:
            _count_session(report, home, archive, thread_id, files, names, imports, state.rows)
        except Exception:  # noqa: BLE001 - one bad thread must not stop the report
            _add(warnings, "other")

    try:
        report["archive"]["revision_files"] = sum(1 for n in rollouts_dir.iterdir() if _REV_RE.fullmatch(n.name))
    except OSError:
        pass
    return report


def _count_records(report: dict[str, Any], read: RolloutRead) -> None:
    for rec in read.records:
        kind = _key(rec.type, _TYPE_RE)
        _add(report["record_types"], kind)
        if rec.type not in _KNOWN_RECORD_TYPES:
            _add(report["unknown_record_types"], kind)
        if rec.type == "session_meta" and "cli_version" in rec.payload:
            _add(report["cli_versions"], _key(rec.payload["cli_version"], _VERSION_RE))
        item = rec.payload.get("item")
        if rec.type == "event_msg" and rec.payload.get("type") == "item_completed" and isinstance(item, dict):
            item_kind = _key(item.get("type"), _TYPE_RE)
            _add(report["item_types"], item_kind)
            if item.get("type") not in KNOWN_ITEM_TYPES:
                _add(report["unknown_item_types"], item_kind)


def _count_archive(counts: dict[str, Any], rollouts_dir: Path, stem: str, data: bytes) -> None:
    """What the copier would do with this rollout now (§4.2 steps 2.3-2.6)."""
    source = data[: data.rfind(b"\n") + 1]  # a last line without a newline is not copied
    try:
        copy = (rollouts_dir / f"{stem}.jsonl").read_bytes()
    except FileNotFoundError:
        counts["would_copy"] += 1
        return
    except OSError:
        counts["decode_errors"] += 1
        return
    if copy.startswith(source):
        counts["unchanged"] += 1
    elif source.startswith(copy):
        counts["would_append"] += 1
    else:
        # The copier's rule: a source equal to the newest revision is unchanged.
        _n, newest = _newest_revision(rollouts_dir, stem)
        try:
            same = newest is not None and newest.read_bytes() == source
        except OSError:
            same = False
        counts["unchanged" if same else "diverged"] += 1


def _count_session(
    report: dict[str, Any],
    home: Path,
    archive: Path,
    thread_id: str,
    files: dict[str, tuple[RolloutFile, RolloutRead]],
    names: dict[str, str],
    imports: dict[str, str],
    rows: dict[str, dict[str, Any]] | None,
) -> None:
    # The copier's rules pick the current rollout; the archived thread JSON
    # stands in when state_5 falls back, as in the copier.
    previous = _read_json(archive / THREADS_DIR / f"{thread_id}.json") if THREAD_ID_RE.fullmatch(thread_id) else None
    meta = thread_metadata(
        home, thread_id, [r.path for r, _ in files.values()],
        names=names, state=rows, imports=imports, previous=previous,
    )
    _r, read = files[meta["current_stem"]]
    mode = meta.get("history_mode")
    conv = project_rollout(
        read.records,
        thread_id=thread_id,
        history_mode=mode if isinstance(mode, str) else None,
        current_ambiguous=bool(meta.get("current_ambiguous")),
        damaged_lines=read.damaged_lines,
    )
    # thread_metadata takes state_5's mode first, then the rollout's.
    _add(report["history_modes"], mode if mode in ("paginated", "legacy") else conv["history_mode"])
    for w in conv["warnings"]:
        _add(report["warnings"], _warning_kind(w))
    inc = report["incomplete"]
    inc["sessions"] += 1
    if conv["incomplete"]:
        inc["incomplete"] += 1
        for kind in {_reason_kind(r) for r in conv["incomplete_reasons"]}:
            _add(inc["by_reason"], kind)
    inc["share"] = inc["incomplete"] / inc["sessions"]


def _counts(title: str, counts: dict[str, int]) -> list[str]:
    if not counts:
        return [f"{title}: none"]
    return [f"{title}:"] + [f"  {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def format_report(report: dict[str, Any]) -> str:
    """The text form for an issue. It holds no message text, path, title, or id."""
    lines = [f"Codex shape report v{report['report_version']} (counts only: no message text, path, title, or id)", ""]
    if not report["home_found"]:
        return "\n".join([*lines, "No Codex home found."])
    r, inc, a, s = report["rollouts"], report["incomplete"], report["archive"], report["sizes"]
    lines += [
        f"Rollouts: {r['total']} ({r['plain']} plain, {r['zst']} zst, {r['archived']} archived)",
        f"Threads: {report['threads']}",
    ]
    lines += _counts("Codex CLI versions (rollouts)", report["cli_versions"])
    lines += _counts("History modes (threads)", report["history_modes"])
    lines += _counts("Record types", report["record_types"])
    lines += _counts("Item types", report["item_types"])
    lines += _counts("Unknown record types", report["unknown_record_types"])
    lines += _counts("Unknown item types", report["unknown_item_types"])
    lines += _counts("Warnings", {k: v for k, v in report["warnings"].items() if v})
    lines += [f"Incomplete sessions: {inc['incomplete']} of {inc['sessions']} ({100 * inc['share']:.1f}%)"]
    lines += [f"  {k}: {v}" for k, v in inc["by_reason"].items()]
    lines += ["Archive (dry run):", f"  exists: {'yes' if a['exists'] else 'no'}"]
    lines += [f"  {k}: {a[k]}" for k in ("unchanged", "would_copy", "would_append", "diverged", "decode_errors", "revision_files")]
    ratio = f" ({s['decoded_bytes'] / s['stored_bytes']:.1f}x)" if s["stored_bytes"] else ""
    lines += [
        "Sizes:",
        f"  stored: {s['stored_bytes']} bytes",
        f"  decoded: {s['decoded_bytes']} bytes{ratio}",
    ]
    return "\n".join(lines)
