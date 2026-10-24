"""
Read Claude Code conversations directly from local JSONL files.

Claude Code (CLI and Desktop Code tab) stores conversations locally at:
    ~/.claude/projects/{project-path-encoded}/{session-uuid}.jsonl

This module reads those JSONL files on-the-fly without copying them.
Features:
- orjson for fast JSON parsing (3-10x faster than stdlib)
- Memory cache with mtime-based invalidation
- Parallel file reading with ThreadPoolExecutor
"""

import logging
import os
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from .cache import (
    get_conversation_cache,
)

# Agent-session discovery + metadata (refactor B5, 2026-05-18). Moved
# to backend.cc_agent_reader; the facade re-exports them so existing
# callers do not need to update their imports.
from .cc_agent_reader import (  # noqa: F401
    _extract_agent_metadata,
    build_agent_index_with_summaries,
    discover_agent_files,
    read_agent_summary,
    read_agent_summary_fast,
)

# Compact-marker extraction (refactor B5, 2026-05-18): moved out of this
# file into a dedicated module. Re-exported here so existing
# ``from backend.claude_code_reader import extract_compact_markers``
# imports keep working without source changes downstream.
from .cc_image_markers import (  # noqa: F401
    _COMPACT_ARGS_RE,
    _COMPACT_COMMAND_NAME,
    _COMPACT_LOOKAHEAD,
    extract_compact_markers,
)

# Raw JSONL I/O (refactor B5, 2026-05-18). The streaming summary
# reader + path-discovery + LOGIC_VERSION live in ``cc_jsonl_io``; the
# facade re-exports them so external imports like
# ``from backend.claude_code_reader import discover_jsonl_files`` and
# ``from backend.claude_code_reader import LOGIC_VERSION`` keep
# resolving to the same callables.
from .cc_jsonl_io import (  # noqa: F401
    DEFAULT_CLAUDE_DIR,
    LOGIC_VERSION,
    discover_jsonl_files,
    parse_jsonl_file,
    read_conversation_summary_fast,
)

# Re-export the message-pipeline transforms from their dedicated module
# (refactor B5, 2026-05-18). Pre-refactor these all lived in this file;
# they were extracted into ``cc_message_transforms`` to drop the facade
# below the 300-line cap. Callers and tests keep their existing
# ``from backend.claude_code_reader import _LOCAL_CMD_ARGS_RE`` shape via
# this re-export.
from .cc_message_transforms import (  # noqa: F401
    _CANNED_NO_RESPONSE_TEXT,
    _LOCAL_CMD_ARGS_RE,
    _LOCAL_CMD_NAME_RE,
    _TITLE_FIELD_BY_TYPE,
    _collapse_local_command_triplets,
    _convert_entry_to_message,
    _extract_conversation_metadata,
    _extract_local_command_name,
    _extract_local_command_name_and_args,
    _extract_title_from_message,
    _flag_leading_prelude_markers,
    _fold_canned_assistant_responses_into_marker,
    _get_message_key,
    _get_message_text,
    _is_canned_no_response_assistant,
    _is_system_message,
    _local_command_kind,
    _merge_entries_to_message,
    _title_from_entry,
)
from .parsing import parse_datetime as _parse_datetime  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)


# Threshold above which we pay the process-pool spawn overhead for the
# first-install / cold-cache case. Below this, the sequential path is
# faster than either thread or process pool (no pool overhead).
# Empirically tuned: at ~50 misses the process-pool spawn cost (~150ms
# on macOS) starts being amortized; at 1,000 misses it's the difference
# between 1.8s (process pool) and 5.6s (sequential).
_PROCESS_POOL_THRESHOLD = 50
#: Paths per process-pool map() call: 8 workers x chunksize 20. The stop
#: flag is checked between calls.
_STOP_CHECK_CHUNK = 160


def _read_summaries_parallel(
    paths: list[Path], stop: "threading.Event | None" = None,
) -> dict[Path, dict[str, Any] | None]:
    """Run :func:`read_conversation_summary_fast` across paths concurrently.

    Returns a dict keyed by the input ``Path``; the value is either the
    metadata dict the fast reader returned, or ``None`` for files that
    were empty / unreadable (the caller should skip those rather than
    cache them).

    Concurrency strategy (empirically tuned, NOT the original
    "threads + GIL-releasing orjson" plan):

      * 0 paths → empty dict (no pool spinup).
      * 1 to ``_PROCESS_POOL_THRESHOLD`` paths → sequential. ProcessPool
        spawn overhead (~150ms on macOS) dominates over the work below
        this threshold.
      * Above the threshold → ``ProcessPoolExecutor`` with 8 workers.
        The pure-Python ``for line in f / entry.get(...)`` cycle inside
        ``read_conversation_summary_fast`` is GIL-bound (orjson.loads
        is only ~46% of cumulative time per a cProfile run), so
        threads actually run SLOWER than sequential on 970 files
        (8.94s vs 5.62s) due to GIL contention. Processes sidestep
        the GIL entirely (970 files in 1.81s, ~3x faster than
        sequential, ~5x faster than threads).
      * Process-pool failure (sandboxed Python, fork restrictions,
        ImportError on the child side, etc.) → fall back to a
        ThreadPoolExecutor pass. Worst case the cold-install
        benchmark gets slow; warm-path latency is unaffected because
        the warm path doesn't hit this function at all.

    ``stop`` (2026-10-23): the lifespan's shutdown flag. The paths are read
    in chunks, and the flag is checked between chunks, so a Quit during a
    first fill waits for one chunk, not for every summary. The chunks
    that finished are returned.
    """
    if not paths or (stop is not None and stop.is_set()):
        return {}

    if len(paths) < _PROCESS_POOL_THRESHOLD:
        out_seq: dict[Path, dict[str, Any] | None] = {}
        for p in paths:
            if stop is not None and stop.is_set():
                break
            out_seq[p] = read_conversation_summary_fast(p)
        return out_seq

    # ProcessPoolExecutor.map preserves input order, which we don't
    # strictly need (we key by Path), but it also chunks more
    # efficiently than submit-per-task. chunksize=20 keeps the
    # per-process work meaningful without starving the pool.
    try:
        done: dict[Path, dict[str, Any] | None] = {}
        with ProcessPoolExecutor(max_workers=8) as executor:
            for start in range(0, len(paths), _STOP_CHECK_CHUNK):
                chunk = paths[start:start + _STOP_CHECK_CHUNK]
                done.update(zip(chunk, executor.map(
                    read_conversation_summary_fast, chunk, chunksize=20,
                )))
                if stop is not None and stop.is_set():
                    break
        return done
    except Exception:
        # ProcessPoolExecutor failure modes are platform-specific
        # (sandboxed Python without fork, frozen executable that
        # can't re-import the module, etc.) and rare. Fall back to
        # threads so we still return SOMETHING; the cold-install
        # benchmark suffers but no warm requests are affected.
        logger.warning(
            "claude_code_reader: ProcessPoolExecutor unavailable; "
            "falling back to ThreadPoolExecutor (cold-cache requests "
            "will be slower than designed)",
            exc_info=True,
        )

    out: dict[Path, dict[str, Any] | None] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_path = {
            executor.submit(read_conversation_summary_fast, p): p
            for p in paths
        }
        for future in as_completed(future_to_path):
            if stop is not None and stop.is_set():
                for f in future_to_path:
                    f.cancel()
                break
            path = future_to_path[future]
            try:
                out[path] = future.result()
            except Exception:
                logger.exception(
                    "claude_code_reader: parallel summary read failed for %s",
                    path,
                )
                out[path] = None
    return out


def read_claude_code_conversation(jsonl_path: Path) -> dict[str, Any] | None:
    """Read a single Claude Code conversation from a JSONL file.

    Handles Claude Code's streaming format where multiple entries represent
    chunks of the same message. Groups entries by message ID and merges them.
    """
    entries = parse_jsonl_file(jsonl_path)
    if not entries:
        return None

    metadata = _extract_conversation_metadata(entries, jsonl_path)

    # Group entries by message key (handles streaming chunks)
    from collections import OrderedDict
    message_groups: OrderedDict[str, list[dict]] = OrderedDict()

    for entry in entries:
        key = _get_message_key(entry)
        if key:
            if key not in message_groups:
                message_groups[key] = []
            message_groups[key].append(entry)

    # Build mapping from any entry UUID to the merged message's UUID
    # Include ALL entries (user, assistant, system, progress, etc.)
    # Non-message entries map to the most recent message's UUID
    uuid_remap: dict[str, str] = {}
    last_message_uuid: str | None = None

    for entry in entries:
        entry_uuid = entry.get("uuid", "")
        if not entry_uuid:
            continue

        key = _get_message_key(entry)
        if key:
            # This is a user/assistant entry - find its merged UUID
            group = message_groups.get(key, [])
            if group:
                merged_uuid = group[0].get("uuid", "")
                uuid_remap[entry_uuid] = merged_uuid
                last_message_uuid = merged_uuid
        else:
            # Non-message entry (system, progress, etc.) - map to last message
            if last_message_uuid:
                uuid_remap[entry_uuid] = last_message_uuid

    # Merge each group into a single message
    messages = []
    for group_entries in message_groups.values():
        msg = _merge_entries_to_message(group_entries)
        if msg:
            # Remap parent_message_uuid to point to merged message UUID
            parent = msg.get("parent_message_uuid")
            if parent and parent in uuid_remap:
                msg["parent_message_uuid"] = uuid_remap[parent]
            messages.append(msg)

    # V1 polish (2026-05-12): collapse <local-command-caveat>/<command-name>/
    # <local-command-stdout> triplets that CC emits around slash commands
    # (e.g. /exit, /clear) into a single short "Session: /foo" marker. Done
    # AFTER streaming-chunk merge so the boilerplate-vs-real-message
    # classification operates on whole logical messages — not interleaved
    # chunks. See `_collapse_local_command_triplets` docstring for the
    # full contract + bidirectional guarantees.
    messages = _collapse_local_command_triplets(messages)
    # V1 polish (2026-05-12, council round 2): absorb CC's canned
    # `"No response requested."` assistant reply into the preceding marker,
    # then flag the leading run of markers as `is_prelude` so the frontend
    # can hide them behind a "Session prelude: N earlier /exit runs (show)"
    # affordance. Prelude markers stay in `chat_messages` with a flag — no
    # silent erasure. See module-level comments above for the full rationale.
    messages = _fold_canned_assistant_responses_into_marker(messages)
    messages, prelude_hidden_count = _flag_leading_prelude_markers(messages)

    result = {
        "uuid": metadata["uuid"],
        "name": metadata["name"],
        "summary": metadata["summary"],
        "model": metadata["model"],
        "created_at": metadata["created_at"].isoformat(),
        "updated_at": metadata["updated_at"].isoformat(),
        "is_starred": False,
        "project_path": metadata["cwd"],
        "git_branch": metadata["git_branch"],
        "claude_code_version": metadata["version"],
        "source": "CLAUDE_CODE",
        "chat_messages": messages,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "compact_markers": extract_compact_markers(entries),
        "prelude_hidden_count": prelude_hidden_count,
    }

    # P4a-fix (2026-05-06): populate ~/.claude-explorer/cc-images/ as a
    # side effect of reading. The live read path is here, so the cache
    # directory is created lazily on first conversation render.
    # Failures are logged and swallowed so a transient I/O error never
    # breaks the conversation render. (Historical note: the original
    # wiring lived in a never-shipped ``fetcher/local_claude_code.py``
    # importer, deleted 2026-05-21.)
    try:
        from .cc_image_cache import cache_all_markers

        cache_all_markers(result)
    except Exception:
        logger.exception("cache_all_markers failed for %s", jsonl_path)

    return result


def _load_conversation_cached(jsonl_path: Path) -> dict[str, Any] | None:
    """Load a full conversation with caching."""
    cache = get_conversation_cache()
    return cache.get_or_load(jsonl_path, read_claude_code_conversation)


def list_claude_code_conversations(
    claude_dir: Path = DEFAULT_CLAUDE_DIR,
    full_content: bool = False,
    include_phantom: bool = False,
) -> list[dict[str, Any]]:
    """List all Claude Code conversations from local JSONL files, including subagents.

    Args:
        claude_dir: Path to Claude config directory
        full_content: If True, read full conversation content (for search).
                     If False, only read metadata (fast, for listing).
        include_phantom: If True, include phantom sessions (local command artifacts).
                        Default False to hide these empty sessions.

    Features:
    - Uses orjson for ~5x faster JSON parsing
    - Caches parsed conversations with mtime-based invalidation
    - Parallel file reading when loading full content
    """
    # Build agent index once upfront - reads each agent file once and extracts summaries
    agent_index = build_agent_index_with_summaries(claude_dir)

    # Collect all file paths
    jsonl_paths = list(discover_jsonl_files(claude_dir))

    if full_content:
        # Use cache + parallel loading for full content
        cache = get_conversation_cache()
        conversations_raw = cache.load_many_parallel(
            jsonl_paths,
            read_claude_code_conversation,
        )
    else:
        # Metadata branch — read-through SQLite cache backed by
        # backend.summary_cache. The fast reader still opens every line
        # of every JSONL on a miss, but on a warm cache we only re-read
        # the small subset of files whose mtime/size has drifted since
        # the last request. orjson releases the GIL during decode so
        # the parallel-miss path scales with disk IO.
        from .summary_cache import get_summary_cache

        summary_cache = get_summary_cache()
        if summary_cache is None:
            # FTS5 missing or SQLite open failed — fall back to the
            # legacy sequential path. Same fallback discipline as
            # backend.search → linear-scan when the FTS5 index is
            # unavailable.
            conversations_raw = [
                read_conversation_summary_fast(p) for p in jsonl_paths
            ]
        else:
            # Pre-stat all paths once so both the hit and miss branches
            # share a single os.stat per file. Missing/unreadable paths
            # drop out here — read_conversation_summary_fast handles
            # the same case by returning None, which the filter below
            # already skips.
            stat_index: dict[Path, os.stat_result] = {}
            for p in jsonl_paths:
                try:
                    stat_index[p] = os.stat(p)
                except OSError:
                    continue

            # ``cached`` may map a path to None — that's a NEGATIVE
            # cache hit (the producer previously returned None for
            # this file and the file hasn't changed since). Treat
            # the presence of the key, not the value, as "hit".
            cached = summary_cache.get_many(jsonl_paths, stat_index)
            misses = [p for p in jsonl_paths if p not in cached]
            fresh = _read_summaries_parallel(misses)
            # Best-effort upsert. A SQLite write failure here just
            # means the next request takes the slow path again; it
            # must NOT block the response. We pass ``fresh`` as-is
            # so None entries get persisted as negative-cache rows.
            summary_cache.upsert_many(fresh, stat_index)

            # Preserve the original order so downstream sort/filter
            # behaves identically to the pre-cache path. None values
            # (from either negative cache hit or fresh None read)
            # propagate through; the downstream ``if conv:`` filter
            # drops them.
            conversations_raw = []
            for p in jsonl_paths:
                if p in cached:
                    conversations_raw.append(cached[p])
                elif p in fresh:
                    conversations_raw.append(fresh[p])
                else:
                    # stat failed earlier — preserve None so the
                    # downstream filter (``if conv:``) drops it.
                    conversations_raw.append(None)

    # Attach subagents to each conversation
    conversations = []
    for conv in conversations_raw:
        if conv:
            # Filter out phantom sessions unless explicitly requested
            if not include_phantom and conv.get("is_phantom", False):
                continue

            session_id = conv["uuid"]

            # Look up agent summaries from pre-built index (no additional file I/O)
            subagents = agent_index.get(session_id, [])

            # Sort subagents by created_at
            subagents = sorted(subagents, key=lambda a: a["created_at"])

            # Convert datetimes to ISO strings for JSON serialization
            for agent in subagents:
                if isinstance(agent["created_at"], datetime):
                    agent["created_at"] = agent["created_at"].isoformat()
                if isinstance(agent["updated_at"], datetime):
                    agent["updated_at"] = agent["updated_at"].isoformat()

            conv["subagents"] = subagents
            conversations.append(conv)

    # Since 2026-10-21 each file's uuid is its file name, so this collapse
    # no longer merges anything; it stays as a guard against duplicate
    # uuids. History: CC `--resume` writes the same internal sessionId across multiple
    # JSONL files (original + continuation, distinct stems). Both files
    # produce conv dicts with the SAME `uuid` (sessionId-derived), so
    # downstream consumers see duplicates: the sidebar renders two rows
    # for one logical session, and `context_size='full'` search emits
    # duplicate React keys (the slow path walks every conv dict). The
    # FTS5 fast path is already last-writer-wins per conv_uuid at INSERT
    # time, which masked this until Full mode was exercised.
    #
    # Collapse to one conv per uuid, keeping the LATEST `updated_at`
    # (string compare is correct: timestamps are ISO-8601 with timezone
    # offset and lexicographically order the same as chronologically).
    # This matches CC's own UI behavior — when you `/resume` a session,
    # the merged view shows the most-recent activity timestamp.
    #
    # Tie-breaker (same updated_at): keep the one with more messages.
    # Final tie-breaker (still equal): preserve first-seen order so the
    # function is deterministic across runs.
    return _dedup_resume_continuations(conversations)


def _dedup_resume_continuations(
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse same-uuid conv dicts produced by CC `--resume` flows.

    See call site in :func:`list_claude_code_conversations` for the full
    rationale. Pure function over a list of conv dicts — separated out so
    `backend/tests/test_cc_resume_dedup.py` can pin behavior without
    needing a filesystem fixture for every variant.
    """
    by_uuid: dict[str, dict[str, Any]] = {}
    order: list[str] = []  # first-seen order, preserved across deletes
    for conv in conversations:
        uuid = conv.get("uuid")
        if not uuid:
            # Skip malformed entries entirely rather than collapse under
            # `None` (which would silently drop all of them).
            continue
        prev = by_uuid.get(uuid)
        if prev is None:
            by_uuid[uuid] = conv
            order.append(uuid)
            continue
        if _conv_supersedes(conv, prev):
            by_uuid[uuid] = conv
        # else: keep prev — first-seen wins when tied AND prev is newer.
    return [by_uuid[u] for u in order]


def _conv_supersedes(new: dict[str, Any], prev: dict[str, Any]) -> bool:
    """Return True if `new` should replace `prev` under the dedup rule.

    Priority order:
      1. Strictly later `updated_at` (lex compare on ISO-8601 strings).
      2. Same `updated_at` AND more chat_messages.
    Anything else: prev wins (first-seen stability).
    """
    n_ts = new.get("updated_at") or ""
    p_ts = prev.get("updated_at") or ""
    if n_ts > p_ts:
        return True
    if n_ts == p_ts:
        n_msgs = len(new.get("chat_messages") or [])
        p_msgs = len(prev.get("chat_messages") or [])
        return n_msgs > p_msgs
    return False