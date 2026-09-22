"""PDF / HTML export surface — WeasyPrint backend + image url_fetcher.

Extracted from ``backend/export.py`` (Council A2, 2026-05-21). Backwards-
compatible imports continue to work via the ``backend.export`` facade.

The WeasyPrint import is intentionally LAZY (inside ``create_pdf``) so
``import backend.export`` doesn't pull in WeasyPrint for Markdown-only
callers. Don't promote it to a module-level import without first
auditing every consumer of ``backend.export``.
"""

from __future__ import annotations

import json
import logging
import threading
import re
import urllib.parse
from pathlib import Path
from typing import Any

from ..models import ConversationDetail, ContentBlock, Message
from ..search_text import _is_compact_trigger_message
from ._shared import (
    CC_IMAGE_MARKER_RE,
    _dedupe_image_files,
    _guess_mime,
    _is_excludable_marker,
    _is_compact_summary_message,
    _resolve_attachment_path,
    escape_html,
    filter_tool_placeholders,
    format_timestamp,
    message_has_visible_content,
    render_compact_indicator,
    render_compact_summary_html,
)


log = logging.getLogger(__name__)

# Guards the native WeasyPrint render; see create_pdf for the rationale.
_RENDER_LOCK = threading.Lock()


# 1x1 transparent PNG used as a placeholder when an image referenced by
# the conversation HTML can't be resolved on disk. WeasyPrint requires
# the url_fetcher to return *some* bytes; raising here would abort the
# entire PDF render.
_TRANSPARENT_1x1_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44"
    "ae426082"
)


_CC_IMAGE_PATH_RE = re.compile(r"/api/cc-image\b")
_FILES_PROXY_PATH_RE = re.compile(r"/api/[^/]+/files/([0-9a-fA-F-]+)/(thumbnail|preview)")
_ATTACHMENTS_PATH_RE = re.compile(
    r"/api/attachments/([0-9a-fA-F-]+)/([0-9a-fA-F-]+)/(thumbnail|preview|original|document)"
)


def _image_html(message: Message) -> str:
    """Render the message's image attachments as an HTML block.

    WeasyPrint accepts both data: URIs and absolute http(s) URLs. Our
    image URLs are claude.ai-relative (``/api/...``); WeasyPrint can't
    fetch those directly, so we wrap each image in a <p> with the
    filename as a fallback caption when the URL fails to resolve.
    """
    images = _dedupe_image_files(message)
    if not images:
        return ""
    parts: list[str] = ['<div class="attachments">']
    for img in images:
        url = (img.get("preview_asset") or {}).get("url") or img.get("thumbnail_url") or ""
        name = img.get("file_name") or "image"
        alt = f"Image attachment: {name}"
        if url:
            parts.append(
                f'<figure class="attachment">'
                f'<img src="{escape_html(url)}" alt="{escape_html(alt)}" '
                f'style="max-width:100%;max-height:480px;height:auto;display:block;" />'
                f'<figcaption style="font-size:11px;color:#666;">{escape_html(name)}</figcaption>'
                f"</figure>"
            )
        else:
            parts.append(
                f'<p class="attachment-missing"><em>(image attachment unavailable: '
                f"{escape_html(name)})</em></p>"
            )
    parts.append("</div>")
    return "".join(parts)


def _rewrite_cc_image_markers_to_html(text: str) -> str:
    """Convert ``[Image: source: <abs-path>]`` markers in ``text`` to
    ``<img>`` tags, escaping the surrounding text as HTML.

    The marker syntax is the source of truth shared with the frontend
    and ``backend/cc_image_cache.py``. Output ``src`` uses the same
    ``/api/cc-image?path=<urlquoted-abs>`` URL the viewer uses; the PDF
    pass resolves it through ``_pdf_url_fetcher`` (no HTTP server
    needed). Malformed markers are left intact (and HTML-escaped) so
    we don't silently drop content.
    """
    out_parts: list[str] = []
    last = 0
    for match in CC_IMAGE_MARKER_RE.finditer(text):
        out_parts.append(escape_html(text[last : match.start()]))
        abs_path = match.group(1).strip()
        if not abs_path:
            out_parts.append(escape_html(match.group(0)))
        else:
            quoted = urllib.parse.quote(abs_path, safe="")
            name = Path(abs_path).name or "image"
            out_parts.append(
                f'<img src="/api/cc-image?path={quoted}" '
                f'alt="{escape_html(name)}" '
                f'style="max-width:100%;max-height:480px;height:auto;display:block;" />'
            )
        last = match.end()
    out_parts.append(escape_html(text[last:]))
    return "".join(out_parts)


def _render_image_block_html(block: ContentBlock) -> str:
    """Render a ``type == "image"`` content block.

    Common Claude Code shapes:
      * ``{"source": {"type": "base64", "media_type": "image/png", "data": "..."}}``
        → emit a ``data:`` URL (works natively in WeasyPrint).
      * ``{"source": {"type": "url", "url": "/api/cc-image?path=..."}}``
        → emit the URL verbatim (resolved via ``_pdf_url_fetcher``).
    """
    src = block.source
    if not isinstance(src, dict):
        return ""
    src_type = src.get("type")
    if src_type == "base64":
        media = src.get("media_type") or "image/png"
        data = src.get("data") or ""
        if not data:
            return ""
        return (
            f'<img src="data:{escape_html(media)};base64,{escape_html(data)}" '
            f'alt="inline image" '
            f'style="max-width:100%;max-height:480px;height:auto;display:block;" />'
        )
    if src_type == "url":
        url = src.get("url") or ""
        if not url:
            return ""
        return (
            f'<img src="{escape_html(url)}" alt="inline image" '
            f'style="max-width:100%;max-height:480px;height:auto;display:block;" />'
        )
    return ""


def render_content_block_html(block: ContentBlock, include_tools: bool = True) -> str:
    """Render a content block to HTML."""
    if block.type == "text" and block.text:
        # Always strip TOOL_PLACEHOLDER (P1.3b), then rewrite cc-image
        # markers to <img> tags before HTML escape.
        return _rewrite_cc_image_markers_to_html(filter_tool_placeholders(block.text))

    if block.type == "image":
        return _render_image_block_html(block)

    if block.type == "tool_use":
        if not include_tools:
            return ""
        input_str = json.dumps(block.input, indent=2) if block.input else ""
        return f"""
        <div class="tool-use">
            <strong>Tool: {escape_html(block.name or '')}</strong>
            <pre><code>{escape_html(input_str)}</code></pre>
        </div>
        """

    if block.type == "tool_result" and block.content:
        if not include_tools:
            return ""
        result_html = ""
        for child in block.content:
            result_html += render_content_block_html(child, include_tools)
        return f"""
        <div class="tool-result">
            <strong>Tool Result</strong>
            {result_html}
        </div>
        """

    return ""


def conversation_to_html(
    conversation: ConversationDetail,
    include_tools: bool = True,
    include_compact: bool = False,
) -> str:
    """Convert a conversation to HTML for PDF rendering.

    ``include_compact`` (V1 polish 2026-05-24, default False) mirrors
    the Markdown exporter's gate. When OFF, the ``isCompactSummary``
    row collapses to a single ``<div class="compact-indicator">``
    block and the trigger row is dropped entirely.
    """
    # Basic HTML template with embedded CSS
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{escape_html(conversation.name or "")}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
            color: #333;
        }}
        h1 {{
            border-bottom: 2px solid #eee;
            padding-bottom: 10px;
        }}
        .meta {{
            color: #666;
            font-size: 14px;
            margin-bottom: 30px;
        }}
        .message {{
            margin-bottom: 24px;
            padding: 16px;
            border-radius: 8px;
        }}
        .message.human {{
            background: #e3f2fd;
        }}
        .message.assistant {{
            background: #f5f5f5;
        }}
        .message-header {{
            font-weight: bold;
            margin-bottom: 8px;
            color: #555;
        }}
        .message-header .timestamp {{
            font-weight: normal;
            font-size: 12px;
            color: #888;
        }}
        .message-content {{
            white-space: pre-wrap;
        }}
        pre {{
            background: #f8f8f8;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 12px;
            overflow-x: auto;
            font-size: 13px;
        }}
        code {{
            font-family: 'SF Mono', 'Fira Code', monospace;
        }}
        .tool-use {{
            background: #fff3e0;
            border: 1px solid #ffb74d;
            border-radius: 4px;
            padding: 12px;
            margin: 8px 0;
        }}
        .tool-result {{
            background: #e8f5e9;
            border: 1px solid #81c784;
            border-radius: 4px;
            padding: 12px;
            margin: 8px 0;
        }}
        hr {{
            border: none;
            border-top: 1px solid #eee;
            margin: 20px 0;
        }}
        .compact-indicator {{
            text-align: center;
            color: #999;
            font-size: 12px;
            font-style: italic;
            margin: 16px 0;
            padding: 6px 0;
            border-top: 1px dashed #ddd;
            border-bottom: 1px dashed #ddd;
        }}
        /* Rich /compact summary block, ON state (include_compact=True).
           Mirrors CompactMarker.tsx in the viewer (purple-bordered
           panel with "You asked" + "Summary" subsections) so the
           recipient of a PDF export sees the same visual distinction
           between LLM summarisation turns and real user/assistant
           messages that they see in the viewer. */
        .compact-summary {{
            border-left: 4px solid #9333ea;  /* purple-600 */
            background: #faf5ff;             /* purple-50 */
            margin: 20px 0;
            padding: 16px 18px;
            border-radius: 6px;
        }}
        .compact-summary-header {{
            font-weight: bold;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #6b21a8;                  /* purple-800 */
            margin-bottom: 12px;
        }}
        /* 2026-05-24 user report: the "You asked" sub-block previously
           used a blue color family which visually separated it from
           the purple "Summary" sub-block — they looked like two
           unrelated panels. Unified to the purple family so the whole
           compaction reads as ONE block (mirror of the viewer change
           in CompactMarker.tsx). The faint purple-50 bg keeps the
           visual hierarchy between label and body without leaking
           into a different color family. */
        .compact-summary-asked {{
            margin-bottom: 12px;
            background: #faf5ff;             /* purple-50 */
            padding: 8px 12px;
            border-radius: 4px;
        }}
        .compact-summary-asked-label {{
            font-weight: bold;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #6b21a8;                  /* purple-800 (matches body label) */
            margin-bottom: 4px;
        }}
        .compact-summary-asked-body {{
            color: #3b0764;                  /* purple-950 */
            font-size: 13px;
        }}
        .compact-summary-body-label {{
            font-weight: bold;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #6b21a8;                  /* purple-800 */
            margin-bottom: 6px;
        }}
        .compact-summary-body-text {{
            color: #27272a;
            font-size: 14px;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
    <h1>{escape_html(conversation.name or "")}</h1>
    <div class="meta">
        <strong>Model:</strong> {escape_html(conversation.model or "")}<br>
        <strong>Created:</strong> {format_timestamp(conversation.created_at)}<br>
        <strong>Messages:</strong> {conversation.message_count}
    </div>
"""

    compact_by_uuid = {
        m.message_uuid: m for m in (conversation.compact_markers or [])
    }
    compact_marker_uuids = set(compact_by_uuid)

    for message in conversation.messages:
        if _is_excludable_marker(message):
            continue
        # Drop the trigger row in BOTH states (V1 polish 2026-05-24):
        # the `<command-name>/compact</command-name>` envelope is
        # chrome the user never wants to see. See markdown.py for
        # full rationale.
        if _is_compact_trigger_message(message.model_dump()):
            continue
        if _is_compact_summary_message(message, compact_marker_uuids):
            if include_compact:
                rich = render_compact_summary_html(message, compact_by_uuid)
                if rich is not None:
                    html += f"    {rich}\n"
                continue
            # OFF state (V1 polish 2026-05-24): fully hide. No
            # indicator. Matches the viewer's "Show Compactions"
            # checkbox semantics. See markdown.py for full rationale.
            continue
        if not message_has_visible_content(message, include_tools):
            continue

        sender = "You" if message.sender == "human" else "Claude"
        timestamp = format_timestamp(message.created_at)

        # Get message content
        content_html = ""
        if message.content:
            for block in message.content:
                content_html += render_content_block_html(block, include_tools)
        elif message.text:
            # Always strip TOOL_PLACEHOLDER before HTML escape (P1.3b),
            # then rewrite cc-image markers to <img> tags.
            text = filter_tool_placeholders(message.text)
            content_html = _rewrite_cc_image_markers_to_html(text)

        # Append image attachments (always, never gated by include_tools).
        # Mirrors the Markdown export's _image_markdown helper so the PDF
        # surface stays in sync with viewer + Markdown ("one truth, three
        # surfaces").
        images_html = _image_html(message)

        html += f"""
    <div class="message {message.sender}">
        <div class="message-header">
            {sender} <span class="timestamp">{timestamp}</span>
        </div>
        <div class="message-content">{content_html}{images_html}</div>
    </div>
"""

    html += """
</body>
</html>
"""
    return html


def _resolve_cc_image_path(abs_path: str) -> Path | None:
    """Find on-disk bytes for a ``/api/cc-image?path=<abs>`` URL.

    Order:
      1. The original absolute path (Claude Code's live cache).
      2. The permanent cache populated at fetch time
         (``~/.claude-explorer/cc-images/*/<sess>--<N>.*.<ext>`` —
         pick newest mtime).

    Returns None if neither exists.
    """
    if not abs_path:
        return None
    try:
        candidate = Path(abs_path).expanduser()
    except (OSError, ValueError):
        return None
    if candidate.is_file():
        return candidate

    # Fallback to the permanent cache. Mirrors backend.routers.files.get_cc_image.
    try:
        from ..cc_image_cache import cache_dir
    except Exception:  # pragma: no cover — defensive
        return None
    sess = candidate.parent.name
    n = candidate.stem
    ext = candidate.suffix.lstrip(".") or "png"
    cache_root = cache_dir()
    if not cache_root.exists():
        return None
    matches = list(cache_root.glob(f"*/{sess}--{n}.*.{ext}"))
    if not matches:
        return None
    return max(matches, key=lambda x: x.stat().st_mtime)


def _build_pdf_url_fetcher(conversation: ConversationDetail) -> Any:
    """Build a WeasyPrint ``url_fetcher`` that resolves the URL shapes
    our HTML emits to on-disk bytes.

    Handled URL shapes:
      * ``/api/cc-image?path=<abs>`` (and ``http(s)://.../api/cc-image?...``)
      * ``/api/<org>/files/<uuid>/{thumbnail,preview}`` — Desktop attachment
        proxy. Resolved against the per-conv files cache using
        ``conversation.uuid`` from this closure.
      * ``/api/attachments/<conv>/<file>/<variant>`` — local cached attachment.
      * ``data:`` URLs — defer to WeasyPrint's default fetcher.
      * Anything else — defer to the default fetcher (which will raise
        for non-data, non-file schemes; that's fine, it's external).

    Missing files emit a 1x1 transparent PNG placeholder so the PDF
    render never aborts on a single broken image.
    """
    conv_uuid = conversation.uuid

    def fetcher(url: str) -> dict[str, Any]:
        # `data:` URIs are inline (no network round trip) and WeasyPrint
        # may emit them internally for fonts or generated images, so we
        # defer them to the default fetcher. EVERY other scheme — http(s),
        # file, ftp — that doesn't match one of the three local-API regexes
        # below falls through to the transparent-PNG placeholder at the
        # bottom of this function. The default fetcher accepts all of those
        # schemes with no allow-list, so passing an unrecognised URL through
        # it would turn any HTML-injection vector in a user-controlled
        # field (conversation.name, conversation.model) into SSRF +
        # arbitrary-file-read during PDF render. Pinned in
        # tests/test_export_pdf_html_injection.py.
        if url.startswith("data:"):
            from weasyprint.urls import default_url_fetcher

            return default_url_fetcher(url)

        # /api/cc-image?path=<abs>
        if _CC_IMAGE_PATH_RE.search(url):
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            path_values = qs.get("path", [])
            abs_path = path_values[0] if path_values else ""
            resolved = _resolve_cc_image_path(abs_path)
            if resolved is not None:
                try:
                    return {
                        "string": resolved.read_bytes(),
                        "mime_type": _guess_mime(resolved),
                    }
                except OSError as exc:
                    log.warning("PDF url_fetcher: read failed for %s: %s", resolved, exc)
            else:
                log.warning("PDF url_fetcher: cc-image not found on disk: %s", abs_path)
            return {"string": _TRANSPARENT_1x1_PNG, "mime_type": "image/png"}

        # /api/<org>/files/<uuid>/{variant}
        files_match = _FILES_PROXY_PATH_RE.search(url)
        if files_match:
            file_uuid = files_match.group(1)
            variant = files_match.group(2)
            resolved = _resolve_attachment_path(conv_uuid, file_uuid, variant)
            if resolved is not None:
                try:
                    return {
                        "string": resolved.read_bytes(),
                        "mime_type": _guess_mime(resolved),
                    }
                except OSError as exc:
                    log.warning("PDF url_fetcher: read failed for %s: %s", resolved, exc)
            else:
                log.warning(
                    "PDF url_fetcher: attachment not cached for %s/%s/%s",
                    conv_uuid, file_uuid, variant,
                )
            return {"string": _TRANSPARENT_1x1_PNG, "mime_type": "image/png"}

        # /api/attachments/<conv>/<file>/<variant>
        att_match = _ATTACHMENTS_PATH_RE.search(url)
        if att_match:
            ac_uuid = att_match.group(1)
            file_uuid = att_match.group(2)
            variant = att_match.group(3)
            resolved = _resolve_attachment_path(ac_uuid, file_uuid, variant)
            if resolved is not None:
                try:
                    return {
                        "string": resolved.read_bytes(),
                        "mime_type": _guess_mime(resolved),
                    }
                except OSError as exc:
                    log.warning("PDF url_fetcher: read failed for %s: %s", resolved, exc)
            else:
                log.warning(
                    "PDF url_fetcher: attachment not cached for %s/%s/%s",
                    ac_uuid, file_uuid, variant,
                )
            return {"string": _TRANSPARENT_1x1_PNG, "mime_type": "image/png"}

        log.warning("PDF url_fetcher: refusing unknown URL scheme: %r", url)
        return {"string": _TRANSPARENT_1x1_PNG, "mime_type": "image/png"}

    return fetcher


def create_pdf(
    conversation: ConversationDetail,
    include_tools: bool = True,
    include_compact: bool = False,
) -> bytes:
    """Create a PDF from a conversation using WeasyPrint.

    Image embedding: the HTML pass emits ``<img>`` tags whose ``src``
    points at our local API URL shapes (``/api/cc-image?path=...``,
    ``/api/<org>/files/<uuid>/{variant}``). The PDF pass has no HTTP
    server context, so we plumb a ``url_fetcher`` that reads bytes
    directly from disk (with a placeholder for missing images so a
    single broken reference doesn't abort the whole render).

    ``include_compact`` is forwarded to :func:`conversation_to_html`.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        raise RuntimeError(
            "WeasyPrint is required for PDF export. Install with: pip install weasyprint"
        )

    html_content = conversation_to_html(
        conversation, include_tools, include_compact=include_compact
    )
    fetcher = _build_pdf_url_fetcher(conversation)
    # base_url is required for WeasyPrint to resolve our root-relative
    # URLs (``/api/cc-image?path=...``) before invoking the fetcher.
    # The scheme/host don't matter — the fetcher matches on path.
    # Serialize the render. WeasyPrint draws through cairo and pango via
    # cffi, and those libraries are not reliably thread-safe. Both callers
    # reach this function on a worker thread (the HTTP route uses
    # asyncio.to_thread, and the MCP server calls it directly), so two
    # concurrent exports would otherwise render at the same time.
    #
    # This is not theoretical: a pytest-xdist worker HARD CRASHED here on
    # 2026-09-22, a process-level crash rather than a failed assertion.
    # In the running server the same collision would take down `serve`
    # mid-request for every connected user.
    #
    # A lock makes a second export wait instead of crash. PDF rendering is
    # already slow and already has its own timeout, so queueing is the right
    # trade. The lock is NOT held while building the HTML, only across the
    # native render.
    with _RENDER_LOCK:
        pdf_bytes = HTML(
            string=html_content,
            url_fetcher=fetcher,
            base_url="http://claude-explorer.local/",
        ).write_pdf()
    return pdf_bytes


__all__ = [
    "_TRANSPARENT_1x1_PNG",
    "_CC_IMAGE_PATH_RE",
    "_FILES_PROXY_PATH_RE",
    "_ATTACHMENTS_PATH_RE",
    "_image_html",
    "_rewrite_cc_image_markers_to_html",
    "_render_image_block_html",
    "render_content_block_html",
    "conversation_to_html",
    "_resolve_cc_image_path",
    "_build_pdf_url_fetcher",
    "create_pdf",
]
