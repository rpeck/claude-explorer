"""Export functionality for Markdown and PDF.

Backwards-compatibility facade for ``backend.exporters.*`` (Council A2
2026-05-21). The Markdown / PDF / bundle surfaces and their shared
helpers live in:

  * ``backend.exporters._shared``  — visibility helpers + dialect +
                                     placeholder filtering + on-disk
                                     attachment resolution
  * ``backend.exporters.markdown`` — ``conversation_to_markdown``,
                                     ``create_markdown_zip``, etc.
  * ``backend.exporters.pdf``      — ``create_pdf``, ``conversation_to_html``,
                                     WeasyPrint url_fetcher, image resolvers
  * ``backend.exporters.bundle``   — ``create_markdown_bundle`` and
                                     its bundle-side helpers

This module re-exports every name the previous monolith exposed so
existing imports continue to work byte-for-byte:

  * ``from backend.export import X``                     (tests)
  * ``from ..export import X``                           (router)
  * monkeypatch targets on ``backend.routers.export.X``  (unchanged —
    the router's value-bound names live there, not here)

Prefer importing directly from the submodules for new code.
"""

from .exporters._shared import (
    CC_IMAGE_MARKER_RE,
    TOOL_PLACEHOLDER,
    TOOL_PLACEHOLDER_MOBILE_ARTIFACT,
    TOOL_PLACEHOLDERS,
    MarkdownDialect,
    _dedupe_image_files,
    _dedupe_non_image_files,
    _guess_mime,
    _is_excludable_marker,
    _resolve_attachment_path,
    escape_html,
    filter_tool_placeholders,
    format_timestamp,
    message_has_visible_content,
    sanitize_filename,
)
from .exporters.bundle import (
    _bundle_block_to_markdown,
    _bundle_message_to_markdown,
    _image_marker_path_is_safe,
    _markdown_attachment_ref,
    _markdown_image_ref,
    _maybe_bundle_marker,
    _resolve_bundle_attachment_path,
    create_markdown_bundle,
)
from .exporters.markdown import (
    _EMPTY_CORPUS_README,
    _image_markdown,
    conversation_to_markdown,
    create_markdown_zip,
    message_to_markdown,
    render_content_block,
)
from .exporters.pdf import (
    _ATTACHMENTS_PATH_RE,
    _CC_IMAGE_PATH_RE,
    _FILES_PROXY_PATH_RE,
    _build_pdf_url_fetcher,
    _image_html,
    _render_image_block_html,
    _resolve_cc_image_path,
    _rewrite_cc_image_markers_to_html,
    _TRANSPARENT_1x1_PNG,
    conversation_to_html,
    create_pdf,
    render_content_block_html,
)
from .models import ContentBlock, ConversationDetail, Message  # noqa: F401  re-export

__all__ = [
    # _shared
    "CC_IMAGE_MARKER_RE",
    "MarkdownDialect",
    "TOOL_PLACEHOLDER",
    "TOOL_PLACEHOLDER_MOBILE_ARTIFACT",
    "TOOL_PLACEHOLDERS",
    "_dedupe_image_files",
    "_dedupe_non_image_files",
    "_guess_mime",
    "_is_excludable_marker",
    "_resolve_attachment_path",
    "escape_html",
    "filter_tool_placeholders",
    "format_timestamp",
    "message_has_visible_content",
    "sanitize_filename",
    # markdown
    "_EMPTY_CORPUS_README",
    "_image_markdown",
    "conversation_to_markdown",
    "create_markdown_zip",
    "message_to_markdown",
    "render_content_block",
    # pdf
    "_ATTACHMENTS_PATH_RE",
    "_CC_IMAGE_PATH_RE",
    "_FILES_PROXY_PATH_RE",
    "_TRANSPARENT_1x1_PNG",
    "_build_pdf_url_fetcher",
    "_image_html",
    "_render_image_block_html",
    "_resolve_cc_image_path",
    "_rewrite_cc_image_markers_to_html",
    "conversation_to_html",
    "create_pdf",
    "render_content_block_html",
    # bundle
    "_bundle_block_to_markdown",
    "_bundle_message_to_markdown",
    "_image_marker_path_is_safe",
    "_markdown_attachment_ref",
    "_markdown_image_ref",
    "_maybe_bundle_marker",
    "_resolve_bundle_attachment_path",
    "create_markdown_bundle",
]
