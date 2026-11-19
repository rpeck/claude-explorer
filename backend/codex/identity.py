"""Thread identity and metadata for a Codex thread (plan §4.2, §4.4 N5).

- The strict patterns for thread ids and rollout stems. Both name files in
  the archive, so nothing else gets into a path.
- :func:`thread_metadata`: the metadata fields of the thread JSON, from the
  rollouts, ``session_index.jsonl``, ``state_5``, and the import list.
- :func:`newest_copy`: the newest copy of one thread id across homes.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from .projection import project_rollout
from .rollout import RolloutRead
from .state_db import StateRead

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
THREAD_ID_RE = re.compile(_UUID)
STEM_RE = re.compile(rf"rollout-(\d{{4}}-\d{{2}}-\d{{2}})T\d{{2}}-\d{{2}}-\d{{2}}-({_UUID})")
_NAME_RE = re.compile(rf"({STEM_RE.pattern})\.jsonl(\.zst)?")

# The thread JSON fields that come from state_5 or session_meta. A pass that
# cannot read state_5 keeps them from the archived thread JSON (council N5, rule 11).
_KEPT_FIELDS = ("cwd", "model", "archived", "history_mode", "thread_source", "parent_thread_id", "updated_at_ms")


def is_thread_id(value: object) -> bool:
    return isinstance(value, str) and THREAD_ID_RE.fullmatch(value) is not None


def stem_of(file_name: str) -> str | None:
    """The stem of a rollout file name (``.jsonl`` or ``.jsonl.zst``), else ``None``."""
    m = _NAME_RE.fullmatch(file_name)
    return m.group(1) if m else None


def thread_id_of(stem: str) -> str | None:
    m = STEM_RE.fullmatch(stem)
    return m.group(2) if m else None


def _norm(path: str) -> str:
    real = os.path.realpath(path)
    return real.casefold() if sys.platform in ("darwin", "win32") else real


def is_inside(home: Path, path: Path) -> bool:
    """True if ``path`` resolves to a place inside ``home``. It only reads."""
    root = _norm(str(home))
    try:
        return os.path.commonpath([root, _norm(str(path))]) == root
    except ValueError:  # another drive on Windows
        return False


@dataclass(frozen=True)
class RolloutFacts:
    """What the metadata rules need from one rollout of a thread."""

    stem: str
    archived: bool  # the file is under archived_sessions/
    session_meta: dict[str, Any]
    session_meta_ts_ms: int | None
    model: str | None
    history_mode: str
    first_human_message: str | None


def _ts_ms(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def rollout_facts(stem: str, read: RolloutRead, *, archived: bool) -> RolloutFacts:
    meta = next((r.payload for r in read.records if r.type == "session_meta"), {})
    conv = project_rollout(read.records, thread_id=thread_id_of(stem))
    first = next((m["text"] for m in conv["chat_messages"] if m["sender"] == "human" and m["text"].strip()), None)
    return RolloutFacts(
        stem=stem,
        archived=archived,
        session_meta=meta,
        session_meta_ts_ms=_ts_ms(meta.get("timestamp")),
        model=conv["model"] or None,
        history_mode=conv["history_mode"],
        # The first line: the image labels and the rest of the message follow it.
        first_human_message=next(line.strip() for line in first.splitlines() if line.strip()) if first else None,
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parent_in(source: object) -> str | None:
    """``parent_thread_id`` anywhere in a ``source`` value (JSON text or a dict)."""
    if isinstance(source, str):
        try:
            source = orjson.loads(source)
        except orjson.JSONDecodeError:
            return None
    if isinstance(source, dict):
        if is_thread_id(source.get("parent_thread_id")):
            return source["parent_thread_id"]
        return next((p for v in source.values() if (p := _parent_in(v))), None)
    return None


def _row_stem(row: Mapping[str, Any] | None, home: Path, stems: set[str]) -> str | None:
    path = row.get("rollout_path") if row else None
    if not isinstance(path, str) or not path or not is_inside(home, Path(path)):
        return None
    stem = stem_of(Path(path).name)
    return stem if stem in stems else None


def thread_metadata(
    thread_id: str,
    rollouts: Sequence[RolloutFacts],
    *,
    home: Path,
    state: StateRead,
    names: Mapping[str, str],
    imports: Mapping[str, str],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The metadata fields of ``threads/<thread_id>.json`` (plan §4.2, §4.4).

    ``state.rows`` is ``None`` when the ``state_5`` read fell back. ``previous`` is the archived thread JSON,
    if any. ``rollouts`` must not be empty.
    """
    by_stem = {r.stem: r for r in sorted(rollouts, key=lambda r: r.stem)}
    stems = set(by_stem)
    first = next(iter(by_stem.values()))
    newest = max(by_stem.values(), key=lambda r: (r.session_meta_ts_ms or -1, r.stem))
    name = _text(names.get(thread_id))
    default_title = f"Codex session {STEM_RE.fullmatch(first.stem).group(1)}"  # type: ignore[union-attr]
    first_human = next((r.first_human_message for r in by_stem.values() if r.first_human_message), None)
    out: dict[str, Any] = {"thread_id": thread_id}

    if state.rows is None and previous is not None:
        # Fallback: never replace good archived values with fallback values.
        kept_stem = previous.get("current_stem")
        prev_stems = {r.get("stem") for r in previous.get("rollouts") or [] if isinstance(r, Mapping)}
        out["source_name"] = name or _text(previous.get("source_name"))
        out["title"] = name or _text(previous.get("title")) or first_human or default_title
        out.update({k: previous.get(k) for k in _KEPT_FIELDS})
        out["current_stem"] = kept_stem if kept_stem in stems else newest.stem
        out["current_ambiguous"] = bool(previous.get("current_ambiguous")) or prev_stems != stems
    else:
        row = state.rows.get(thread_id) if state.rows is not None else None
        row_stem = _row_stem(row, home, stems)
        current = by_stem[row_stem or newest.stem]
        meta = current.session_meta
        r = row or {}
        out["source_name"] = name or _text(r.get("name"))
        out["title"] = out["source_name"] or _text(r.get("title")) or first_human or default_title
        out["cwd"] = _text(r.get("cwd")) or _text(meta.get("cwd"))
        out["model"] = _text(r.get("model")) or current.model
        out["archived"] = bool(r["archived"]) if row else current.archived
        out["history_mode"] = _text(r.get("history_mode")) or current.history_mode
        out["thread_source"] = _text(r.get("thread_source")) or _text(meta.get("thread_source"))
        out["parent_thread_id"] = (
            _parent_in(r.get("source")) or _parent_in(meta.get("source"))
            or (meta["parent_thread_id"] if is_thread_id(meta.get("parent_thread_id")) else None)
        )
        updated = r.get("updated_at_ms")
        out["updated_at_ms"] = updated if isinstance(updated, int) else None
        out["current_stem"] = current.stem
        out["current_ambiguous"] = len(stems) > 1 and row_stem is None
    out["imported_from"] = _text(imports.get(thread_id))
    return out


def _rank(copy: Mapping[str, Any]) -> int:
    updated = copy.get("updated_at_ms")
    if isinstance(updated, int):
        return updated
    stamps = [r.get("session_meta_ts_ms") for r in copy.get("rollouts") or [] if isinstance(r, Mapping)]
    return max((s for s in stamps if isinstance(s, int)), default=-1)


def newest_copy(copies: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The newest thread JSON of one thread id across homes (plan §4.2).

    The largest ``updated_at_ms``; a copy without one uses its newest
    ``session_meta`` timestamp. A tie goes to the smaller ``home_key``.
    """
    return min(copies, key=lambda c: (-_rank(c), str(c.get("home_key") or "")))
