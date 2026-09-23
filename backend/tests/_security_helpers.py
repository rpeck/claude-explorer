"""Shared helpers for backend security-adjacent tests.

These helpers exist to keep the security tests
(``test_cc_image.py``, ``test_attachments.py``) tightly focused on the
contract under test rather than re-deriving fs layout and negative-space
assertions on every call site.

Per TESTING.md §5.9, security-adjacent tests assert REJECTION
plus NO LEAK — never "200 with sanitized content". The helpers below
encode that discipline:

* :func:`assert_no_leak_bytes` — negative-space assertion that a magic
  byte sequence does NOT appear in a response body. Use a unique magic
  string (e.g. ``b"SUPER_SECRET_LEAK_TEST_BYTES"``) rather than a path
  fragment, since 4xx responses typically echo the rejected path back
  in ``detail`` (which would otherwise spuriously trip a path-substring
  assertion).
* :func:`make_image_cache_dir` — materialize the
  ``<claude_dir>/image-cache/<session>/`` tree the cc-image route walks.
* :func:`attachments_root_for` — mirror ``files.py:_attachments_root``
  so the test's ground truth matches the production resolution rule
  (``conversations`` sibling vs ``files`` subdir fallback).
* :func:`make_attachment_dir` — materialize a per-conv/per-file/document
  tree so the attachments handler's ``file_dir.is_dir()`` and ``glob``
  paths execute against real disk.

Per TESTING.md §1, these helpers do NOT consult the handler
implementation while writing the tests; they encode the *spec* surface
(allowlists, fs layout, response shape).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# A deliberately-unusual byte sequence that will NOT appear in any
# legitimate 4xx error envelope echoing the rejected path back to the
# client. Use this as the body of "secret" files in negative-space
# leak tests so a path-fragment match in ``detail`` cannot cause a
# spurious pass.
LEAK_MAGIC = b"SUPER_SECRET_LEAK_TEST_BYTES_X7Q9"


def assert_no_leak_bytes(resp: Any, magic: bytes = LEAK_MAGIC, *, msg: str = "") -> None:
    """Assert that ``magic`` does NOT appear in ``resp.content``.

    Negative-space assertion per TESTING.md §5.9: a path-traversal
    attempt MUST be rejected with no body leak — not merely "200 with
    sanitized content".

    Args:
        resp: any object exposing ``.content`` as bytes (e.g. an
            ``httpx.Response`` from ``TestClient``).
        magic: the byte sequence that the secret file was written with.
            Defaults to :data:`LEAK_MAGIC`.
        msg: optional context appended to the assertion failure.
    """

    suffix = f" ({msg})" if msg else ""
    assert magic not in resp.content, (
        f"leaked secret bytes in response body{suffix}"
    )


def make_image_cache_dir(claude_dir: Path, session: str = "test-session") -> Path:
    """Create ``<claude_dir>/image-cache/<session>/`` and return it.

    Mirrors the production layout the cc-image route validates against
    (``files.py:_image_cache_root`` returns
    ``settings.claude_dir / "image-cache"``).
    """

    p = claude_dir / "image-cache" / session
    p.mkdir(parents=True, exist_ok=True)
    return p


def attachments_root_for(data_dir: Path) -> Path:
    """Compute the attachments root the way ``files.py:_attachments_root`` does.

    Production layout: ``data_dir`` is named ``conversations`` and the
    attachments live at ``data_dir.parent / "files"``. The fallback for
    tests with arbitrary data-dir names is ``data_dir / "files"``.

    Tests that touch the attachments contract should use this helper
    rather than hard-coding either branch — the production resolution
    rule is part of the contract, and divergence between the test and
    impl would silently mask wrongness.
    """

    if data_dir.name == "conversations":
        return data_dir.parent / "files"
    return data_dir / "files"


def make_attachment_dir(data_dir: Path, conv_uuid: str, file_uuid: str) -> Path:
    """Materialize ``<attachments_root>/<conv_uuid>/<file_uuid>/`` and return it.

    The attachments handler's contract is "serve cached bytes from this
    tree, 404 when nothing's cached". For traversal tests, we usually
    want EITHER:

    * an empty conv/file tree so legitimate paths 404 cleanly, OR
    * a populated tree under a *different* conv/file tuple so an escape
      that lands in the wrong tuple returns 404 (not the tuple's bytes).

    This helper handles only the materialization; callers write secret
    bytes themselves with the appropriate magic per
    :func:`assert_no_leak_bytes`.
    """

    p = attachments_root_for(data_dir) / conv_uuid / file_uuid
    p.mkdir(parents=True, exist_ok=True)
    return p
