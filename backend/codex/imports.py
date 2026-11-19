"""Read ``external_agent_session_imports.json`` (plan §3, §4.4 N5).

Codex lists there each Claude Code session that it imported. Explorer uses
it for the "Imported from Claude Code" notice only.
"""

from __future__ import annotations

import logging
from pathlib import Path

import orjson

log = logging.getLogger(__name__)

IMPORTS_FILE = "external_agent_session_imports.json"


def read_imports(home: Path, warnings: list[str] | None = None) -> dict[str, str]:
    """Imported thread id -> the Claude Code ``source_path``."""
    try:
        data = orjson.loads((home / IMPORTS_FILE).read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, orjson.JSONDecodeError):
        _warn(warnings, "the Codex session-import list cannot be read")
        return {}
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        _warn(warnings, "the Codex session-import list has an unknown format")
        return {}
    out: dict[str, str] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        tid, src = r.get("imported_thread_id"), r.get("source_path")
        if isinstance(tid, str) and isinstance(src, str) and src:
            out[tid] = src
    return out


def _warn(warnings: list[str] | None, message: str) -> None:
    log.warning("%s", message)
    if warnings is not None:
        warnings.append(message)
