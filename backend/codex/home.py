"""Paths in a Codex home: rollout names, containment, and discovery (plan §4.2).

It only lists and stats. It creates nothing, also when the home or one of
its folders is missing (plan §1.2). The id and stem patterns are strict,
because a stem and a thread id name files in the archive.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SESSIONS_DIR = "sessions"
ARCHIVED_DIR = "archived_sessions"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
THREAD_ID_RE = re.compile(_UUID)
STEM_RE = re.compile(rf"rollout-(\d{{4}}-\d{{2}}-\d{{2}})T\d{{2}}-\d{{2}}-\d{{2}}-({_UUID})")
_NAME_RE = re.compile(rf"({STEM_RE.pattern})\.jsonl(\.zst)?")


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
    """True if ``path`` resolves to a place inside ``home``. ``path`` need not exist."""
    root = _norm(str(home))
    try:
        return os.path.commonpath([root, _norm(str(path))]) == root
    except ValueError:  # another drive on Windows
        return False


@dataclass(frozen=True)
class RolloutFile:
    stem: str
    thread_id: str
    path: Path
    archived: bool  # under archived_sessions/


def _rank(r: RolloutFile) -> tuple[bool, bool]:
    # The plain file wins over the .zst one (plan §4.2). Archiving moves a
    # file, so a copy in archived_sessions/ is the newer place.
    return (r.path.suffix == ".jsonl", r.archived)


def find_rollouts(home: Path) -> list[RolloutFile]:
    """One entry per rollout stem in ``sessions/**`` and ``archived_sessions/**``, by stem."""
    found: dict[str, RolloutFile] = {}
    for archived, folder in ((False, home / SESSIONS_DIR), (True, home / ARCHIVED_DIR)):
        # os.walk does not follow a symlinked folder, and a symlinked file is
        # skipped below, so every file read stays inside the home.
        for dirpath, _dirs, files in os.walk(folder):
            for name in files:
                stem = stem_of(name)
                path = Path(dirpath) / name
                if stem is None or path.is_symlink():
                    continue
                found_one = RolloutFile(stem, thread_id_of(stem) or "", path, archived)
                old = found.get(stem)
                if old is None or _rank(found_one) > _rank(old):
                    found[stem] = found_one
    return [found[s] for s in sorted(found)]
