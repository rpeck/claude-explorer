"""Image-attachment proxy.

Claude Desktop conversations carry image attachments as `Message.files[]`
entries with claude.ai-relative URLs like
``/api/<org_uuid>/files/<file_uuid>/{thumbnail,preview}``. The browser
can't reach those directly — they require the captured ``sessionKey``
cookie that lives in ``~/.claude-explorer/credentials.json``.

This router proxies those URLs back through claude.ai using the same
``curl_cffi`` + cookie pattern as ``fetcher/bulk_fetch.py``. The local
backend acts as a same-origin shim so:

  - ``MessageAttachments`` <img> tags ``src="/api/<org>/files/<file>/thumbnail"``
    work without CORS gymnastics.
  - The Markdown export's image refs render in any Markdown viewer that
    can reach localhost:8765.
  - The PDF export's WeasyPrint pass can fetch the bytes through the
    same URL.

The proxy is a no-op when credentials are missing — returns 503 with a
hint to run ``claude-explorer capture`` rather than failing silently.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..config import files_dir_for, get_settings

from fetcher.credentials import (
    DEFAULT_CREDENTIALS_PATH,
    CredentialsCorruptError,
    load_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Lazy imports inside handlers so the backend still boots when curl_cffi
# isn't installed (e.g. fixture-mode CI on a backend-only build).


def _build_upstream_url(org_id: str, file_uuid: str, variant: str) -> str:
    return f"https://claude.ai/api/{org_id}/files/{file_uuid}/{variant}"


def _load_session_cookies() -> dict[str, str]:
    """Load sessionKey + Cloudflare cookies from disk, raise 503 on miss."""
    try:
        creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Image proxy unavailable: no Claude Desktop credentials on disk. "
                "Run `claude-explorer capture` (or click Refresh in the sidebar) "
                "to log in."
            ),
        ) from exc
    except CredentialsCorruptError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Image proxy unavailable: credentials file is corrupt ({exc}).",
        ) from exc

    cookies: dict[str, str] = {"sessionKey": creds["session_key"]}
    cf_bm = creds.get("cf_bm")
    if cf_bm:
        cookies["__cf_bm"] = cf_bm
    cf_clearance = creds.get("cf_clearance")
    if cf_clearance:
        cookies["cf_clearance"] = cf_clearance
    return cookies


def _serve_from_local_cache(file_uuid: str, variant: str) -> FileResponse | None:
    """Return the bulk-fetcher-cached attachment for (file_uuid, variant), or None.

    Shared by the 404 branch ("claude.ai GC'd the file") AND the
    network-failure branch ("we're offline from claude.ai") of ``_proxy``.
    The bulk fetcher writes
    ``<attachments_root>/<conv>/<file_uuid>/<variant>.<ext>`` at fetch
    time; this lookup is the read side of that contract.

    Hunt #4 (API boundaries audit): defense-in-depth glob-meta reject.
    ``Path.glob()`` interprets ``*``, ``?``, ``[``, ``]`` in the pattern,
    and ``**`` as recursive descent. With ``file_uuid="**"`` a request
    would return the FIRST matching file from any subdir — real
    attachment bytes from an unrelated file_uuid, at 200 OK. h11 /
    Starlette routing doesn't filter these characters from path segments.
    Return None on any metachar so the caller falls through to its
    standard error (404 or 502, depending on which branch invoked us).
    """
    if any(ch in file_uuid for ch in "*?[]"):
        return None
    try:
        cached = [
            m for m in _attachments_root().glob(f"*/{file_uuid}/{variant}.*")
            if m.is_file()  # exclude any stray directory matching the pattern
        ]
    except OSError:
        return None
    if not cached:
        return None
    media_type = mimetypes.guess_type(str(cached[0]))[0] or "application/octet-stream"
    return FileResponse(cached[0], media_type=media_type, headers={
        "cache-control": "public, max-age=86400, stale-while-revalidate=604800",
    })


def _proxy(org_id: str, file_uuid: str, variant: str) -> Response:
    """Fetch the image bytes from claude.ai and stream them back."""
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
    except ImportError as exc:  # pragma: no cover — curl_cffi is a hard dep in production
        raise HTTPException(
            status_code=500,
            detail="curl_cffi is required for image proxying but isn't installed.",
        ) from exc

    cookies = _load_session_cookies()
    url = _build_upstream_url(org_id, file_uuid, variant)

    try:
        upstream = curl_requests.get(url, cookies=cookies, impersonate="chrome", timeout=15)
    except Exception as exc:
        # Offline / DNS timeout / claude.ai unreachable. Try the local
        # cache before surfacing 502 — the bulk fetcher writes
        # thumbnails/previews/originals to disk at fetch time, so a
        # user who has fetched the conversation while online can still
        # see attachment images after the network drops. Mirrors the
        # 404-branch fallback below and the /api/cc-image fallback at
        # lines ~270+.
        served = _serve_from_local_cache(file_uuid, variant)
        if served is not None:
            logger.info(
                "proxy_local_fallback",
                extra={
                    "file_uuid": file_uuid,
                    "variant": variant,
                    "reason": "upstream_unreachable",
                },
            )
            return served
        logger.warning("image proxy upstream fetch failed for %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {exc}") from exc

    if upstream.status_code == 404:
        # claude.ai garbage-collects file storage over time. Before
        # surfacing the 404 (and breaking Markdown / PDF exporters that
        # hit this same URL — see module docstring lines 15-18), look
        # for a local copy the bulk fetcher cached at
        # <attachments_root>/<conv>/<file>/<variant>.<ext>.
        served = _serve_from_local_cache(file_uuid, variant)
        if served is not None:
            logger.info(
                "proxy_local_fallback",
                extra={
                    "file_uuid": file_uuid,
                    "variant": variant,
                    "reason": "upstream_404",
                },
            )
            return served
        raise HTTPException(
            status_code=404,
            detail="image not found upstream and no local cache",
        )
    if upstream.status_code in (401, 403):
        raise HTTPException(
            status_code=upstream.status_code,
            detail=(
                "Claude Desktop session expired. Re-run `claude-explorer capture` "
                "(or click Refresh in the sidebar) to refresh credentials."
            ),
        )
    if upstream.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"upstream returned HTTP {upstream.status_code}",
        )

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    # Cache aggressively in the browser — these assets are immutable
    # (file_uuid is content-addressed). Stale-while-revalidate gives us a
    # quick recovery if the upstream URL ever changes.
    headers = {
        "cache-control": "public, max-age=86400, stale-while-revalidate=604800",
    }
    return Response(content=upstream.content, media_type=content_type, headers=headers)


# Register both legacy ``files`` and (any future) ``files_v2`` paths in
# case the upstream URL shape evolves; for now both variants are
# rendered in the UI as the same path shape.
@router.get(
    "/{org_id}/files/{file_uuid}/thumbnail",
    response_class=StreamingResponse,
    summary="Proxy a Claude Desktop file thumbnail via the captured session",
    responses={200: {"content": {"image/*": {}}}},
)
def get_thumbnail(org_id: str, file_uuid: str) -> Response:
    return _proxy(org_id, file_uuid, "thumbnail")


@router.get(
    "/{org_id}/files/{file_uuid}/preview",
    response_class=StreamingResponse,
    summary="Proxy a Claude Desktop file preview via the captured session",
    responses={200: {"content": {"image/*": {}}}},
)
def get_preview(org_id: str, file_uuid: str) -> Response:
    return _proxy(org_id, file_uuid, "preview")


# ----------------------------------------------------------------------
# Claude Code image-cache serving
#
# Claude Code stores image attachments on disk at
# ``~/.claude/image-cache/<session-uuid>/<N>.<ext>`` and references them
# inside the message text as a literal ``[Image: source: <abs-path>]``
# marker. The frontend strips the marker, encodes the absolute path into
# a query string, and renders ``<img src="/api/cc-image?path=...">``.
# This route validates the path is under the user's image-cache dir and
# serves the bytes — no proxying to claude.ai required, the bytes are
# already local.
# ----------------------------------------------------------------------


def _image_cache_root() -> Path:
    """Where Claude Code stores image-cache files. Honors the same
    CLAUDE_DIR override the rest of the backend uses."""
    return get_settings().claude_dir / "image-cache"


_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


@router.get(
    "/cc-image",
    summary="Serve a Claude Code cached image from disk (with permanent-cache fallback)",
    responses={200: {"content": {"image/*": {}}}},
)
def get_cc_image(path: str = Query(..., description="Absolute path under ~/.claude/image-cache/")) -> FileResponse:
    """Serve a Claude Code cached image from disk.

    Path must be an absolute path inside the configured image-cache
    root, and must end in a known image extension. If the original file
    has been rotated away by Claude Code, fall back to the permanent
    cache populated at fetch time (P4b).
    """
    # Resolve lexically so we can still validate the path against the
    # cache root + extension allow-list when the original is gone.
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except (OSError, ValueError) as exc:
        # CWE-754: Path.resolve() raises ValueError("embedded null
        # character in path") on null-byte injection (foo%00.png), in
        # addition to OSError for ELOOP / ENAMETOOLONG / etc. All of
        # these are malformed-input conditions per RFC 7231 §6.5.1,
        # not server faults, so unify under a 400 with a static detail
        # (CWE-200: don't leak the raw exception text).
        logger.warning("path resolve failed for %r: %s", path, exc)
        raise HTTPException(status_code=400, detail="invalid path") from exc

    root = _image_cache_root().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        # CWE-200: don't echo the resolved root path back to the
        # client — that leaks the server's on-disk layout. Log the
        # real values so operators can still debug from server logs.
        logger.info(
            "cc-image refused (outside root): candidate=%s root=%s", candidate, root
        )
        raise HTTPException(
            status_code=403,
            detail="refused: path is outside the cc-image cache root",
        ) from exc

    if candidate.suffix.lower() not in _ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"refused: extension {candidate.suffix!r} is not an allowed image type",
        )

    media_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
    cache_headers = {
        "cache-control": "public, max-age=86400, stale-while-revalidate=604800",
    }

    if candidate.is_file():
        # Lazy-populate the permanent cache (P4b "Option B"): if the live
        # file exists but no cache copy does, copy it now. Means the
        # cache fills as the user views images, not just at fetch time.
        # For CC sessions the conversation UUID equals the session UUID
        # (= parent dir name), which matches what cache_all_markers
        # writes during the eager fetch-time path. Best-effort: any
        # error is logged and swallowed so it can never fail the
        # request.
        try:
            from ..cc_image_cache import copy_marker_image_to_cache

            copy_marker_image_to_cache(str(candidate), candidate.parent.name)
        except Exception:  # noqa: BLE001
            logger.exception("lazy cc-image cache copy failed for %s", candidate)
        # CWE-732: bail out before FileResponse opens the file so a
        # 0o000-mode (or otherwise unreadable) cache entry surfaces as a
        # static 403 instead of a 500 leak. There is a small TOCTOU
        # window between this check and FileResponse.open(), acceptable
        # for a local single-user app.
        if not os.access(candidate, os.R_OK):
            logger.warning("readable check failed: %s", candidate)
            raise HTTPException(status_code=403, detail="file not readable")
        return FileResponse(candidate, media_type=media_type, headers=cache_headers)

    # P4b: original is gone — try the permanent cache. The cached
    # filename embeds <sess>--<N>.<sha8>.<ext>, so look up by parent
    # dir name (sess) + stem (N), and glob for any sha8 variant in any
    # conv-uuid subdir. Multiple candidates (re-fetch with different
    # bytes) → pick the newest mtime.
    from ..cc_image_cache import cache_dir

    sess = candidate.parent.name
    n = candidate.stem
    ext = candidate.suffix.lstrip(".") or "png"
    cache_root = cache_dir()
    if cache_root.exists():
        candidates = list(cache_root.glob(f"*/{sess}--{n}.*.{ext}"))
        if candidates:
            best = max(candidates, key=lambda x: x.stat().st_mtime)
            # CWE-732: same readable check as the live-file branch above.
            if not os.access(best, os.R_OK):
                logger.warning("readable check failed: %s", best)
                raise HTTPException(status_code=403, detail="file not readable")
            return FileResponse(best, media_type=media_type, headers=cache_headers)

    # CWE-200: don't echo the resolved candidate path back to the
    # client — that leaks the server's on-disk layout. Log it
    # server-side for operator diagnostics.
    logger.info("cc-image not found: %s", candidate)
    raise HTTPException(status_code=404, detail="image not found")


# ----------------------------------------------------------------------
# P4c: cached-attachment server.
#
# fetcher.bulk_fetch.download_conversation_files writes every Message.files[]
# attachment (image variants + non-image documents) under
#   ~/.claude-explorer/files/<conv-uuid>/<file-uuid>/<variant><ext>
# where variant ∈ {thumbnail, preview, original, document}.
#
# The frontend / exporter wants a stable URL it can drop into <img src=> or
# <a href=> without juggling absolute on-disk paths or refetching from
# claude.ai. This route serves those bytes verbatim, 404s when the file
# isn't cached (no on-demand refetch — the bulk fetch owns that), and
# enforces an allow-list on the variant segment so a malicious URL can't
# escape into ../../etc/passwd territory.
# ----------------------------------------------------------------------

_ALLOWED_ATTACHMENT_VARIANTS = frozenset({"thumbnail", "preview", "original", "document"})


def _attachments_root() -> Path:
    """Directory that holds per-conversation/per-file cached bytes.

    Production layout puts ``conversations/`` and ``files/`` as siblings
    under ``~/.claude-explorer/``. We derive ``files/`` from
    ``settings.data_dir`` (which points at the ``conversations/`` subdir
    in production and is overridden by ``CLAUDE_EXPLORER_DATA_DIR`` — or
    the legacy ``CLAUDE_EXPORTER_DATA_DIR`` — in tests). When the
    override points at a directory whose name is NOT ``conversations``,
    we fall back to ``data_dir / "files"`` so older test layouts still
    work.
    """
    return files_dir_for(get_settings().data_dir)


@router.get(
    "/attachments/{conv_uuid}/{file_uuid}/{variant}",
    summary="Serve a cached attachment (thumbnail/preview/original/document) from disk",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def get_attachment(conv_uuid: str, file_uuid: str, variant: str) -> FileResponse:
    """Serve a cached attachment from the local files directory.

    Returns 400 for unknown variant, 404 if the file isn't on disk
    (callers should re-run a fetch to populate the cache).
    """
    if variant not in _ALLOWED_ATTACHMENT_VARIANTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown variant {variant!r}; allowed: {sorted(_ALLOWED_ATTACHMENT_VARIANTS)}",
        )

    file_dir = _attachments_root() / conv_uuid / file_uuid
    # Defense in depth: reject any conv_uuid/file_uuid combination that
    # escapes the attachments root via ``..`` segments OR Python's
    # ``Path("a") / "/b" == Path("/b")`` absolute-injection semantics.
    # The downstream ``chosen.resolve().relative_to(file_dir.resolve())``
    # only validates the FINAL chosen file against ``file_dir`` — it
    # does NOT validate that ``file_dir`` itself is inside the
    # attachments root, so a traversal attack would return a 200 with
    # an arbitrary on-disk file (provided the path resolved to a real
    # directory containing a ``<variant>.*`` match). See
    # ``backend/tests/test_attachments.py`` for the regression cases.
    try:
        file_dir.resolve().relative_to(_attachments_root().resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not file_dir.is_dir():
        raise HTTPException(status_code=404, detail="attachment not cached")

    # The bulk fetcher writes <variant><ext>, where <ext> depends on
    # Content-Type / filename. Glob to find the actual file regardless
    # of extension. Reject results outside file_dir defensively.
    matches = sorted(file_dir.glob(f"{variant}.*"))
    # Allow extensionless writes too, e.g. when guess_extension returned "".
    if not matches:
        bare = file_dir / variant
        if bare.is_file():
            matches = [bare]
    if not matches:
        raise HTTPException(status_code=404, detail="attachment not cached")

    chosen = matches[0]
    try:
        chosen.resolve().relative_to(file_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="path traversal refused") from exc

    media_type = mimetypes.guess_type(str(chosen))[0] or "application/octet-stream"
    cache_headers = {
        "cache-control": "public, max-age=86400, stale-while-revalidate=604800",
    }
    # CWE-732: see cc-image branch above for TOCTOU rationale. Pre-check
    # readability so a 0o000-mode cached attachment surfaces as a static
    # 403 instead of a 500 leaking the absolute on-disk path.
    if not os.access(chosen, os.R_OK):
        logger.warning("readable check failed: %s", chosen)
        raise HTTPException(status_code=403, detail="file not readable")
    return FileResponse(chosen, media_type=media_type, headers=cache_headers)
