"""P4c: fetch ALL Message.files[] attachments (not just images).

Currently fetcher/bulk_fetch.py downloads thumbnail/preview/original from
Message.files[] (image variants), but skips file_kind='document' entries
that carry their bytes via document_url. This means PDFs, txt, markdown,
etc. attached to a conversation never get cached locally.

P4c extends download_conversation_files to also fetch document_url when
present, storing under the existing
~/.claude-explorer/files/<conv-uuid>/<file-uuid>/document<ext> convention,
and adds a /api/attachments/<conv>/<file>/<variant> route that serves
the cached bytes (404 if not cached — no on-demand refetch).

Tests use a thin _download_file monkey-patch rather than mocking the
HTTP layer, since curl_cffi is impersonation-heavy and respx hooks at a
different layer than curl_cffi.requests.get.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_fetcher(tmp_path: Path):
    from fetcher.bulk_fetch import ClaudeFetcher

    org_uuid = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    return ClaudeFetcher(
        session_key="sk-test",
        orgs=[{"uuid": org_uuid, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        primary_org_id=org_uuid,
        output_dir=tmp_path / "conversations",
        files_dir=tmp_path / "files",
        download_files=True,
        delay=0.0,
    )


def _patch_download(fetcher, payloads: dict[str, bytes]):
    """Replace _download_file to return canned bytes keyed by URL.

    Writes the bytes to dest_path and returns (True, dest_path) so the
    surrounding flow stamps local_* fields exactly as in production.
    """
    def fake(url: str, dest_path: Path):
        body = payloads.get(url)
        if body is None:
            return False, dest_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(body)
        return True, dest_path

    fetcher._download_file = fake  # type: ignore[method-assign]


def test_fetch_downloads_pdf_attachment(tmp_path: Path) -> None:
    """A conversation with a PDF in Message.files[] downloads the PDF
    bytes into ~/.claude-explorer/files/<conv-uuid>/<file-uuid>/document<ext>."""
    fetcher = _make_fetcher(tmp_path)
    pdf_bytes = b"%PDF-1.4\n%fake pdf bytes\n%%EOF"
    pdf_url = "https://claude.ai/api/org-1/files/abcd-1234/document"
    _patch_download(fetcher, {pdf_url: pdf_bytes})

    conv_uuid = "11111111-2222-3333-4444-555555555555"
    conv = {
        "uuid": conv_uuid,
        "name": "Conv with PDF",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "sender": "human",
                "text": "Take a look at this",
                "files": [
                    {
                        "file_kind": "document",
                        "file_uuid": "abcd-1234",
                        "uuid": "abcd-1234",
                        "file_name": "spec.pdf",
                        "file_type": "application/pdf",
                        "document_url": pdf_url,
                    }
                ],
                "files_v2": [],
            }
        ],
    }
    fetcher.save_conversation(conv)

    expected = tmp_path / "files" / conv_uuid / "abcd-1234" / "document.pdf"
    assert expected.exists(), f"expected {expected}; tree:\n" + "\n".join(
        str(p) for p in (tmp_path / "files").rglob("*")
    )
    assert expected.read_bytes() == pdf_bytes


def test_fetch_downloads_txt_attachment(tmp_path: Path) -> None:
    """Same as PDF but with a plain-text attachment."""
    fetcher = _make_fetcher(tmp_path)
    txt_bytes = b"hello\nworld\n"
    txt_url = "https://claude.ai/api/org-1/files/efgh-5678/document"
    _patch_download(fetcher, {txt_url: txt_bytes})

    conv_uuid = "22222222-2222-3333-4444-555555555555"
    conv = {
        "uuid": conv_uuid,
        "name": "Conv with TXT",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "sender": "human",
                "text": "log file",
                "files": [
                    {
                        "file_kind": "document",
                        "file_uuid": "efgh-5678",
                        "uuid": "efgh-5678",
                        "file_name": "notes.txt",
                        "file_type": "text/plain",
                        "document_url": txt_url,
                    }
                ],
                "files_v2": [],
            }
        ],
    }
    fetcher.save_conversation(conv)

    expected = tmp_path / "files" / conv_uuid / "efgh-5678" / "document.txt"
    assert expected.exists()
    assert expected.read_bytes() == txt_bytes


def test_fetch_stamps_local_document_path(tmp_path: Path) -> None:
    """The conversation JSON saved to disk should carry local_document so
    the renderer / exporter can find the cached bytes."""
    fetcher = _make_fetcher(tmp_path)
    pdf_bytes = b"%PDF-1.4\n%%EOF"
    pdf_url = "https://claude.ai/api/org-1/files/zzz-9/document"
    _patch_download(fetcher, {pdf_url: pdf_bytes})

    conv_uuid = "33333333-2222-3333-4444-555555555555"
    conv = {
        "uuid": conv_uuid,
        "name": "Stamp test",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "sender": "human",
                "text": "",
                "files": [
                    {
                        "file_kind": "document",
                        "file_uuid": "zzz-9",
                        "uuid": "zzz-9",
                        "file_name": "x.pdf",
                        "file_type": "application/pdf",
                        "document_url": pdf_url,
                    }
                ],
                "files_v2": [],
            }
        ],
    }
    fetcher.save_conversation(conv)

    saved_path = (
        tmp_path
        / "conversations"
        / "by-org"
        / "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
        / f"{conv_uuid}.json"
    )
    saved = json.loads(saved_path.read_text())
    file_info = saved["chat_messages"][0]["files"][0]
    assert "local_document" in file_info
    # local_document is an absolute local filesystem path, so it uses the
    # platform separator. Compare the file name, not a POSIX suffix.
    assert Path(file_info["local_document"]).name == "document.pdf"


# ----------------------------------------------------------------------
# /api/attachments route
# ----------------------------------------------------------------------


@pytest.fixture
def attachments_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the backend at tmp_path/conversations for data and tmp_path/files
    for cached attachments — mirrors the production layout where both dirs
    sit under ~/.claude-explorer/.
    """
    data_dir = tmp_path / "conversations"
    data_dir.mkdir()
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield files_dir
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_api_attachments_serves_local_pdf(attachments_env: Path) -> None:
    """GET /api/attachments/<conv>/<file>/document returns the cached PDF
    bytes (no upstream call)."""
    files_dir = attachments_env
    conv_uuid = "conv-aaaa"
    file_uuid = "file-bbbb"
    cached_dir = files_dir / conv_uuid / file_uuid
    cached_dir.mkdir(parents=True)
    pdf_bytes = b"%PDF-1.4\nlocal cached\n%%EOF"
    (cached_dir / "document.pdf").write_bytes(pdf_bytes)

    from backend.main import app

    client = TestClient(app)
    resp = client.get(f"/api/attachments/{conv_uuid}/{file_uuid}/document")
    assert resp.status_code == 200, resp.text
    assert resp.content == pdf_bytes
    assert resp.headers["content-type"].startswith("application/pdf")


def test_api_attachments_serves_local_txt(attachments_env: Path) -> None:
    files_dir = attachments_env
    conv_uuid = "conv-aaaa"
    file_uuid = "file-cccc"
    cached_dir = files_dir / conv_uuid / file_uuid
    cached_dir.mkdir(parents=True)
    body = b"hello world\n"
    (cached_dir / "document.txt").write_bytes(body)

    from backend.main import app

    client = TestClient(app)
    resp = client.get(f"/api/attachments/{conv_uuid}/{file_uuid}/document")
    assert resp.status_code == 200
    assert resp.content == body
    # Some platforms map .txt to text/plain; just require text/*.
    assert resp.headers["content-type"].startswith("text/")


def test_api_attachments_404_when_no_cache(attachments_env: Path) -> None:
    """Missing cache returns 404; we do NOT refetch from claude.ai on demand."""
    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/attachments/missing-conv/missing-file/document")
    assert resp.status_code == 404


def test_api_attachments_serves_thumbnail_variant(attachments_env: Path) -> None:
    """The route must also serve thumbnail/preview/original variants from
    the same cache dir (one route covers all four variants)."""
    files_dir = attachments_env
    conv_uuid = "conv-d"
    file_uuid = "file-d"
    cached_dir = files_dir / conv_uuid / file_uuid
    cached_dir.mkdir(parents=True)
    body = b"\xff\xd8\xff\xe0FAKEJPEG"
    (cached_dir / "thumbnail.jpg").write_bytes(body)

    from backend.main import app

    client = TestClient(app)
    resp = client.get(f"/api/attachments/{conv_uuid}/{file_uuid}/thumbnail")
    assert resp.status_code == 200
    assert resp.content == body
    assert resp.headers["content-type"].startswith("image/")


def test_api_attachments_rejects_unknown_variant(attachments_env: Path) -> None:
    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/attachments/c/f/evil")
    # Either 400 or 404 is acceptable (allow-list rejection); the key
    # behavior is that the request does NOT escape the variant set.
    assert resp.status_code in (400, 404)
