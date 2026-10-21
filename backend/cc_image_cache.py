"""Permanent cache for Claude Code image-cache attachments (P4a).

Claude Code stores image attachments at
``~/.claude/image-cache/<sess>/<N>.<ext>`` and references them inside
message text as a literal ``[Image: source: <abs-path>]`` marker.
Claude Code rotates / deletes those files; the explorer's viewer then
breaks. To keep them around forever, at fetch time we copy each
referenced file into:

    ``<data_dir>/cc-images/<conv-uuid>/<sess>--<N>.<sha8>.<ext>``

The ``sha8`` suffix prevents collisions if a re-fetch produces
different bytes for the same ``<sess>--<N>`` slot. We do **not** delete
old copies — both survive on disk; the fallback endpoint (P4b) resolves
the conversation marker to the most recent.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Callable

from .config import get_settings


log = logging.getLogger(__name__)


_MARKER_RE = re.compile(r"\[Image: source: ([^\]]+)\]")


def cache_dir() -> Path:
    """Root of the permanent CC image cache.

    Production layout puts ``conversations/`` and ``cc-images/`` as
    siblings under ``~/.claude-explorer/``. We derive ``cc-images/``
    from ``settings.data_dir`` (which points at the ``conversations/``
    subdir in production and is overridden by ``CLAUDE_EXPLORER_DATA_DIR``
    — or the legacy ``CLAUDE_EXPORTER_DATA_DIR`` — in tests). When the
    override points at a directory whose name is NOT ``conversations``,
    we fall back to ``data_dir / "cc-images"`` so older test layouts
    still work. Mirrors the ``backend.routers.files._attachments_root``
    precedent.
    """
    data_dir = get_settings().data_dir
    if data_dir.name == "conversations":
        return data_dir.parent / "cc-images"
    return data_dir / "cc-images"


def cache_path_for(
    conv_uuid: str, sess: str, n: str, sha8: str, ext: str = "png"
) -> Path:
    """Compute the destination path for a given (conv, sess, slot, sha8)."""
    return cache_dir() / conv_uuid / f"{sess}--{n}.{sha8}.{ext}"


def copy_marker_image_to_cache(
    abs_path: str,
    conv_uuid: str,
    *,
    _seen_missing: set[str] | None = None,
) -> Path | None:
    """Read bytes from ``abs_path``, hash, copy into permanent cache.

    Returns the destination path, or ``None`` if ``abs_path`` is missing
    or unreadable. Missing paths fall into two buckets:

    * **Recoverable** — the bytes are already in the permanent cache under
      ``<cache_dir>/<conv_uuid>/<sess>--<N>.*.<ext>``. Claude Code rotated
      the live file but the explorer's earlier eager/lazy/watcher pass
      already copied it. This is the *normal* steady state and is logged
      at DEBUG only.
    * **Permanent data loss** — missing in both the live cache *and* the
      permanent cache. The log level depends on whether the supervised
      CC watcher is installed (see :mod:`backend.watcher_status`):

      * Watcher installed → INFO. The loss is historical; future losses
        are prevented. Shouting at WARNING forever would be log noise.
      * Watcher missing → WARNING. The loss is ongoing; the user must
        run ``claude-explorer install-watcher`` to stop the bleeding.

    The optional ``_seen_missing`` set deduplicates per-walk: when a
    conversation references the same missing slot from multiple
    messages (very common — a single screenshot often gets pasted into
    several follow-up turns), :func:`cache_all_markers` passes a fresh
    set so each unique ``abs_path`` produces at most one log record per
    walk.

    Never raises on missing live files.
    """
    p = Path(abs_path)
    if not p.exists() or not p.is_file():
        # Check the permanent cache before warning. Glob is scoped to the
        # exact <cache_dir>/<conv_uuid>/ directory — O(files-in-conv-dir),
        # not O(all-cached-files). Junk paths (parent_dir or stem that
        # don't look like a CC sess/slot) safely produce an empty glob.
        sess = p.parent.name
        n = p.stem
        ext = p.suffix.lstrip(".") or "png"
        target_dir = cache_dir() / conv_uuid
        if target_dir.exists() and any(target_dir.glob(f"{sess}--{n}.*.{ext}")):
            log.debug(
                "CC image %s (conv %s) live-cache missing but permanent-cache "
                "copy found; treating as recovered",
                abs_path,
                conv_uuid,
            )
            return None
        if _seen_missing is not None:
            if abs_path in _seen_missing:
                return None
            _seen_missing.add(abs_path)
        # Import here (not at module top) to avoid a circular import via
        # main → cc_image_cache → watcher_status → ... if watcher_status
        # ever grows a dependency on a backend module that itself imports
        # cc_image_cache. The lookup is cached so per-call cost is nil.
        from .watcher_status import is_watcher_installed
        level = logging.INFO if is_watcher_installed() else logging.WARNING
        log.log(
            level,
            "CC image referenced by conv %s not on disk: %s",
            conv_uuid,
            abs_path,
        )
        return None
    try:
        bytes_ = p.read_bytes()
    except OSError as e:
        log.warning(
            "Could not read CC image %s for conv %s: %s", abs_path, conv_uuid, e
        )
        return None

    sha8 = hashlib.sha256(bytes_).hexdigest()[:8]
    # Parse "<sess>" and "<N>" from the path: parent dir name is the
    # session uuid, stem is the slot number.
    sess = p.parent.name
    n = p.stem
    ext = p.suffix.lstrip(".") or "png"

    dst = cache_path_for(conv_uuid, sess, n, sha8, ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        dst.write_bytes(bytes_)
    return dst


def cache_all_markers(conversation_json: dict) -> list[Path]:
    """Walk a conversation JSON for ``[Image: source: ...]`` markers
    and copy each referenced file to the permanent cache.

    Returns the list of destination paths actually written (or
    pre-existing). Skipped/missing references are logged at WARNING
    and do not appear in the returned list.
    """
    conv_uuid = conversation_json.get("uuid")
    if not conv_uuid:
        return []

    out: list[Path] = []
    # Per-walk dedupe set so a conversation that references the same
    # missing image from N messages emits at most one permanent-loss
    # log record per unique abs_path. Fresh set per walk — see
    # `copy_marker_image_to_cache` and the bidirectional pair in
    # `test_cc_image_warning_dedupe.py::test_dedupe_state_does_not_leak_between_walks`.
    seen_missing: set[str] = set()
    for msg in conversation_json.get("chat_messages", []):
        for block in msg.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text") or ""
            for m in _MARKER_RE.finditer(text):
                dst = copy_marker_image_to_cache(
                    m.group(1).strip(),
                    conv_uuid,
                    _seen_missing=seen_missing,
                )
                if dst is not None:
                    out.append(dst)
    return out


def warm_all_sessions(
    *,
    limit: int | None = None,
    progress: Callable[[dict], None] | None = None,
    stop: threading.Event | None = None,
) -> dict:
    """Walk every Claude Code session JSONL on disk and copy referenced
    image-cache files into the permanent cache.

    Idempotent: ``copy_marker_image_to_cache`` skips files already in
    cache (sha8 collision check), so re-running this is safe and
    cheap. Sessions whose markers point at already-rotated files are
    silently skipped (the bytes are gone, nothing to do).

    Used by:
      * the FastAPI lifespan startup hook (auto-warms in the
        background — see ``backend/main.py``), so the user never has
        to remember to run a CLI.
      * the ``claude-explorer warm-cc-cache`` CLI as a manual
        override / one-shot.

    Args:
        limit: Optional cap on number of sessions to walk.
        progress: Optional callback invoked with a dict of running
            counters at every batch checkpoint (every 50 sessions
            and at completion). Keys: ``sessions_walked``,
            ``sessions_with_markers``, ``files_cached``,
            ``sessions_failed``, ``total_sessions``.
        stop: Optional cooperative-cancel flag. The lifespan sets it on
            server shutdown; the walk checks it before each session and
            returns early, so a long walk does not keep the process
            alive after Quit.

    Returns:
        Dict with the same keys as the progress callback's last call.
    """
    from .claude_code_reader import (
        discover_jsonl_files,
        read_claude_code_conversation,
    )

    # Use the live settings, not the import-time DEFAULT_CLAUDE_DIR
    # constant — tests + CLAUDE_DIR overrides require this.
    claude_dir = get_settings().claude_dir
    sessions = list(discover_jsonl_files(claude_dir))
    if limit is not None:
        sessions = sessions[:limit]
    total_sessions = len(sessions)

    state = {
        "sessions_walked": 0,
        "sessions_with_markers": 0,
        "files_cached": 0,
        "sessions_failed": 0,
        "total_sessions": total_sessions,
    }

    for i, jsonl_path in enumerate(sessions, start=1):
        if stop is not None and stop.is_set():
            log.info(
                "warm_all_sessions: stopped at shutdown after %d of %d session(s)",
                i - 1, total_sessions,
            )
            break
        state["sessions_walked"] = i
        try:
            data = read_claude_code_conversation(jsonl_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("warm_all_sessions: read FAILED for %s: %s", jsonl_path.name, exc)
            state["sessions_failed"] += 1
            continue
        if not data:
            continue
        # read_claude_code_conversation already calls cache_all_markers,
        # but the in-memory cache may have served a stale result. Call
        # it again here directly to guarantee the warm-cache pass runs
        # for every session this command was asked to process.
        written = cache_all_markers(data)
        if written:
            state["sessions_with_markers"] += 1
            state["files_cached"] += len(written)
        if progress is not None and (i % 50 == 0 or i == total_sessions):
            progress(dict(state))

    return state


async def warm_all_sessions_async(
    *, limit: int | None = None, stop: threading.Event | None = None
) -> dict:
    """Async wrapper for :func:`warm_all_sessions` that runs the
    blocking work in a thread so it doesn't stall the event loop.

    Used by the FastAPI lifespan startup hook. Logs a summary at
    completion (INFO) so dashboard tails can confirm the warm pass
    ran. Errors at the per-session level are already swallowed by the
    sync function; any whole-pass failure here is logged at WARNING
    and absorbed (a partial cache is still better than crashing the
    backend).
    """
    try:
        state = await asyncio.to_thread(warm_all_sessions, limit=limit, stop=stop)
    except Exception as exc:  # noqa: BLE001
        log.warning("warm_all_sessions_async aborted: %s", exc)
        return {"error": str(exc)}
    log.info(
        "CC warm pass complete: %d session(s) walked, %d with markers, "
        "%d files cached, %d failed.",
        state["sessions_walked"],
        state["sessions_with_markers"],
        state["files_cached"],
        state["sessions_failed"],
    )
    return state
