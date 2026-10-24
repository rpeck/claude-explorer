"""Format-agnostic JSONL primitives for agent-session readers.

Shared between :mod:`backend.cc_jsonl_io` /
:mod:`backend.cc_message_transforms` (Claude Code projects/*.jsonl) and
:mod:`backend.cowork_reader` (Claude Desktop Cowork
``local-agent-mode-sessions/<deployment>/<org>/local_<uuid>/audit.jsonl``).

Functions here operate on *already-parsed dicts* and are completely free
of CC slash-command / prelude / canned-fold heuristics — those stay in
``cc_message_transforms`` because they encode Claude Code-specific
boilerplate that does not appear in Cowork's audit log.

Re-exported by ``cc_jsonl_io`` (parse_jsonl_file) and
``cc_message_transforms`` (the four transforms) for backwards-compat;
all existing 1400-LOC of CC imports continue to work unchanged.

Refactor (Phase 0, 2026-05-25): extracted here as the seam for the
Cowork reader (see ``PLANS/2026.05.24-SUPPORT-COWORK-SESSIONS.md``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .cache import parse_jsonl_fast


def parse_jsonl_file(path: Path) -> list[dict]:
    """Parse a JSONL file and return all entries.

    Uses orjson (via :func:`backend.cache.parse_jsonl_fast`) for ~5x
    faster parsing than stdlib ``json``. Tolerates a partial last line
    (writer was interrupted mid-flush) by silently dropping it — same
    semantics ``parse_jsonl_fast`` already provides.
    """
    return parse_jsonl_fast(path)


def _get_message_text(entry: dict) -> str:
    """Extract text content from a message entry."""
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        return " ".join(text_parts)
    return ""


def _get_message_key(entry: dict) -> str | None:
    """Get a unique key for grouping streaming chunks of the same message.

    Assistant messages have message.id, user messages use entry uuid.
    """
    entry_type = entry.get("type")
    if entry_type not in ("user", "assistant"):
        return None

    msg = entry.get("message", {})
    # Assistant messages have message.id for grouping streaming chunks.
    # Cowork autonomous-decision (Phase 0a #7): if `message.id` is absent
    # on a Cowork assistant line, fall back to `assistant:<uuid>` so each
    # line becomes its own logical message rather than being silently
    # collapsed with another id-less assistant line.
    if entry_type == "assistant" and msg.get("id"):
        return f"assistant:{msg['id']}"
    if entry_type == "assistant":
        return f"assistant:{entry.get('uuid', '')}"
    # User messages use entry uuid
    return f"user:{entry.get('uuid', '')}"


def _merge_entries_to_message(entries: list[dict]) -> dict | None:
    """Merge multiple streaming entries into a single message.

    Claude Code streams messages as multiple entries, each with different
    content blocks (thinking, text, tool_use, etc.). This merges them all.
    """
    if not entries:
        return None

    first_entry = entries[0]
    last_entry = entries[-1]
    entry_type = first_entry.get("type")

    if entry_type not in ("user", "assistant"):
        return None

    # Collect ALL content blocks from ALL entries
    all_content_blocks = []
    text_parts = []

    for entry in entries:
        message_data = entry.get("message", {})
        content = message_data.get("content", "")

        if isinstance(content, str):
            if content:
                all_content_blocks.append({"type": "text", "text": content})
                text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                all_content_blocks.append(block)
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

    text = "\n".join(text_parts)

    timestamp = first_entry.get("timestamp", datetime.now(UTC).isoformat())

    # Use first entry's uuid as the message uuid (for parent chain)
    # Use first entry's parentUuid to link to previous message
    return {
        "uuid": first_entry.get("uuid", ""),
        "sender": "human" if entry_type == "user" else "assistant",
        "text": text,
        "content": all_content_blocks,
        "created_at": timestamp,
        "updated_at": last_entry.get("timestamp", timestamp),
        "truncated": False,
        "parent_message_uuid": first_entry.get("parentUuid"),
        "attachments": [],
        "files": [],
    }


def _convert_entry_to_message(entry: dict) -> dict | None:
    """Convert a JSONL entry to a chat message format.

    Note: For streaming conversations, use _merge_entries_to_message instead.
    """
    entry_type = entry.get("type")

    if entry_type not in ("user", "assistant"):
        return None

    message_data = entry.get("message", {})

    # Extract text content
    content = message_data.get("content", "")
    if isinstance(content, str):
        text = content
        content_blocks = [{"type": "text", "text": content}] if content else []
    elif isinstance(content, list):
        content_blocks = content
        text_parts = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        text = "\n".join(text_parts)
    else:
        text = ""
        content_blocks = []

    timestamp = entry.get("timestamp", datetime.now(UTC).isoformat())

    return {
        "uuid": entry.get("uuid", ""),
        "sender": "human" if entry_type == "user" else "assistant",
        "text": text,
        "content": content_blocks,
        "created_at": timestamp,
        "updated_at": timestamp,
        "truncated": False,
        "parent_message_uuid": entry.get("parentUuid"),
        "attachments": [],
        "files": [],
    }


def normalize_session_fields(
    entry: dict, *, fmt: Literal["claude_code", "cowork"]
) -> dict:
    """Normalize a parsed JSONL line to the CC-canonical field shape.

    Claude Code's ``projects/*.jsonl`` writer uses camelCase
    (``sessionId``, ``parentUuid``, ``timestamp``); Cowork's
    ``audit.jsonl`` writer uses a Desktop-internal shape
    (``session_id``, no ``parentUuid``, ``_audit_timestamp``).

    All downstream pure transforms (``_get_message_key``,
    ``_merge_entries_to_message``, etc.) expect the CC-canonical shape,
    so the Cowork reader runs each line through this normalizer first.

    For ``fmt="claude_code"``, this is a no-op (CC is already
    canonical). For ``fmt="cowork"`` we:

    - map ``session_id`` → ``sessionId`` (only when ``sessionId`` is
      absent — never overwrite an upstream value);
    - map ``_audit_timestamp`` → ``timestamp`` (same caveat);
    - drop ``_audit_hmac`` (D4 — we don't verify it, and shipping it
      to the frontend would leak the Desktop audit-log secret).

    ``uuid``, ``parentUuid``, and the ``message.*`` shape are left
    untouched: per Phase 0a schema verification, Cowork user/assistant
    lines carry ``uuid`` directly, and ``parentUuid`` is intentionally
    absent (Cowork is a chronological append-only log; the store layer's
    ``is_chronological`` guard handles rendering without parent links).
    """
    if fmt == "claude_code":
        return entry
    out = dict(entry)
    if "sessionId" not in out and "session_id" in out:
        out["sessionId"] = out["session_id"]
    if "timestamp" not in out and "_audit_timestamp" in out:
        out["timestamp"] = out["_audit_timestamp"]
    out.pop("_audit_hmac", None)
    return out
