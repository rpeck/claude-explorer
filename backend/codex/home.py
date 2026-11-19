"""Find the rollout files of a Codex home (plan §4.2 step 2).

It only lists and stats. It creates nothing, also when the home or one of
its folders is missing (plan §1.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .identity import stem_of, thread_id_of

SESSIONS_DIR = "sessions"
ARCHIVED_DIR = "archived_sessions"


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
