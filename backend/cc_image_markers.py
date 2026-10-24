"""Compact-marker extraction for Claude Code JSONL conversations.

Claude Code emits a synthetic user message with ``isCompactSummary: true``
each time the runtime compacts the conversation history. The frontend
renders these as "Compact summary" dividers with optional auto/manual
labels and a "View full prompt" affordance for manual /compact runs.

This module owns the pure-functional pass that walks a list of already-
parsed JSONL entries and pulls out one marker dict per compact event.
It does not touch the filesystem; the caller (typically
:func:`backend.claude_code_reader.read_claude_code_conversation`) parses
the JSONL first and passes the entries in.

Layering:
  * Imports ``_get_message_text`` from :mod:`backend.cc_message_transforms`.
  * Does not import from :mod:`backend.cc_jsonl_io` (no I/O dependency).
  * Imported by the facade :mod:`backend.claude_code_reader`.

Naming history (refactor B5, 2026-05-18): named ``cc_image_markers`` per
the task spec to keep room for future CC-image-related marker passes
(today only the compact-summary marker is implemented; image-marker
caching lives in :mod:`backend.cc_image_cache`).
"""

from __future__ import annotations

import re

from .cc_message_transforms import _get_message_text
from .compact_prefixes import is_compaction_prefix_text

_COMPACT_LOOKAHEAD = 8
_COMPACT_COMMAND_NAME = "<command-name>/compact</command-name>"
_COMPACT_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def _is_compact_summary_entry(entry: dict) -> bool:
    """True iff this entry is a compaction-summary message.

    Primary signal: the ``isCompactSummary: true`` field stamped by
    modern Claude Code binaries.

    Fallback (2026-05-26): user-role entries whose text content starts
    with the canonical Claude compaction prompt. Older CC binaries
    (observed in Aug 2025 sessions) emit the prompt text without
    setting the marker field; the text-prefix is the runtime's
    invariant since the feature shipped. Same heuristic the Cowork
    reader uses for its audit.jsonl format.

    The fallback is gated on ``role == "user"`` to defend against
    quoting / template content in non-user roles.
    """
    if entry.get("isCompactSummary") is True:
        return True
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    text = _get_message_text(entry)
    if not isinstance(text, str):
        return False
    return is_compaction_prefix_text(text)


def extract_compact_markers(entries: list[dict]) -> list[dict]:
    """Extract compact markers from a Claude Code JSONL entry list.

    Each marker is the synthetic user message Claude Code injects when
    it compacts the conversation history. Detected via:
      * ``isCompactSummary: true`` (modern CC), OR
      * canonical-prefix text fallback (older CC sessions).

    Auto vs manual is determined by scanning the small window AFTER the
    marker for a replayed ``<command-name>/compact</command-name>``
    user record; manual markers also surface the ``<command-args>``
    text so the UI can render the user's prompt.

    Returns a list of dicts: `{message_uuid, summary_text, timestamp, kind, user_prompt}`.
    """
    markers: list[dict] = []
    for idx, entry in enumerate(entries):
        if not _is_compact_summary_entry(entry):
            continue
        kind = "auto"
        user_prompt: str | None = None
        end = min(len(entries), idx + 1 + _COMPACT_LOOKAHEAD)
        for j in range(idx + 1, end):
            other = entries[j]
            if other.get("isCompactSummary") is True:
                break
            text = _get_message_text(other)
            if _COMPACT_COMMAND_NAME in text:
                kind = "manual"
                m = _COMPACT_ARGS_RE.search(text)
                user_prompt = m.group(1).strip() if m else ""
                break
        markers.append({
            "message_uuid": entry.get("uuid", ""),
            "summary_text": _get_message_text(entry),
            "timestamp": entry.get("timestamp", ""),
            "kind": kind,
            "user_prompt": user_prompt,
        })
    return markers
