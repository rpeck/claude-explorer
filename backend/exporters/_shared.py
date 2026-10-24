"""Helpers shared across every export surface (markdown, pdf, bundle).

Extracted from ``backend/export.py`` (Council A2, 2026-05-21). The
"one truth, three surfaces" invariant (visible-message rules consistent
across MD/PDF/bundle) means ``message_has_visible_content``,
``_is_excludable_marker``, ``filter_tool_placeholders``, and the
file-dedupe helpers MUST live in exactly one place. This is that place.

Don't add surface-specific rendering logic here. If a helper produces
Markdown or HTML strings, it belongs in the corresponding surface
module.
"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..models import ContentBlock, Message

if TYPE_CHECKING:
    from ..models import CompactMarker


# ---------------------------------------------------------------------------
# Dialect + tool-placeholder constants
# ---------------------------------------------------------------------------

# Issue #4 — Markdown export dialects for the bundled-zip variant.
# CommonMark uses standard ![alt](path); Obsidian uses ![[path]]
# wikilinks. Both Obsidian and GitHub render plain CommonMark so
# CommonMark covers GitHub/MacDown/most others by default.
MarkdownDialect = Literal["commonmark", "obsidian"]


# Placeholder strings the originating Claude Desktop client baked into a
# flattened conversation's `.text` field whenever it couldn't render a
# content block at write time (tool calls — web search, MCP servers,
# artifacts, the analysis REPL, file ops; mobile-only artifact preview;
# etc.). Once flattened, the structured block is gone from the wire format;
# we can only suppress the literal placeholder string downstream, never
# restore the original content. TOOL_PLACEHOLDER stays as the primary
# literal for back-compat with imports / tests; TOOL_PLACEHOLDERS is the
# full set the filter walks.
TOOL_PLACEHOLDER = "This block is not supported on your current device yet."
TOOL_PLACEHOLDER_MOBILE_ARTIFACT = (
    "Viewing artifacts created via the Analysis Tool web feature preview "
    "isn't yet supported on mobile."
)
TOOL_PLACEHOLDERS: tuple[str, ...] = (
    TOOL_PLACEHOLDER,
    TOOL_PLACEHOLDER_MOBILE_ARTIFACT,
)


# Marker for a Claude-Code referenced image, e.g. ``[Image: source: /abs/path]``.
# Both pdf and bundle surfaces consume this; lives in _shared so neither
# pulls a dependency on the other (Council A2 dependency-graph correction).
CC_IMAGE_MARKER_RE = re.compile(r"\[Image: source: ([^\]]+)\]")


# ---------------------------------------------------------------------------
# Filename / timestamp / HTML helpers
# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be safe for use as a filename."""
    # Remove or replace invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)
    # Replace whitespace with underscores
    sanitized = re.sub(r"\s+", "_", sanitized)
    # Limit length
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized or "conversation"


def format_timestamp(dt: datetime) -> str:
    """Format a datetime for display."""
    return dt.strftime("%Y-%m-%d %H:%M")


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


# ---------------------------------------------------------------------------
# Tool-placeholder filtering
# ---------------------------------------------------------------------------


def filter_tool_placeholders(text: str) -> str:
    r"""Strip every Claude Desktop placeholder in ``TOOL_PLACEHOLDERS`` from ``text``.

    Mirrors the frontend canonical algorithm in
    ``frontend/src/components/message/MarkdownRenderer.tsx::stripToolPlaceholderText``
    (P1.3a/P1.3b — keep the two in sync).

    Behaviour:

    * Walks the text line-by-line, tracking whether we're inside a
      fenced code block (toggled by any line whose first non-whitespace
      content is ``\`\`\``).
    * **Outside a fence**: drop ALL occurrences of TOOL_PLACEHOLDER
      anywhere on the line (line-anchored OR mid-paragraph). If the
      line was non-empty before the strip but whitespace-only after,
      drop the whole line so we don't leave a phantom blank paragraph.
    * **Inside a fence**: the frontend leaves the placeholder intact
      so its ``code`` component can render a friendly badge in its
      place. The backend export surfaces (Markdown + PDF) have no such
      badge — the literal string would just leak to the recipient — so
      we strip it inside fences too. If after stripping the fence
      block is empty (``\`\`\`\n\n\`\`\``-like), the surrounding fence
      lines are left in place; downstream renderers handle empty code
      blocks fine.

    After the line walk we collapse 3-or-more consecutive newlines
    down to a single paragraph break, matching the frontend.
    """
    if not any(p in text for p in TOOL_PLACEHOLDERS):
        return text

    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    fence_open_idx: int | None = None  # index in `out` of the most recent ``` we kept

    for line in lines:
        stripped_line = line.lstrip()
        is_fence_marker = stripped_line.startswith("```")

        if is_fence_marker:
            if not in_fence:
                in_fence = True
                fence_open_idx = len(out)
                out.append(line)
            else:
                in_fence = False
                # If the fence we're closing wrapped only stripped /
                # blank content, drop the whole fence (open + body +
                # close) so we don't leave an empty ``` ``` block
                # behind. This is the common Claude Desktop shape.
                if fence_open_idx is not None:
                    body = out[fence_open_idx + 1 :]
                    if all(b.strip() == "" for b in body):
                        del out[fence_open_idx:]
                        fence_open_idx = None
                        continue
                fence_open_idx = None
                out.append(line)
            continue

        # Non-fence line: strip ALL occurrences regardless of fence
        # state (see docstring re: backend has no badge surface).
        had_content = line.strip() != ""
        stripped = line
        for placeholder in TOOL_PLACEHOLDERS:
            stripped = stripped.replace(placeholder, "")
        if had_content and stripped.strip() == "":
            # Line was only the placeholder (plus whitespace) — drop it.
            continue
        out.append(stripped)

    filtered = "\n".join(out)
    # Collapse runs of blank lines to a single paragraph break.
    filtered = re.sub(r"\n{3,}", "\n\n", filtered)
    return filtered


# ---------------------------------------------------------------------------
# Message-level visibility (the "one truth, three surfaces" invariant)
# ---------------------------------------------------------------------------


def _is_excludable_marker(message: Message) -> bool:
    """V1 polish (2026-05-13): True iff this message is pure conversational
    chrome that should be hidden from all export surfaces.

    Two cases both reduce to ``is_command_marker=True``:
      * Argless slash markers (``/exit``, ``/clear``, …). Post-Fix-2
        (claude_code_reader 2026-05-13), ``is_command_marker=True`` IMPLIES
        the marker is argless — argful markers (`/coding <prompt>`,
        `/plan <prose>`) carry the user's real prose and have
        ``is_command_marker=False``, so they pass through this filter and
        export normally.
      * Leading prelude markers. ``is_prelude=True`` is set ONLY on
        argless markers (enforced at claude_code_reader._flag_leading_prelude_markers),
        so they're already covered by the ``is_command_marker`` check.
        We don't need a separate ``is_prelude`` branch.

    Spec invariant X8 ("one truth, three surfaces"): the viewer hides
    these markers behind the SessionPreludeAffordance / SlashCommandBadge
    chrome; markdown/PDF exports must do the same so the recipient sees
    the same content the user saw.
    """
    return message.is_command_marker


def _is_compact_summary_message(
    message: Message, compact_marker_uuids: set[str]
) -> bool:
    """True iff this message's UUID is in the conversation's
    ``compact_markers`` set.

    Caller builds the set once per export via::

        compact_marker_uuids = {m.message_uuid for m in conversation.compact_markers}

    Identifies artifact #2 of a /compact event (the ``isCompactSummary``
    synthetic message). The trigger row (artifact #1) is identified via
    a separate predicate (:func:`backend.search_text._is_compact_trigger_message`).

    Why a separate predicate from :func:`_is_excludable_marker`:
    ``_is_excludable_marker`` filters argless slash markers (``/exit``,
    ``/clear``, the leading-prelude rows). The /compact summary is a
    different category — it carries real content, and whether to drop
    it is a USER PREFERENCE (``export.includeCompactContent``), not an
    invariant. Keep the two predicates separate so the user pref toggle
    can flip /compact behavior without touching the argless-marker
    invariant.
    """
    return message.uuid in compact_marker_uuids


def render_compact_indicator(
    message: Message,
    compact_marker_by_uuid: dict[str, CompactMarker],
) -> str | None:
    """Return a one-line marker indicator if ``message`` is an
    ``isCompactSummary`` row for a known marker; else return None.

    Output shapes::

        ── Compacted (manual): preserve A and refactor auth ──
        ── Compacted (auto) at 2026-04-01 11:00 ──

    For manual markers WITH a ``user_prompt``, the prompt is surfaced
    (provenance — the recipient sees what the user asked /compact to
    preserve, without the verbose LLM summary). For auto markers (no
    prompt) OR manual markers whose ``user_prompt`` is empty/missing,
    we fall back to the message timestamp so the indicator still
    conveys "compaction happened here".

    NOTE: this is for the OFF state (``include_compact=False``). When
    the pref is ON, the summary message renders via
    :func:`render_compact_summary_markdown` /
    :func:`render_compact_summary_html` (rich visual treatment that
    mirrors ``frontend/src/components/conversation/CompactMarker.tsx``).
    """
    marker = compact_marker_by_uuid.get(message.uuid)
    if marker is None:
        return None
    kind = marker.kind  # 'manual' | 'auto'
    prompt = (marker.user_prompt or "").strip() if marker.kind == "manual" else ""
    if prompt:
        return f"── Compacted ({kind}): {prompt} ──"
    return f"── Compacted ({kind}) at {format_timestamp(message.created_at)} ──"


def render_compact_summary_markdown(
    message: Message,
    compact_marker_by_uuid: dict[str, CompactMarker],
) -> str | None:
    """Render the rich Markdown block for a /compact summary message
    when ``include_compact=True``. Returns None if ``message`` is not
    a known summary marker.

    Mirrors the viewer's ``CompactMarker.tsx`` panel — purple-bordered
    block with a ``Compacted (kind)`` header, an optional
    ``You asked:`` subsection (manual markers only, when a
    ``user_prompt`` is present), and a ``Summary:`` subsection
    carrying the LLM body verbatim. Markdown blockquote (``>``)
    syntax stands in for the purple left border the viewer uses.

    Why a separate helper from :func:`render_compact_indicator`: the
    OFF state collapses both the trigger row AND the summary message
    to a single indicator line (the user opted out of verbose
    compaction content). The ON state must distinguish the LLM
    summary from a regular user message — without this, the summary
    body renders identically to a "You: ..." human message and the
    reader can't tell which turns are summarisations vs. real
    prompts. User report 2026-05-24.
    """
    marker = compact_marker_by_uuid.get(message.uuid)
    if marker is None:
        return None
    kind = marker.kind
    timestamp = format_timestamp(message.created_at)
    prompt = (marker.user_prompt or "").strip() if kind == "manual" else ""
    # Filter tool placeholders from the summary body to preserve the
    # P1.3b invariant (placeholders must never leak to recipients
    # regardless of which path renders the text). The `or message.text`
    # fallback covers the edge case where marker.summary_text is empty
    # but the message itself carries the summary; in either case the
    # placeholder filter runs.
    raw_summary = marker.summary_text or message.text or ""
    summary_text = filter_tool_placeholders(raw_summary).strip()

    lines: list[str] = [
        f"> **─── Compacted ({kind}) at {timestamp} ───**",
        ">",
    ]
    if prompt:
        lines.append(f"> **You asked:** {prompt}")
        lines.append(">")
    lines.append("> **Summary:**")
    lines.append(">")
    # Blockquote each line of the summary so multi-line summaries
    # stay visually grouped inside the rich block. Empty lines stay
    # blockquoted with a bare `>` so CommonMark / GFM / Obsidian /
    # MacDown don't break the block on the blank.
    for body_line in summary_text.split("\n"):
        if body_line == "":
            lines.append(">")
        else:
            lines.append(f"> {body_line}")
    return "\n".join(lines)


def render_compact_summary_html(
    message: Message,
    compact_marker_by_uuid: dict[str, CompactMarker],
) -> str | None:
    """HTML mirror of :func:`render_compact_summary_markdown`. Returns
    None if ``message`` is not a known summary marker.

    Emits a ``<div class="compact-summary compact-summary-<kind>">``
    block containing:

      * ``<div class="compact-summary-header">`` — "Compacted
        (manual/auto) at <timestamp>" pill, mirror of the viewer's
        CompactMarker.tsx pill.
      * Optional ``<div class="compact-summary-asked">`` block with
        ``<div class="compact-summary-asked-label">You asked</div>``
        + body — only for manual markers with a user_prompt.
      * ``<div class="compact-summary-body">`` with
        ``<div class="compact-summary-body-label">Summary</div>`` +
        the LLM summary text.

    Companion CSS lives in :func:`backend.exporters.pdf.conversation_to_html`
    so the PDF surface renders the purple-bordered visual the user
    expects from the viewer.
    """
    marker = compact_marker_by_uuid.get(message.uuid)
    if marker is None:
        return None
    kind = marker.kind  # 'manual' | 'auto'
    timestamp = format_timestamp(message.created_at)
    prompt = (marker.user_prompt or "").strip() if kind == "manual" else ""
    # P1.3b invariant: filter tool placeholders before HTML-escape.
    raw_summary = marker.summary_text or message.text or ""
    summary_text = filter_tool_placeholders(raw_summary).strip()

    # `kind` is defensively escaped even though the model layer
    # restricts it to 'manual' | 'auto' — cheap insurance against a
    # future schema drift surfacing arbitrary text. The CSS class
    # uses raw `kind` because Pydantic enforces the literal type at
    # the API boundary.
    parts: list[str] = [
        f'<div class="compact-summary compact-summary-{kind}">',
        f'  <div class="compact-summary-header">Compacted ({escape_html(kind)}) at {escape_html(timestamp)}</div>',
    ]
    if prompt:
        parts.extend([
            '  <div class="compact-summary-asked">',
            '    <div class="compact-summary-asked-label">You asked</div>',
            f'    <div class="compact-summary-asked-body">{escape_html(prompt)}</div>',
            '  </div>',
        ])
    # Summary body relies on `white-space: pre-wrap` in the companion
    # CSS to preserve newlines; we don't pre-convert `\n` to `<br>`.
    parts.extend([
        '  <div class="compact-summary-body">',
        '    <div class="compact-summary-body-label">Summary</div>',
        f'    <div class="compact-summary-body-text">{escape_html(summary_text)}</div>',
        '  </div>',
        '</div>',
    ])
    return "\n".join(parts)


def _dedupe_image_files(message: Message) -> list[dict[str, Any]]:
    """Merge files + files_v2 and dedupe by file_uuid; image files only."""
    merged: list[dict[str, Any]] = []
    for raw in (message.files or []) + (getattr(message, "files_v2", None) or []):
        if isinstance(raw, dict) and raw.get("file_kind") == "image":
            merged.append(raw)
    by_uuid: dict[str, dict[str, Any]] = {}
    for f in merged:
        uuid = f.get("file_uuid") or f.get("file_name") or ""
        existing = by_uuid.get(uuid)
        if not existing:
            by_uuid[uuid] = f
            continue
        # Prefer the entry with a preview_asset.url present.
        existing_url = (existing.get("preview_asset") or {}).get("url")
        new_url = (f.get("preview_asset") or {}).get("url")
        if not existing_url and new_url:
            by_uuid[uuid] = f
    return list(by_uuid.values())


def _dedupe_non_image_files(message: Message) -> list[dict[str, Any]]:
    """Merge files + files_v2 and dedupe by file_uuid; non-image files only.

    Non-image entries (PDFs, .txt, .docx, etc.) ship as ``file_kind`` values
    other than ``"image"`` (typically ``"document"``). These are bundled
    under ``attachments/`` in the Markdown bundle export.
    """
    merged: list[dict[str, Any]] = []
    for raw in (message.files or []) + (getattr(message, "files_v2", None) or []):
        if isinstance(raw, dict) and raw.get("file_kind") and raw.get("file_kind") != "image":
            merged.append(raw)
    by_uuid: dict[str, dict[str, Any]] = {}
    for f in merged:
        uuid = f.get("file_uuid") or f.get("uuid") or f.get("file_name") or ""
        existing = by_uuid.get(uuid)
        if not existing:
            by_uuid[uuid] = f
            continue
        # Prefer the entry whose document_asset/url surface is populated
        # (the v2 shape often has the live URL the v1 shape lacks).
        def _has_doc_url(d: dict[str, Any]) -> bool:
            return bool(
                d.get("document_url")
                or (d.get("document_asset") or {}).get("url")
                or d.get("local_document")
            )

        if not _has_doc_url(existing) and _has_doc_url(f):
            by_uuid[uuid] = f
    return list(by_uuid.values())


def message_has_visible_content(message: Message, include_tools: bool = True) -> bool:
    """Check if a message has any visible content (considering tool call visibility).

    A message with image attachments is always visible (Council Q7: images
    are primary content, not gated by toggles).
    """
    if _dedupe_image_files(message):
        return True
    if message.text and message.text.strip():
        if not include_tools:
            filtered = filter_tool_placeholders(message.text).strip()
            if not filtered:
                return False
        return True
    if message.content:
        for block in message.content:
            if block.type == "text" and block.text and block.text.strip():
                return True
            if block.type in ("tool_use", "tool_result") and include_tools:
                return True
    return False


# ---------------------------------------------------------------------------
# On-disk attachment resolution (used by BOTH pdf.py and bundle.py).
# Lives in _shared so the bundle surface doesn't need to import pdf.py.
# ---------------------------------------------------------------------------


def _resolve_attachment_path(conv_uuid: str, file_uuid: str, variant: str) -> Path | None:
    """Find on-disk bytes for a cached desktop attachment."""
    try:
        from ..config import get_settings
    except Exception:  # pragma: no cover
        return None
    data_dir = get_settings().data_dir
    files_root = data_dir.parent / "files" if data_dir.name == "conversations" else data_dir / "files"
    file_dir = files_root / conv_uuid / file_uuid
    if not file_dir.is_dir():
        return None
    matches = sorted(file_dir.glob(f"{variant}.*"))
    if not matches:
        bare = file_dir / variant
        if bare.is_file():
            return bare
        return None
    return matches[0]


__all__ = [
    "CC_IMAGE_MARKER_RE",
    "TOOL_PLACEHOLDER",
    "TOOL_PLACEHOLDERS",
    "TOOL_PLACEHOLDER_MOBILE_ARTIFACT",
    "MarkdownDialect",
    "_dedupe_image_files",
    "_dedupe_non_image_files",
    "_guess_mime",
    "_is_compact_summary_message",
    "_is_excludable_marker",
    "_resolve_attachment_path",
    "escape_html",
    "filter_tool_placeholders",
    "format_timestamp",
    "message_has_visible_content",
    "render_compact_indicator",
    "render_compact_summary_html",
    "render_compact_summary_markdown",
    "sanitize_filename",
]


# Re-export ContentBlock and Message for convenience to consumers who
# import the surface modules: keeps their import lists short.
__all__ += ["ContentBlock", "Message"]
