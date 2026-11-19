"""Read Codex's ``session_index.jsonl``, the append-only name index (plan §3).

Each line is ``{"id", "thread_name", "updated_at"}``. The last line for an
id wins, so a rename reaches the archive. An empty name clears the name.
"""

from __future__ import annotations

import logging
from pathlib import Path

import orjson

log = logging.getLogger(__name__)

NAME_INDEX_FILE = "session_index.jsonl"


def read_name_index(home: Path, warnings: list[str] | None = None) -> dict[str, str]:
    """Thread id -> Codex session name. Damaged lines add one warning."""
    names: dict[str, str] = {}
    damaged = 0
    try:
        with open(home / NAME_INDEX_FILE, "rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break  # Codex can still be writing the last line.
                if not line.strip():
                    continue
                try:
                    obj = orjson.loads(line)
                except orjson.JSONDecodeError:
                    damaged += 1
                    continue
                if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or "thread_name" not in obj:
                    damaged += 1
                    continue
                name = obj["thread_name"]
                if isinstance(name, str) and name.strip():
                    names[obj["id"]] = name.strip()
                else:
                    names.pop(obj["id"], None)
    except FileNotFoundError:
        return {}
    except OSError:
        _warn(warnings, "the Codex session index cannot be read")
        return {}
    if damaged:
        _warn(warnings, f"{damaged} damaged line(s) in the Codex session index")
    return names


def _warn(warnings: list[str] | None, message: str) -> None:
    log.warning("%s", message)
    if warnings is not None:
        warnings.append(message)
