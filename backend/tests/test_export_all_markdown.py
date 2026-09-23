"""Tests for ``/api/export/all/markdown`` empty-corpus behavior (C6 (c)).

PLANS/2026.05.18-test-hardening.md C6(c): the empty-corpus case
(fresh install, before first fetch) should return a valid zip — not
a 404. The user clicking "Export all conversations" on a fresh
install would otherwise see a confusing error message; a zip
containing a README explains the state and removes the surprise.

The route previously raised ``HTTPException(404, "No conversations
found")`` when the corpus was empty; this test pins the new contract
where the zip is always well-formed.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_export_all_markdown_empty_corpus_returns_valid_zip(
    isolated_data_dir: Path,
) -> None:
    """Empty corpus → 200 + valid (non-empty-by-content) zip.

    Uses ``isolated_data_dir`` so we never touch the developer's real
    conversation directory. After the fixture runs, the data dir
    exists but contains zero conversation files — exactly the
    fresh-install state.

    Contract pinned:
      * Status: 200 (not 404).
      * Content-Type: ``application/zip``.
      * Body is a well-formed zip per stdlib ``zipfile.ZipFile``
        (no ``BadZipFile`` raised).
      * The zip contains AT LEAST one entry — empty zips render as
        "0 items" in file managers and look broken; a README.md
        explaining the state is the friendly minimum.
      * The README content mentions "no conversations" so the user
        understands why the export is otherwise empty.
    """
    # Import lazily so the autouse env-var fixtures from conftest are
    # applied before the app is instantiated and any module-level
    # state (settings, store) is resolved against them.
    from backend.main import app

    with TestClient(app) as client:
        # Sanity check: no conversation files exist. The lifespan's
        # v2 migration drops a ``.migration_log.json`` under by-org/
        # before any conversations land, so we look for the inner
        # ``{uuid}.json`` files that ``store.list_conversations``
        # actually walks (under ``by-org/<org>/<uuid>.json``).
        # If a future fixture change seeds a real conv, the test
        # would test the wrong thing — fail loudly here.
        conv_files = [
            p for p in isolated_data_dir.rglob("*.json")
            if not p.name.startswith(".")
        ]
        assert not conv_files, (
            f"isolated_data_dir should contain no conversation files but "
            f"contains: {conv_files}"
        )

        r = client.get("/api/export/all/markdown")

    assert r.status_code == 200, (
        f"empty-corpus export should return 200; got {r.status_code}: "
        f"{r.text[:200]}"
    )
    assert r.headers["content-type"] == "application/zip", (
        f"unexpected content-type: {r.headers.get('content-type')!r}"
    )

    body = r.content
    assert body, "response body is empty; expected a zip"

    # Bidirectional verification (TESTING.md §5): use stdlib's
    # zipfile to parse the bytes — if the route returns 44-byte EOCD-
    # only output, this fails with BadZipFile (or namelist is empty).
    # We assert namelist is non-empty AND that the README content is
    # present, so a route refactor that drops the README would be
    # caught.
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = zf.namelist()
        assert names, (
            "empty-corpus zip contains no entries; file managers render "
            "this as 0 items and the user can't tell if the export "
            "succeeded — include a README.md"
        )
        # The README is the canonical explainer. Any file path
        # containing "README" (case-insensitive) satisfies the
        # contract — we don't pin the exact name so a future
        # rename (e.g. "README-empty.md") doesn't break the test.
        readme_names = [n for n in names if "readme" in n.lower()]
        assert readme_names, (
            f"empty-corpus zip should contain a README; got entries: {names}"
        )
        readme_bytes = zf.read(readme_names[0])
        readme_text = readme_bytes.decode("utf-8").lower()
        assert "no conversations" in readme_text, (
            "README content should mention 'no conversations' so the user "
            f"understands the empty export; got: {readme_text[:200]!r}"
        )
