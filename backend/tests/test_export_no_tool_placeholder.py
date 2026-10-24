"""P1.3b — TOOL_PLACEHOLDER must be stripped from BOTH Markdown and PDF
backend exports.

Claude Desktop emits the literal string ``"This block is not supported on
your current device yet."`` as a placeholder where a tool call or
artifact existed in the original session but couldn't be serialized to
the export. The frontend viewer hides this string entirely (replaced
with a friendly badge — see `frontend/.../MarkdownRenderer.tsx`); the
backend export surfaces (Markdown + PDF) MUST do the same so the user
isn't shipping that string to colleagues.

The frontend's canonical algorithm
(``stripToolPlaceholderText`` in ``MarkdownRenderer.tsx``):

  - OUTSIDE a fenced code block: drop ALL occurrences anywhere on the
    line (line-anchored OR mid-paragraph). If the line was non-empty
    before but is whitespace-only after, drop the line entirely.
  - INSIDE a fenced code block: leave the placeholder intact (the
    viewer's `code` component renders the badge using that text).

For backend exports we don't have the badge surface, so we strip the
placeholder unconditionally — fenced or not — to guarantee the literal
string is never visible to the recipient. This is stricter than the
frontend, but the user's requirement is "must not contain the literal
placeholder."
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOOL_PLACEHOLDER = "This block is not supported on your current device yet."


def _conv_with_placeholder(uuid: str, text: str) -> dict:
    """Build a minimal CLAUDE_AI-shape conversation that contains the
    placeholder somewhere in its sole human message."""
    return {
        "uuid": uuid,
        "name": "Conv with placeholder",
        "summary": "",
        "source": "CLAUDE_AI",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": "m1",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": text,
                # NOTE: leaving `content` empty forces the export to
                # use the `text` codepath (the one that calls
                # `filter_tool_placeholders`).
                "content": [],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            }
        ],
    }


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Stand up an isolated CLAUDE_EXPLORER_DATA_DIR + clear caches."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Also point CLAUDE_DIR somewhere harmless so CC reader doesn't
    # pick up the developer's real ~/.claude during the test.
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))

    from backend import cache
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    cache.clear_cache()

    yield data_dir

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    cache.clear_cache()


def _write_conv(data_dir: Path, conv: dict) -> None:
    (data_dir / f"{conv['uuid']}.json").write_text(json.dumps(conv))


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app)


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF for substring assertions.

    Prefer ``pypdf`` if available (handles compressed text streams);
    otherwise fall back to a brute-force latin-1 decode + scan, which
    works because WeasyPrint embeds the visible text as UTF-8 inside
    content streams that are usually NOT compressed for tiny inputs.
    The latin-1 fallback is intentionally permissive — if the
    placeholder string appears in ANY decoded byte run, we want the
    test to fail loudly.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return pdf_bytes.decode("latin-1", errors="ignore")

    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001, S112 — any extractor failure is a non-signal here
            continue
    text = "\n".join(out)
    # Belt-and-suspenders: also scan raw bytes in case the extractor
    # drops the offending text. WeasyPrint emits visible text into
    # uncompressed Tj/TJ ops for small inputs.
    return text + "\n" + pdf_bytes.decode("latin-1", errors="ignore")


# ---------- Markdown ----------------------------------------------------


def test_markdown_export_strips_tool_placeholder(isolated_data_dir):
    """Placeholder fenced inside a ``` block (Claude Desktop's usual
    shape) must NOT appear in the exported Markdown."""
    uuid = "11111111-1111-1111-1111-111111111111"
    text = (
        "Before the placeholder.\n\n"
        "```\n"
        f"{TOOL_PLACEHOLDER}\n"
        "```\n\n"
        "After the placeholder."
    )
    _write_conv(isolated_data_dir, _conv_with_placeholder(uuid, text))

    response = _client().get(f"/api/conversations/{uuid}/export/markdown")
    assert response.status_code == 200, response.text
    body = response.text
    assert TOOL_PLACEHOLDER not in body, (
        f"placeholder leaked into markdown export:\n{body}"
    )
    # Surrounding text must still be present.
    assert "Before the placeholder." in body
    assert "After the placeholder." in body


def test_markdown_export_strips_mid_paragraph_placeholder(isolated_data_dir):
    """Placeholder appearing mid-paragraph (not on its own line, not
    fenced) must also be stripped."""
    uuid = "22222222-2222-2222-2222-222222222222"
    text = f"Before. {TOOL_PLACEHOLDER} After."
    _write_conv(isolated_data_dir, _conv_with_placeholder(uuid, text))

    response = _client().get(f"/api/conversations/{uuid}/export/markdown")
    assert response.status_code == 200, response.text
    body = response.text
    assert TOOL_PLACEHOLDER not in body, (
        f"mid-paragraph placeholder leaked into markdown export:\n{body}"
    )
    # Surrounding text must still be present.
    assert "Before." in body
    assert "After." in body


# ---------- PDF ---------------------------------------------------------


def test_pdf_export_strips_tool_placeholder(isolated_data_dir):
    """Placeholder fenced inside a ``` block must NOT appear in the
    exported PDF bytes."""
    uuid = "33333333-3333-3333-3333-333333333333"
    text = (
        "Before the placeholder.\n\n"
        "```\n"
        f"{TOOL_PLACEHOLDER}\n"
        "```\n\n"
        "After the placeholder."
    )
    _write_conv(isolated_data_dir, _conv_with_placeholder(uuid, text))

    response = _client().get(f"/api/conversations/{uuid}/export/pdf")
    assert response.status_code == 200, response.text
    text_in_pdf = _pdf_text(response.content)
    assert TOOL_PLACEHOLDER not in text_in_pdf, (
        "placeholder leaked into PDF export"
    )


def test_pdf_export_strips_mid_paragraph_placeholder(isolated_data_dir):
    """Placeholder appearing mid-paragraph must also be stripped from
    the PDF."""
    uuid = "44444444-4444-4444-4444-444444444444"
    text = f"Before. {TOOL_PLACEHOLDER} After."
    _write_conv(isolated_data_dir, _conv_with_placeholder(uuid, text))

    response = _client().get(f"/api/conversations/{uuid}/export/pdf")
    assert response.status_code == 200, response.text
    text_in_pdf = _pdf_text(response.content)
    assert TOOL_PLACEHOLDER not in text_in_pdf, (
        "mid-paragraph placeholder leaked into PDF export"
    )
