"""Phase 6 — Markdown bundle export now also bundles non-image
attachments (PDF, .txt, .docx, etc.) under ``attachments/``.

Previously ``create_markdown_bundle`` only walked Claude Code image
sources (inline base64 + ``[Image: source: ...]`` markers) and image
``Message.files[]`` entries. Non-image attachments (``file_kind !=
"image"``) were silently dropped.

These tests assert the new ``attachments/`` zip prefix is populated
from on-disk bytes cached under
``~/.claude-explorer/files/<conv-uuid>/<file-uuid>/{document|original}``
(per fetcher contract) and that ``conversation.md`` rewrites the
references to point at the bundled file using the chosen Markdown
dialect.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.export import create_markdown_bundle
from backend.models import ConversationDetail, Message

CONV_UUID = "11111111-2222-3333-4444-555555555555"
FILE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PDF_BYTES = b"%PDF-1.4\nphase-6-attachment-test\n%%EOF\n"


def _make_conv_with_pdf_file() -> ConversationDetail:
    """Construct a ConversationDetail with a single human message that
    references a non-image (PDF) attachment in Message.files[]."""
    msg = Message(
        uuid="msg-1",
        sender="human",
        text="See attached spec.",
        content=[],
        created_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        files=[
            {
                "file_kind": "document",
                "file_uuid": FILE_UUID,
                "file_name": "spec.pdf",
                "document_url": f"/api/test-org/files/{FILE_UUID}/document",
            }
        ],
    )
    return ConversationDetail(
        uuid=CONV_UUID,
        name="Bundle attachments test",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 5, 6, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        message_count=1,
        human_message_count=1,
        messages=[msg],
    )


@pytest.fixture
def attachments_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand up an isolated ~/.claude-explorer layout: data_dir under
    tmp_path/conversations and the per-conv attachment cache under
    tmp_path/files. Returns the files_dir."""
    data_dir = tmp_path / "conversations"
    data_dir.mkdir()
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Isolate claude_dir so the bundle's image-cache scan can't escape tmp.
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))

    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield files_dir
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def _seed_pdf(files_dir: Path) -> Path:
    """Drop the on-disk PDF copy where the fetcher would have stored
    it: <files_dir>/<conv_uuid>/<file_uuid>/document.pdf."""
    file_dir = files_dir / CONV_UUID / FILE_UUID
    file_dir.mkdir(parents=True)
    path = file_dir / "document.pdf"
    path.write_bytes(PDF_BYTES)
    return path


def test_pdf_attachment_bundled_under_attachments_commonmark(
    attachments_data_dir: Path,
) -> None:
    """A PDF in Message.files[] with file_kind='document' must appear
    under attachments/<sanitized-name> in the zip, and conversation.md
    must contain a CommonMark link [spec.pdf](attachments/spec.pdf).
    """
    _seed_pdf(attachments_data_dir)
    conv = _make_conv_with_pdf_file()

    bundle = create_markdown_bundle(conv, dialect="commonmark")
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    names = zf.namelist()

    assert "conversation.md" in names, names
    attachment_names = [n for n in names if n.startswith("attachments/")]
    assert attachment_names == ["attachments/spec.pdf"], attachment_names

    assert zf.read("attachments/spec.pdf") == PDF_BYTES

    md = zf.read("conversation.md").decode("utf-8")
    assert "[spec.pdf](attachments/spec.pdf)" in md, md
    # No leftover absolute API URLs in bundled .md.
    assert "/api/" not in md, md


def test_pdf_attachment_bundled_under_attachments_obsidian(
    attachments_data_dir: Path,
) -> None:
    """Obsidian dialect uses wikilink syntax for the bundled
    attachment: [[attachments/spec.pdf]]."""
    _seed_pdf(attachments_data_dir)
    conv = _make_conv_with_pdf_file()

    bundle = create_markdown_bundle(conv, dialect="obsidian")
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    names = zf.namelist()

    assert "attachments/spec.pdf" in names, names
    assert zf.read("attachments/spec.pdf") == PDF_BYTES

    md = zf.read("conversation.md").decode("utf-8")
    assert "[[attachments/spec.pdf]]" in md, md
    # And no CommonMark-style ref to the same attachment.
    assert "](attachments/spec.pdf)" not in md, md


def test_attachment_with_no_on_disk_copy_is_skipped_cleanly(
    attachments_data_dir: Path,
) -> None:
    """If the cached bytes are missing, the bundle must still build
    (no exception). conversation.md surfaces a textual placeholder so
    the recipient knows an attachment was elided. The zip must NOT
    contain an empty attachments/ entry.
    """
    # NOTE: deliberately do NOT call _seed_pdf — the on-disk file is absent.
    conv = _make_conv_with_pdf_file()

    bundle = create_markdown_bundle(conv, dialect="commonmark")
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    names = zf.namelist()

    # No attachments dir entries when nothing was bundleable.
    assert not any(n.startswith("attachments/") for n in names), names

    md = zf.read("conversation.md").decode("utf-8")
    # conversation.md may either omit the link or render a textual
    # placeholder. We assert the placeholder shape to lock in the
    # contract — no dangling /api/... URL allowed either way.
    assert "/api/" not in md, md
    assert "spec.pdf" in md, md
    assert "(attachment not bundled" in md, md


def test_files_v2_pdf_attachment_bundled(attachments_data_dir: Path) -> None:
    """The same logic applies to files_v2 entries (deduped by
    file_uuid against files[])."""
    _seed_pdf(attachments_data_dir)
    msg = Message(
        uuid="msg-1",
        sender="human",
        text="See attached spec.",
        content=[],
        created_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        files_v2=[
            {
                "file_kind": "document",
                "file_uuid": FILE_UUID,
                "file_name": "spec.pdf",
                "document_asset": {
                    "url": f"/api/test-org/files/{FILE_UUID}/document"
                },
            }
        ],
    )
    conv = ConversationDetail(
        uuid=CONV_UUID,
        name="Bundle attachments v2",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 5, 6, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
        message_count=1,
        human_message_count=1,
        messages=[msg],
    )

    bundle = create_markdown_bundle(conv, dialect="commonmark")
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    names = zf.namelist()
    assert "attachments/spec.pdf" in names, names
    assert zf.read("attachments/spec.pdf") == PDF_BYTES
