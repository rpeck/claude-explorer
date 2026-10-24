"""Issue #4 — Markdown bundle export.

The plain Markdown export embeds image refs as ``![alt](/api/...)``
URLs that only resolve while the local Claude Explorer backend is
running. To send a conversation to a colleague, the user wants a
self-contained bundle: a zip with ``conversation.md`` plus an
``images/`` directory of the referenced bytes, with the Markdown
rewritten to point at relative paths.

This test covers two image sources that ship with the bundle:

  - Inline base64 image content blocks (Claude Code shape:
    ``{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}``)
  - ``[Image: source: <abs-path>]`` text markers (Claude Code Pattern B)
    that point at real bytes under ``~/.claude/image-cache/``.

Two Markdown dialects are supported:

  - ``commonmark`` (default): standard ``![alt](images/x.png)``
  - ``obsidian``: ``![[images/x.png]]`` wikilink

Desktop ``Message.files[]`` previews live behind an authenticated
proxy and aren't bundled (out of scope this round). They appear in
the bundle's Markdown as a footnote-style "(image not bundled)"
note.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

# 1x1 transparent PNG bytes.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAA"
    "YAAjCB0C8AAAAASUVORK5CYII="
)
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)


def _write_cc_image_cache(claude_dir: Path, session_uuid: str, name: str) -> Path:
    """Drop a real image file under ~/.claude/image-cache/<session>/."""
    image_dir = claude_dir / "image-cache" / session_uuid
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / name
    path.write_bytes(TINY_PNG_BYTES)
    return path


def _write_cc_conversation(
    claude_dir: Path,
    session_uuid: str,
    *,
    inline_image_b64: str,
    marker_path: Path,
) -> str:
    """Write a Claude Code JSONL with one human + one assistant message
    that together exercise the inline image AND the [Image: source: ...]
    marker codepath. Return the conversation UUID."""
    project_dir = claude_dir / "projects" / "-tmp-bundle-test"
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = project_dir / f"{session_uuid}.jsonl"

    # Claude Code's on-disk schema: each line is a top-level JSON object
    # with a `type` field. We use the minimum shape the reader expects.
    # (See backend/claude_code_reader.py for the parser.)
    lines = [
        json.dumps({
            "type": "summary",
            "summary": "Bundle test conversation",
            "leafUuid": "msg-2",
        }),
        json.dumps({
            "uuid": "msg-1",
            "type": "user",
            "sessionId": session_uuid,
            "timestamp": "2026-04-01T10:00:00.000Z",
            "cwd": "/tmp",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Look at this:\n[Image: source: {marker_path}]"},
                ],
            },
        }),
        json.dumps({
            "uuid": "msg-2",
            "type": "assistant",
            "parentUuid": "msg-1",
            "sessionId": session_uuid,
            "timestamp": "2026-04-01T10:01:00.000Z",
            "cwd": "/tmp",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [
                    {"type": "text", "text": "Here's a base64 image in response."},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": inline_image_b64,
                        },
                    },
                ],
            },
        }),
    ]
    jsonl_path.write_text("\n".join(lines) + "\n")
    return session_uuid


def _open_bundle(client, conv_uuid: str, dialect: str = "commonmark") -> tuple[zipfile.ZipFile, bytes]:
    """Hit /export/markdown-bundle and parse the response as a zip."""
    response = client.get(
        f"/api/conversations/{conv_uuid}/export/markdown-bundle",
        params={"dialect": dialect, "include_tools": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.content
    return zipfile.ZipFile(io.BytesIO(body)), body


def test_markdown_bundle_zips_inline_image_and_marker_image(monkeypatch, tmp_path):
    """Bundle contains conversation.md + images/, with both CC image
    sources rewritten to relative paths and bytes present.
    """
    # Stand up isolated CLAUDE_DIR + CLAUDE_EXPLORER_DATA_DIR.
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))

    # Reset settings + cache between tests so monkeypatch envs apply.
    from backend import cache
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    cache.clear_cache()

    # Drop the on-disk marker image under ~/.claude/image-cache/.
    session_uuid = "00000000-0000-0000-0000-0000000000bd"
    marker_path = _write_cc_image_cache(claude_dir, session_uuid, "marker.png")
    _write_cc_conversation(
        claude_dir,
        session_uuid,
        inline_image_b64=TINY_PNG_B64,
        marker_path=marker_path,
    )

    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    zf, _body = _open_bundle(client, session_uuid, dialect="commonmark")
    names = zf.namelist()

    # Must contain conversation.md and at least one image under images/.
    assert "conversation.md" in names, names
    image_names = [n for n in names if n.startswith("images/")]
    assert len(image_names) >= 2, image_names  # one inline, one marker

    md = zf.read("conversation.md").decode("utf-8")

    # CommonMark: relative refs.
    for img in image_names:
        # Each image must be referenced from the Markdown via its
        # relative path.
        assert f"]({img})" in md, f"missing reference to {img} in:\n{md}"

    # No leftover absolute API URLs in the bundled .md (they only
    # resolve while the local backend is running).
    assert "/api/" not in md, f"bundled markdown still references API URLs:\n{md}"

    # Bytes round-trip OK.
    for img in image_names:
        data = zf.read(img)
        assert data == TINY_PNG_BYTES


def test_markdown_bundle_obsidian_dialect_uses_wikilinks(monkeypatch, tmp_path):
    """Obsidian dialect emits ``![[images/x.png]]`` instead of
    ``![alt](images/x.png)``."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import cache
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    cache.clear_cache()

    session_uuid = "00000000-0000-0000-0000-0000000000be"
    marker_path = _write_cc_image_cache(claude_dir, session_uuid, "marker.png")
    _write_cc_conversation(
        claude_dir,
        session_uuid,
        inline_image_b64=TINY_PNG_B64,
        marker_path=marker_path,
    )

    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    zf, _ = _open_bundle(client, session_uuid, dialect="obsidian")
    md = zf.read("conversation.md").decode("utf-8")

    image_names = [n for n in zf.namelist() if n.startswith("images/")]
    assert len(image_names) >= 2

    # Obsidian: at least one wikilink ![[images/...]].
    assert "![[images/" in md, f"expected obsidian wikilinks, got:\n{md}"
    # And no CommonMark-style ref to the same images.
    for img in image_names:
        assert f"]({img})" not in md, f"unexpected commonmark ref:\n{md}"


def test_markdown_bundle_unknown_dialect_rejects_with_422(monkeypatch, tmp_path):
    """Bogus dialect string must 422, not silently default."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import cache
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    cache.clear_cache()

    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    response = client.get(
        "/api/conversations/anything/export/markdown-bundle",
        params={"dialect": "macdown-flavor"},
    )
    assert response.status_code == 422
