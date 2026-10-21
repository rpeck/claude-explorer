"""Raw JSONL I/O for Claude Code conversations.

Owns the lowest-level reads against ``~/.claude/projects/**/*.jsonl``:
  * ``DEFAULT_CLAUDE_DIR`` — path constant.
  * ``parse_jsonl_file`` — full-file parse delegating to the orjson-backed
    helper in :mod:`backend.cache`.
  * ``discover_jsonl_files`` — generator that yields every top-level
    JSONL session file (excluding ``agent-*.jsonl`` sub-conversations).
  * ``read_conversation_summary_fast`` — streaming single-pass metadata
    reader used by the listing path.
  * ``LOGIC_VERSION`` — sha256(inspect.getsource(read_conversation_summary_fast))
    truncated to 16 hex chars, used to invalidate
    :class:`backend.summary_cache.SummaryCache` rows when the producer's
    body changes (whitespace and comments included — acceptable since a
    refactor that touches this function will always force a one-time
    cache rebuild on next startup).

Layering:
  * Imports from :mod:`backend.cc_message_transforms` for the pure text
    helpers (``_title_from_entry``, ``_is_system_message``,
    ``_extract_title_from_message``). These are stateless leaf functions
    that operate on already-parsed dicts; the import does not pull any
    other I/O machinery.
  * Imported by :mod:`backend.cc_agent_reader` for ``parse_jsonl_file``
    and by the facade :mod:`backend.claude_code_reader` for everything.

History (refactor B5, 2026-05-18): extracted from the 1540-line
monolithic ``backend.claude_code_reader``. The facade re-exports every
public symbol from this module so existing imports
(``from backend.claude_code_reader import discover_jsonl_files`` etc.)
keep working unchanged.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import orjson

from .agent_session_io import parse_jsonl_file  # noqa: F401  re-export
from .cc_message_transforms import (
    cc_session_uuid,
    _extract_title_from_message,
    _is_system_message,
    _title_from_entry,
)
from .parsing import parse_datetime as _parse_datetime


# Default Claude directory
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"


def read_conversation_summary_fast(jsonl_path: Path) -> dict[str, Any] | None:
    """Read metadata from a JSONL file for fast listing.

    Scans the entire file to:
    - Find first user/assistant entries for metadata
    - Count all user entries and unique assistant message IDs
    """
    latest_title: str | None = None  # Last non-empty title from any title row
    first_user = None
    first_real_user = None  # First user message that's not a system "Caveat" message
    first_assistant = None
    first_timestamp = None

    # Message counting
    user_count = 0
    assistant_message_ids: set[str] = set()

    try:
        with open(jsonl_path, "rb") as f:  # Binary mode for orjson
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = orjson.loads(line)
                    entry_type = entry.get("type")

                    # Extract metadata from first occurrences
                    ts = entry.get("timestamp")
                    if ts and first_timestamp is None:
                        first_timestamp = ts

                    # Title-event rows (custom-title / agent-name / summary):
                    # last non-empty value wins. See `_TITLE_FIELD_BY_TYPE`.
                    title_candidate = _title_from_entry(entry)
                    if title_candidate:
                        latest_title = title_candidate

                    if entry_type == "user":
                        user_count += 1
                        if not first_user:
                            first_user = entry
                        # Track first real user message for title extraction
                        if not first_real_user and not _is_system_message(entry):
                            first_real_user = entry
                    elif entry_type == "assistant":
                        # Dedupe by message.id to handle streaming chunks
                        msg = entry.get("message", {})
                        msg_id = msg.get("id")
                        if msg_id:
                            assistant_message_ids.add(msg_id)
                        if not first_assistant:
                            first_assistant = entry

                except orjson.JSONDecodeError:
                    pass
    except (OSError, IOError):
        return None

    if not first_user:
        return None

    # Build metadata - prefer the most recent title-event (custom-title /
    # agent-name / summary). Fall back to first-real-user truncation only
    # when no title rows exist (e.g. unrenamed sessions on older CC).
    name = latest_title
    if not name and first_real_user:
        name = _extract_title_from_message(first_real_user)

    if not name:
        name = jsonl_path.stem

    # Detect phantom sessions (local command artifacts with no real conversation)
    # A phantom session starts with "Caveat:" AND has no assistant responses
    is_phantom = (
        name.startswith("Caveat: The messages below were generated")
        and len(assistant_message_ids) == 0
    )

    session_id = cc_session_uuid(jsonl_path)
    cwd = first_user.get("cwd", "")
    git_branch = first_user.get("gitBranch", "")

    # Get model
    model = ""
    if first_assistant:
        msg = first_assistant.get("message", {})
        model = msg.get("model", "")

    # Use file mtime for updated_at (fast)
    created_at = _parse_datetime(first_timestamp)
    try:
        mtime = jsonl_path.stat().st_mtime
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        updated_at = created_at

    # Total messages = user messages + unique assistant responses
    message_count = user_count + len(assistant_message_ids)

    return {
        "uuid": session_id,
        "name": name,
        "summary": "",
        "model": model,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "is_starred": False,
        "project_path": cwd,
        "git_branch": git_branch,
        "source": "CLAUDE_CODE",
        "message_count": message_count,
        "human_message_count": user_count,
        "has_branches": False,
        "is_phantom": is_phantom,
    }


# Source-hash of the fast metadata reader. Bumps every time the function
# body changes (including whitespace + comments — acceptable since the
# function changes rarely and the trade-off is "never serve cached rows
# from an out-of-date producer"). Stored in
# ``conversation_summaries_meta.value`` and compared at lifespan startup;
# mismatch → :meth:`backend.summary_cache.SummaryCache.clear_on_logic_mismatch`
# wipes the cache table.
#
# inspect.getsource is stable for module-level functions in CPython
# (verified manually); if it ever returns something fragile we can fall
# back to a manually-maintained version string.
#
# Refactor note (B5, 2026-05-18): the constant moved here from
# ``backend.claude_code_reader`` alongside its hashed function. The
# move re-hashes the source (different physical file, but inspect.getsource
# returns only the function body) — in practice the hash IS stable across
# the move because inspect.getsource ignores enclosing module identity.
# Re-exported from the facade so the existing
# ``from backend.claude_code_reader import LOGIC_VERSION`` import path
# is preserved.
LOGIC_VERSION = hashlib.sha256(
    inspect.getsource(read_conversation_summary_fast).encode()
).hexdigest()[:16]


def discover_jsonl_files(claude_dir: Path = DEFAULT_CLAUDE_DIR) -> Iterator[Path]:
    """Find all JSONL session files in the Claude directory."""
    projects_dir = claude_dir / "projects"

    if not projects_dir.exists():
        return

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            # Skip agent sub-conversations
            if jsonl_file.name.startswith("agent-"):
                continue
            yield jsonl_file
