"""Markdown export surface — single-conversation and zip-all-conversations.

Extracted from ``backend/export.py`` (Council A2, 2026-05-21). Backwards-
compatible imports continue to work via the ``backend.export`` facade.
"""

from __future__ import annotations

import io
import json
import zipfile

from ..models import ConversationDetail, ContentBlock, Message
from ..search_text import _is_compact_trigger_message
from ._shared import (
    _dedupe_image_files,
    _is_excludable_marker,
    _is_compact_summary_message,
    filter_tool_placeholders,
    format_timestamp,
    message_has_visible_content,
    render_compact_indicator,
    render_compact_summary_markdown,
    sanitize_filename,
)


def render_content_block(
    block: ContentBlock, indent: int = 0, include_tools: bool = True
) -> str:
    """Render a content block to Markdown."""
    prefix = "  " * indent

    if block.type == "text" and block.text:
        # Always strip TOOL_PLACEHOLDER from text content blocks
        # (P1.3b — the placeholder must never reach the recipient
        # regardless of include_tools).
        return filter_tool_placeholders(block.text)

    if block.type == "tool_use":
        if not include_tools:
            return ""
        lines = [f"{prefix}**Tool: {block.name}**"]
        if block.input:
            input_str = json.dumps(block.input, indent=2)
            lines.append(f"{prefix}```json\n{input_str}\n{prefix}```")
        return "\n".join(lines)

    if block.type == "tool_result" and block.content:
        if not include_tools:
            return ""
        lines = [f"{prefix}<details>", f"{prefix}<summary>Tool Result</summary>", ""]
        for child in block.content:
            lines.append(render_content_block(child, indent + 1, include_tools))
        lines.extend(["", f"{prefix}</details>"])
        return "\n".join(lines)

    return ""


def _image_markdown(message: Message) -> str:
    """Render image attachments as Markdown image refs (after content)."""
    images = _dedupe_image_files(message)
    if not images:
        return ""
    lines: list[str] = [""]
    for img in images:
        url = (img.get("preview_asset") or {}).get("url") or img.get("thumbnail_url") or ""
        name = img.get("file_name") or "image"
        alt = f"Image attachment: {name}"
        if url:
            lines.append(f"![{alt}]({url})")
        else:
            lines.append(f"_(image attachment unavailable: {name})_")
        lines.append("")
    return "\n".join(lines)


def message_to_markdown(message: Message, include_tools: bool = True) -> str:
    """Convert a single message to Markdown."""
    sender = "You" if message.sender == "human" else "Claude"
    timestamp = format_timestamp(message.created_at)

    lines = [f"**{sender}:** *{timestamp}*", ""]

    # Render content blocks
    if message.content:
        for block in message.content:
            rendered = render_content_block(block, include_tools=include_tools)
            if rendered:
                lines.append(rendered)
    elif message.text:
        # Always strip TOOL_PLACEHOLDER (P1.3b — it represents an
        # uncaptured tool call/artifact and the recipient should never
        # see the literal string, regardless of include_tools).
        lines.append(filter_tool_placeholders(message.text))

    # Image attachments (always rendered, regardless of include_tools).
    image_md = _image_markdown(message)
    if image_md:
        lines.append(image_md)

    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def conversation_to_markdown(
    conversation: ConversationDetail,
    include_tools: bool = True,
    include_compact: bool = False,
) -> str:
    """Convert a conversation to Markdown.

    ``include_compact`` (V1 polish 2026-05-24, default False) controls
    whether /compact-related content (the ``isCompactSummary``
    synthetic message + the trigger row carrying the
    ``<command-name>/compact</command-name>`` envelope) renders
    verbatim or is collapsed to a single-line indicator. Defaults to
    OFF so exports stay short by default; the user opts in via
    ``export.includeCompactContent`` in Settings.
    """
    lines = [
        f"# {conversation.name}",
        "",
        f"**Model:** {conversation.model}",
        f"**Created:** {format_timestamp(conversation.created_at)}",
        f"**Messages:** {conversation.message_count}",
        "",
        "---",
        "",
    ]

    compact_by_uuid = {
        m.message_uuid: m for m in (conversation.compact_markers or [])
    }
    compact_marker_uuids = set(compact_by_uuid)

    for message in conversation.messages:
        if _is_excludable_marker(message):
            continue
        # Drop the trigger row in BOTH states (V1 polish 2026-05-24,
        # user-reported): the trigger's `<command-name>/compact</command-name>`
        # envelope is chrome the user never wants to see. In the OFF
        # state the indicator line covers the event; in the ON state
        # the rich summary block surfaces `user_prompt` as
        # `**You asked:**`, so the trigger row's prompt content is
        # redundant.
        if _is_compact_trigger_message(message.model_dump()):
            continue
        if _is_compact_summary_message(message, compact_marker_uuids):
            if include_compact:
                # Rich summary block — mirror of CompactMarker.tsx in
                # the viewer (purple-bordered panel with
                # "You asked" / "Summary" subsections).
                rich = render_compact_summary_markdown(message, compact_by_uuid)
                if rich is not None:
                    lines.append(rich)
                    lines.append("")
                    lines.append("---")
                    lines.append("")
                continue
            # OFF state (V1 polish 2026-05-24 refinement, user-reported):
            # fully HIDE the compaction. No indicator line. Matches the
            # viewer's "Show Compactions" checkbox semantics — unchecked
            # = the compaction is invisible, not "summarized as a one-
            # liner." Recipients of the export see the conversation as
            # if the compaction never happened.
            continue
        if message_has_visible_content(message, include_tools):
            lines.append(message_to_markdown(message, include_tools))

    return "\n".join(lines)


# Stub content the empty-corpus zip ships. Two reasons we don't return a
# byte-empty zip on an empty corpus:
#   1. File managers render a zero-entry zip as "0 items" / "empty"
#      with no further context — a user who clicked "Export all" can't
#      tell whether the export succeeded or failed.
#   2. A README inside the zip is self-documenting: it explains the
#      "fresh install" state (no fetches run yet) and points the user
#      at the next step. The "Refresh" button in the sidebar is the V1
#      flow that owns capture + fetch (see AGENTS.md "Web UI Refresh
#      button"), so we name it explicitly.
_EMPTY_CORPUS_README = (
    "# Claude Explorer — Empty export\n"
    "\n"
    "This zip contains no conversations because the local data directory is\n"
    "empty. That's the fresh-install state: credentials have not been\n"
    "captured yet, or no fetch has completed.\n"
    "\n"
    "Open Claude Explorer and click **Refresh** in the sidebar to capture\n"
    "credentials and fetch your conversations, then export again.\n"
)


def create_markdown_zip(
    conversations: list[ConversationDetail],
    *,
    include_compact: bool = False,
) -> bytes:
    """Create a ZIP file containing all conversations as Markdown.

    Empty-corpus contract (C6 (c), PLANS/2026.05.18-test-hardening.md):
    when ``conversations`` is empty we still return a valid, well-formed
    zip containing a single ``README.md`` stub. The route
    ``/api/export/all/markdown`` calls into this on every export — a
    byte-empty zip would surface in the user's file manager as "0
    items" with no explanation, indistinguishable from a corrupt
    download.

    ``include_compact`` (default False) forwards to
    :func:`conversation_to_markdown` for each conversation.
    """
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if not conversations:
            zf.writestr("README.md", _EMPTY_CORPUS_README.encode("utf-8"))
        else:
            for conv in conversations:
                filename = f"{sanitize_filename(conv.name)}.md"
                content = conversation_to_markdown(
                    conv, include_compact=include_compact
                )
                zf.writestr(filename, content.encode("utf-8"))

    buffer.seek(0)
    return buffer.read()


__all__ = [
    "render_content_block",
    "_image_markdown",
    "message_to_markdown",
    "conversation_to_markdown",
    "_EMPTY_CORPUS_README",
    "create_markdown_zip",
]


# Re-export the model types so consumers of this submodule don't need
# to reach into ``backend.models`` separately.
_ = (Message, ContentBlock)
