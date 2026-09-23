"""Security-adjacent tests for ``GET /api/cc-image``.

Targets ``backend/routers/files.py:168-239``. The route serves Claude
Code's local image-cache; the security contract is "serve only files
under ``<claude_dir>/image-cache``, only with allow-listed image
extensions, never leak bytes from elsewhere on disk".

Spec-driven discipline (TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * ``PLANS/2026.05.07-frontend-api-contract.md`` (CCIMG clauses)
      * ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` (P2.5)
      * ``backend/routers/files.py:168-239`` (under test)
      * ``backend/tests/conftest.py`` (``isolated_data_dir``)
      * ``backend/tests/_security_helpers.py`` (this PR)

The existing ``test_files_proxy.py`` covers happy-path + the
"path-outside-cache" case via an *absolute* secret path. This file
covers the harder cases: explicit ``..``-traversal, symlink escape,
URL-encoded slash canonicalization, and a strong negative-space
"no leaked bytes" assertion on every traversal attempt.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.tests._security_helpers import (
    LEAK_MAGIC,
    assert_no_leak_bytes,
    make_image_cache_dir,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cc_image_env(isolated_data_dir: Path, tmp_path: Path) -> tuple[TestClient, Path]:
    """TestClient + image-cache dir, fully isolated under ``tmp_path``.

    ``isolated_data_dir`` pins ``CLAUDE_DIR`` to ``<tmp_path>/claude`` and
    clears the ``get_settings`` lru_cache. The image-cache dir lives at
    ``<claude_dir>/image-cache/test-session``.

    Returns ``(client, cache_dir)`` so each test can drop files in
    ``cache_dir`` without recomputing the layout.
    """

    from backend.main import app

    claude_dir = isolated_data_dir.parent / "claude"
    cache_dir = make_image_cache_dir(claude_dir, "test-session")
    return TestClient(app), cache_dir


def _write_secret_outside_cache(tmp_path: Path) -> Path:
    """Plant a secret PNG outside the image-cache dir.

    Uses :data:`LEAK_MAGIC` as the body so an ``assert_no_leak_bytes``
    check on the response can't be spuriously satisfied by the rejected
    path appearing in the ``detail`` field.
    """

    secret = tmp_path / "secret.png"
    secret.write_bytes(LEAK_MAGIC + b"\x00restofpngbytes")
    return secret


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test__get_cc_image__path_traversal_dotdot__returns_403_no_leak(
    cc_image_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """CCIMG-403-OUTSIDE + CCIMG-NEG-LEAK.

    A path that lexically traverses out of the cache via ``..`` MUST be
    rejected with 403 and the response MUST NOT contain the target
    file's bytes.
    """

    client, cache_dir = cc_image_env
    secret = _write_secret_outside_cache(tmp_path)
    # Construct a path that starts in the cache and ``..``s out of it.
    # ``Path.resolve(strict=False)`` will canonicalize this to the
    # secret's true location (which is outside the cache root) — the
    # lexical ``relative_to(root)`` check then raises ``ValueError`` and
    # the route 403s.
    traversal = cache_dir / ".." / ".." / secret.name
    assert ".." in str(traversal)  # sanity: confirms the test exercises ..

    resp = client.get("/api/cc-image", params={"path": str(traversal)})

    assert resp.status_code == 403, (
        f"expected 403 for traversal; got {resp.status_code}: {resp.text}"
    )
    assert "detail" in resp.json()
    # Negative-space: even if the route 403s, the body must not echo
    # the secret file's bytes.
    assert_no_leak_bytes(resp, msg="cc-image dotdot traversal")


def test__get_cc_image__symlink_to_outside__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """CCIMG-SYMLINK + CCIMG-NEG-LEAK.

    A symlink LIVING under the cache dir but pointing OUT of it must be
    refused. ``Path.resolve()`` follows symlinks, so the canonicalized
    target is outside the cache root → 403/404 with no leaked bytes.
    """

    client, cache_dir = cc_image_env
    secret = _write_secret_outside_cache(tmp_path)

    sym = cache_dir / "sym.png"
    try:
        os.symlink(secret, sym)
    except (OSError, NotImplementedError) as e:  # pragma: no cover — Windows
        pytest.skip(f"symlink unsupported on this platform: {e}")

    resp = client.get("/api/cc-image", params={"path": str(sym)})

    # The contract is REJECT (403 or 404) — the route's specific error
    # branch may resolve differently for symlinked-outside vs purely
    # lexical traversal, but the security contract is identical.
    assert resp.status_code in (403, 404), (
        f"expected 403/404 for symlink-out; got {resp.status_code}: {resp.text}"
    )
    assert_no_leak_bytes(resp, msg="cc-image symlink-out")


def test__get_cc_image__non_image_extension__returns_400(
    cc_image_env: tuple[TestClient, Path],
) -> None:
    """CCIMG-400-EXT.

    A file LEGITIMATELY under the cache but with a non-allowlisted
    suffix must 400 — defense in depth against the route serving
    arbitrary text files even when the path-validation check passes.
    """

    client, cache_dir = cc_image_env
    txt = cache_dir / "leaked.txt"
    txt.write_bytes(LEAK_MAGIC + b"\nsensitive text content")

    resp = client.get("/api/cc-image", params={"path": str(txt)})

    assert resp.status_code == 400, (
        f"expected 400 for non-image ext; got {resp.status_code}: {resp.text}"
    )
    assert "extension" in resp.json()["detail"].lower()
    # Body should not contain the file's contents (contract: refuse,
    # don't read).
    assert_no_leak_bytes(resp, msg="cc-image bad-extension")


def test__get_cc_image__url_encoded_slash__canonicalized_to_403(
    cc_image_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """CCIMG-CANONICAL + CCIMG-403-OUTSIDE.

    A ``%2F``-encoded slash must canonicalize via ``Path.resolve()`` to
    the same rejection as a raw ``/``. Documents the FastAPI/Starlette
    behavior: ``Query(...)`` URL-decodes once before the handler sees
    ``path``, so by the time ``Path()`` is constructed the bytes are
    identical for ``%2F`` and ``/``. The test pins this so a future
    change in decoding would surface as a regression.
    """

    client, cache_dir = cc_image_env
    secret = _write_secret_outside_cache(tmp_path)

    # Pass the path with a %2F-encoded slash explicitly via the URL.
    # httpx will NOT re-encode an already-encoded sequence, so the wire
    # bytes contain ``%2F``; FastAPI/Starlette decode it to ``/`` before
    # the handler reads ``path``.
    encoded = str(secret).replace("/", "%2F")
    # We hit the URL directly rather than using ``params=`` to ensure
    # the encoding survives intact through the test pipeline.
    url = f"/api/cc-image?path={encoded}"
    resp = client.get(url)

    assert resp.status_code == 403, (
        f"expected 403 for %2F-encoded outside path; got {resp.status_code}: "
        f"{resp.text}"
    )
    assert_no_leak_bytes(resp, msg="cc-image %2F-encoded")
