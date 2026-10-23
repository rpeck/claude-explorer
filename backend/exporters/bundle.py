"""Markdown bundle export — self-contained zip with conversation.md +
images/ + attachments/, suitable for emailing without the local backend.

Extracted from ``backend/export.py`` (Council A2, 2026-05-21). Backwards-
compatible imports continue to work via the ``backend.export`` facade.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from ..models import ConversationDetail, ContentBlock, Message
from ..search_text import _is_compact_trigger_message
from ._shared import (
    CC_IMAGE_MARKER_RE,
    MarkdownDialect,
    _dedupe_image_files,
    _dedupe_non_image_files,
    _is_excludable_marker,
    _is_compact_summary_message,
    _resolve_attachment_path,
    filter_tool_placeholders,
    format_timestamp,
    message_has_visible_content,
    render_compact_summary_markdown,
    sanitize_filename,
)


def _image_marker_path_is_safe(path: Path, image_cache_root: Path) -> bool:
    """Refuse paths that resolve outside the user's Claude Code image
    cache directory. Mirrors backend.routers.files.get_cc_image."""
    try:
        resolved = path.expanduser().resolve(strict=True)
        resolved.relative_to(image_cache_root.resolve())
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True


def _markdown_image_ref(rel_path: str, alt: str, dialect: MarkdownDialect) -> str:
    """Render an image ref in the chosen dialect."""
    if dialect == "obsidian":
        return f"![[{rel_path}]]"
    return f"![{alt}]({rel_path})"


def _markdown_attachment_ref(rel_path: str, name: str, dialect: MarkdownDialect) -> str:
    """Render an attachment ref in the chosen Markdown dialect.

    CommonMark: ``[<name>](attachments/<file>)``
    Obsidian:   ``[[attachments/<file>]]`` (wikilink — Obsidian renders
    the file's display name itself).
    """
    if dialect == "obsidian":
        return f"[[{rel_path}]]"
    return f"[{name}]({rel_path})"


def _resolve_bundle_attachment_path(conv_uuid: str, file_uuid: str) -> Path | None:
    """Locate the cached on-disk copy of a non-image attachment for the
    bundle export. Tries the ``document`` variant first (per fetcher
    contract for ``file_kind == 'document'``), then ``original``.

    Returns ``None`` if no cached copy exists.
    """
    for variant in ("document", "original"):
        resolved = _resolve_attachment_path(conv_uuid, file_uuid, variant)
        if resolved is not None:
            return resolved
    return None


def _bundle_block_to_markdown(
    block: ContentBlock,
    *,
    include_tools: bool,
    bundle_index: dict[str, str],  # cc-image source key -> images/<filename>
    bundled_alts: dict[str, str],
    dialect: MarkdownDialect,
) -> str:
    """Variant of render_content_block that knows how to swap image
    refs for bundle-relative paths.

    For text blocks: replace each ``[Image: source: <abs-path>]`` marker
    with the chosen-dialect image ref pointing at the bundled copy
    (or a clear fallback if the source path was rejected/missing).

    For inline image content blocks: emit the image ref directly.
    """
    if block.type == "text" and block.text:
        def _swap(match: re.Match[str]) -> str:
            key = f"marker:{match.group(1).strip()}"
            rel = bundle_index.get(key)
            if rel:
                alt = bundled_alts.get(key, "image")
                return _markdown_image_ref(rel, alt, dialect)
            return f"_(image not bundled: {match.group(1).strip()})_"

        # Always strip TOOL_PLACEHOLDER from bundled text (P1.3b).
        return CC_IMAGE_MARKER_RE.sub(_swap, filter_tool_placeholders(block.text))

    if block.type == "image":
        # Inline image content block. The bundle index keys these by the
        # message-position-derived id passed in via bundled_alts/index
        # — the caller pre-populated bundle_index for every inline
        # image in the conversation.
        # We don't know the block's identity from here, so the caller
        # also injects a synthetic textual placeholder we can resolve.
        return ""  # Handled at the message level; see _bundle_message_to_markdown

    if block.type == "tool_use":
        if not include_tools:
            return ""
        lines = [f"**Tool: {block.name}**"]
        if block.input:
            input_str = json.dumps(block.input, indent=2)
            lines.append(f"```json\n{input_str}\n```")
        return "\n".join(lines)

    if block.type == "tool_result" and block.content:
        if not include_tools:
            return ""
        lines = ["<details>", "<summary>Tool Result</summary>", ""]
        for child in block.content:
            rendered = _bundle_block_to_markdown(
                child,
                include_tools=include_tools,
                bundle_index=bundle_index,
                bundled_alts=bundled_alts,
                dialect=dialect,
            )
            if rendered:
                lines.append(rendered)
        lines.extend(["", "</details>"])
        return "\n".join(lines)

    return ""


def _bundle_message_to_markdown(
    message: Message,
    *,
    include_tools: bool,
    inline_image_refs: dict[int, str],  # block_index -> images/<filename>
    bundle_index: dict[str, str],
    bundled_alts: dict[str, str],
    dialect: MarkdownDialect,
    attachment_refs: list[tuple[str, str]] | None = None,  # [(rel, display_name), ...]
    missing_attachments: list[str] | None = None,  # display names skipped
) -> str:
    """Render one message for the bundle, swapping CC image refs for
    bundle-relative paths and inserting inline-image refs at their
    block position."""
    sender = "You" if message.sender == "human" else "Claude"
    timestamp = format_timestamp(message.created_at)
    lines: list[str] = [f"**{sender}:** *{timestamp}*", ""]

    if message.content:
        for bi, block in enumerate(message.content):
            if block.type == "image":
                rel = inline_image_refs.get(bi)
                if rel:
                    lines.append(_markdown_image_ref(rel, "inline image", dialect))
                continue
            rendered = _bundle_block_to_markdown(
                block,
                include_tools=include_tools,
                bundle_index=bundle_index,
                bundled_alts=bundled_alts,
                dialect=dialect,
            )
            if rendered:
                lines.append(rendered)
    elif message.text:
        # Always strip TOOL_PLACEHOLDER (P1.3b).
        text = filter_tool_placeholders(message.text)
        # Run marker substitution on plain text too.
        def _swap(match: re.Match[str]) -> str:
            key = f"marker:{match.group(1).strip()}"
            rel = bundle_index.get(key)
            if rel:
                alt = bundled_alts.get(key, "image")
                return _markdown_image_ref(rel, alt, dialect)
            return f"_(image not bundled: {match.group(1).strip()})_"

        lines.append(CC_IMAGE_MARKER_RE.sub(_swap, text))

    # Desktop preview-asset image attachments: not bundled (they
    # require an authenticated proxy fetch from claude.ai). Emit a
    # footnote so the colleague viewing the bundled .md knows what
    # they're missing instead of seeing a dangling /api/... URL.
    if _dedupe_image_files(message):
        lines.append("")
        for img in _dedupe_image_files(message):
            name = img.get("file_name") or "image"
            lines.append(f"_(Desktop image attachment not bundled: {name})_")

    # Phase 6: non-image attachments (PDF, .txt, .docx, ...). Successfully
    # bundled entries are rendered as Markdown links to the
    # attachments/<filename> entry inside the zip. Entries with no
    # on-disk cached copy are surfaced as a textual placeholder so the
    # recipient sees what they're missing (no dangling /api/... URLs).
    if attachment_refs:
        lines.append("")
        for rel, display_name in attachment_refs:
            lines.append(_markdown_attachment_ref(rel, display_name, dialect))
    if missing_attachments:
        lines.append("")
        for name in missing_attachments:
            lines.append(f"_(attachment not bundled: {name})_")

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _maybe_bundle_marker(
    abs_path_str: str,
    image_cache_root: Path,
    bundle_index: dict[str, str],
    bundled_alts: dict[str, str],
    files_to_write: list[tuple[str, bytes]],
    unique_name_fn: Any,
) -> None:
    """Resolve a `[Image: source: ...]` marker path and, if it's
    safe + readable, register it in bundle_index + queue its bytes
    for the zip writer."""
    key = f"marker:{abs_path_str}"
    if key in bundle_index:
        return
    p = Path(abs_path_str)
    if not _image_marker_path_is_safe(p, image_cache_root):
        return
    try:
        data = p.read_bytes()
    except OSError:
        return
    name = unique_name_fn(p.stem, p.suffix or ".png")
    rel = f"images/{name}"
    bundle_index[key] = rel
    bundled_alts[key] = p.name
    files_to_write.append((rel, data))


def create_markdown_bundle(
    conversation: ConversationDetail,
    *,
    include_tools: bool = True,
    include_compact: bool = False,
    dialect: MarkdownDialect = "commonmark",
    image_cache_root: Path | None = None,
) -> bytes:
    """Bundle a single conversation as a self-contained zip.

    Layout::

        <zip>
        ├── conversation.md     — Markdown with relative image refs
        └── images/
            ├── <synthetic-filename-1>.png
            └── ...

    Image sources bundled:
      - Inline base64 ``image`` content blocks (Claude Code shape) —
        decoded and written under ``images/``.
      - ``[Image: source: <abs-path>]`` text markers — copied from the
        absolute path on disk (validated to live under
        ``image_cache_root``) into ``images/``.

    Image sources NOT bundled:
      - Desktop ``Message.files[]`` previews (require an authenticated
        proxy fetch from claude.ai). Surfaced as a footnote in the .md.
    """
    if image_cache_root is None:
        from ..config import get_settings

        image_cache_root = get_settings().claude_dir / "image-cache"

    buffer = io.BytesIO()
    bundle_index: dict[str, str] = {}  # logical key -> "images/<name>"
    bundled_alts: dict[str, str] = {}  # logical key -> alt text
    files_to_write: list[tuple[str, bytes]] = []
    used_names: set[str] = set()

    def _unique_name(stem: str, suffix: str) -> str:
        """Avoid clobbering when two source images would share a name."""
        base = sanitize_filename(stem) or "image"
        candidate = f"{base}{suffix}"
        i = 2
        while candidate in used_names:
            candidate = f"{base}-{i}{suffix}"
            i += 1
        used_names.add(candidate)
        return candidate

    # Pass 1: scan messages, collect bytes + decide image filenames.
    inline_refs_per_message: dict[str, dict[int, str]] = {}
    # Phase 6: per-message non-image attachment plan. Each entry is a
    # list of (zip_rel_path, display_name) tuples for successfully
    # cached attachments, plus a parallel list of display names whose
    # bytes weren't on disk so we can footnote them.
    attachment_refs_per_message: dict[str, list[tuple[str, str]]] = {}
    missing_attachments_per_message: dict[str, list[str]] = {}
    seen_attachment_uuids: set[str] = set()
    used_attachment_names: set[str] = set()

    def _unique_attachment_name(stem: str, suffix: str) -> str:
        base = sanitize_filename(stem) or "attachment"
        candidate = f"{base}{suffix}"
        i = 2
        while candidate in used_attachment_names:
            candidate = f"{base}-{i}{suffix}"
            i += 1
        used_attachment_names.add(candidate)
        return candidate

    for message in conversation.messages:
        # Phase 6: walk non-image attachments regardless of whether the
        # message has structured content blocks (Desktop messages
        # frequently ship `files` with no `content`).
        msg_attachment_refs: list[tuple[str, str]] = []
        msg_missing_attachments: list[str] = []
        for f in _dedupe_non_image_files(message):
            file_uuid = f.get("file_uuid") or f.get("uuid") or ""
            if not file_uuid or file_uuid in seen_attachment_uuids:
                continue
            seen_attachment_uuids.add(file_uuid)
            display_name = f.get("file_name") or f"{file_uuid}.bin"
            resolved = _resolve_bundle_attachment_path(conversation.uuid, file_uuid)
            if resolved is None:
                msg_missing_attachments.append(display_name)
                continue
            try:
                data = resolved.read_bytes()
            except OSError:
                msg_missing_attachments.append(display_name)
                continue
            # Use the original filename (sanitized) so the recipient sees
            # a recognisable name; fall back to the on-disk variant
            # extension if the source filename has none.
            stem, _, ext = display_name.rpartition(".")
            if stem and ext:
                bundled_name = _unique_attachment_name(stem, "." + ext.lower())
            else:
                bundled_name = _unique_attachment_name(
                    display_name or resolved.stem,
                    resolved.suffix or "",
                )
            rel = f"attachments/{bundled_name}"
            files_to_write.append((rel, data))
            msg_attachment_refs.append((rel, display_name))
        if msg_attachment_refs:
            attachment_refs_per_message[message.uuid] = msg_attachment_refs
        if msg_missing_attachments:
            missing_attachments_per_message[message.uuid] = msg_missing_attachments

        if not message.content:
            # Walk text for marker images.
            if message.text:
                for match in CC_IMAGE_MARKER_RE.finditer(message.text):
                    _maybe_bundle_marker(
                        match.group(1).strip(),
                        image_cache_root,
                        bundle_index,
                        bundled_alts,
                        files_to_write,
                        _unique_name,
                    )
            continue

        msg_inline_refs: dict[int, str] = {}
        for bi, block in enumerate(message.content):
            if block.type == "image" and block.source:
                src = block.source
                # Pydantic ContentBlock.source is `dict | None`; cast safely.
                source_dict: dict[str, Any] = src if isinstance(src, dict) else getattr(src, "model_dump", lambda: {})()
                if source_dict.get("type") == "base64" and source_dict.get("data"):
                    media = source_dict.get("media_type") or "image/png"
                    suffix = "." + (media.split("/")[-1] or "png")
                    name = _unique_name(f"inline-{message.uuid[:8]}-{bi}", suffix)
                    rel = f"images/{name}"
                    files_to_write.append((rel, base64.b64decode(source_dict["data"])))
                    msg_inline_refs[bi] = rel
            elif block.type == "text" and block.text:
                for match in CC_IMAGE_MARKER_RE.finditer(block.text):
                    _maybe_bundle_marker(
                        match.group(1).strip(),
                        image_cache_root,
                        bundle_index,
                        bundled_alts,
                        files_to_write,
                        _unique_name,
                    )
        if msg_inline_refs:
            inline_refs_per_message[message.uuid] = msg_inline_refs

    # Pass 2: build conversation.md.
    md_lines: list[str] = [
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
        # Drop the trigger row in BOTH states (V1 polish 2026-05-24).
        # See markdown.py for full rationale.
        if _is_compact_trigger_message(message.model_dump()):
            continue
        if _is_compact_summary_message(message, compact_marker_uuids):
            if include_compact:
                rich = render_compact_summary_markdown(message, compact_by_uuid)
                if rich is not None:
                    md_lines.append(rich)
                    md_lines.append("")
                    md_lines.append("---")
                    md_lines.append("")
                continue
            # OFF state (V1 polish 2026-05-24): fully hide. No
            # indicator. Matches the viewer's "Show Compactions"
            # checkbox semantics. See markdown.py for full rationale.
            continue
        has_attachments = (
            message.uuid in attachment_refs_per_message
            or message.uuid in missing_attachments_per_message
        )
        if not message_has_visible_content(message, include_tools) and not has_attachments:
            continue
        md_lines.append(
            _bundle_message_to_markdown(
                message,
                include_tools=include_tools,
                inline_image_refs=inline_refs_per_message.get(message.uuid, {}),
                bundle_index=bundle_index,
                bundled_alts=bundled_alts,
                dialect=dialect,
                attachment_refs=attachment_refs_per_message.get(message.uuid),
                missing_attachments=missing_attachments_per_message.get(message.uuid),
            )
        )
    md = "\n".join(md_lines)

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("conversation.md", md.encode("utf-8"))
        for name, data in files_to_write:
            zf.writestr(name, data)

    buffer.seek(0)
    return buffer.read()


__all__ = [
    "_image_marker_path_is_safe",
    "_markdown_image_ref",
    "_markdown_attachment_ref",
    "_resolve_bundle_attachment_path",
    "_bundle_block_to_markdown",
    "_bundle_message_to_markdown",
    "_maybe_bundle_marker",
    "create_markdown_bundle",
]
