"""Store module for reading conversation JSON files from disk."""

import json
import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

from .config import get_settings
from .parsing import parse_datetime as _parse_datetime  # noqa: F401  (re-export; backend.search imports _parse_datetime from this module)
from .claude_code_reader import (
    list_claude_code_conversations,
    # Re-exported so existing tests that patch
    # ``backend.store.read_claude_code_conversation`` keep their
    # bind point (the fast-path C1 fix routes through
    # _load_conversation_cached, which calls this name via the
    # claude_code_reader module's own binding — but tests that
    # patch store.py-side as a belt-and-suspenders measure want
    # this symbol to exist).
    read_claude_code_conversation,  # noqa: F401  (re-export for tests)
    _load_conversation_cached,
    discover_jsonl_files,
)
from .models import (
    ConversationSummary,
    ConversationDetail,
    ConversationTree,
    Message,
    MessageNode,
    ContentBlock,
    SubagentSummary,
)


logger = logging.getLogger(__name__)


# W3+W4 (2026-05-23 council decision): module-level cache of the
# rendered ``model_dump(mode='json')`` output for ConversationDetail.
#
# Why a dict cache (not a Pydantic-object cache):
#   * On warm hits, the route returns ORJSONResponse(content=cached_dict)
#     which bypasses BOTH Pydantic rebuild (~186 ms per call on the
#     user's 16K-msg conv) AND FastAPI's default encoder (~70 ms).
#   * Caching the Pydantic object would still pay model_dump on every
#     hit — the dict shape is the actual perf target.
#
# Cache key: (uuid, mtime). When the underlying file's mtime changes,
# the next lookup is a miss and rebuilds.
#
# Mutability contract: the cached dict is SHARED across requests. Callers
# MUST treat it as immutable. The conversation route returns it directly
# to ORJSONResponse which serializes without mutation.
#
# LRU cap: 32 entries. Each entry is ~5-70 MB on disk worth of dict —
# at 32 entries the worst-case resident memory is ~2 GB on a heavy
# corpus. In practice users rotate through a handful of conversations
# per session; the LRU keeps the hot set warm.
#
# Thread safety: a threading.RLock guards the OrderedDict. The route
# layer is async but the cache lookup happens inside synchronous code.
#
# Invalidation: keyed by mtime, so a file write naturally invalidates.
# For explicit cache purges, use ``invalidate_detail_dict_cache(uuid)``
# or ``_DETAIL_DICT_CACHE.clear()``.
_DETAIL_DICT_CACHE_MAX = 32
_DETAIL_DICT_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_DETAIL_DICT_CACHE_LOCK = threading.RLock()


def invalidate_detail_dict_cache(uuid: str | None = None) -> None:
    """Drop one (or all) entries from the detail-dict cache.

    Call with ``uuid=None`` to clear the entire cache (used by tests
    and the CC watcher when it detects out-of-band file changes).
    """
    with _DETAIL_DICT_CACHE_LOCK:
        if uuid is None:
            _DETAIL_DICT_CACHE.clear()
        else:
            _DETAIL_DICT_CACHE.pop(uuid, None)


def _safe_int(raw: Any, *, default: int = 0, field: str = "?",
              uuid: str | None = None) -> int:
    """Coerce a raw JSON value to int, falling back to ``default`` on
    any non-numeric / wrong-shape input.

    Same documented-fallback pattern as :func:`backend.parsing.parse_datetime`
    and the inline guard in :func:`get_conversation` for
    ``prelude_hidden_count`` (commit 15c4fc5). Used by ``_make_summary``
    to defang corrupt-on-disk ``message_count`` / ``human_message_count``
    values that would otherwise propagate to ``ConversationSummary(int=...)``
    and 500 the entire sidebar via the ``list_conversations`` loop.

    Catches ValueError (non-numeric string), TypeError (list/dict/None),
    and also handles a quirk of ``dict.get(k, 0)``: when the on-disk JSON
    has ``"k": null``, ``.get`` returns ``None`` (not the default!), which
    Pydantic v2 rejects.
    """
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "Conversation %s has non-numeric %s %r; defaulting to %d",
            uuid, field, raw, default,
        )
        return default


# Top-level files that should be considered conversation JSONs.
# UUID-shaped names only — excludes _index.json, .migration_log.json, etc.
_UUID_FILENAME_RE = re.compile(r"^[0-9a-f-]{36}\.json$", re.IGNORECASE)
_MIGRATION_SENTINEL = "by-org/.migrated_v2"


def _extract_text(content: list[dict[str, Any]]) -> str:
    """Extract plain text from content blocks."""
    texts = []
    for block in content:
        if block.get("type") == "text" and block.get("text"):
            texts.append(block["text"])
    return "\n".join(texts)


def _parse_content_blocks(content: list[dict[str, Any]]) -> list[ContentBlock]:
    """Parse raw content blocks into ContentBlock models."""
    blocks = []
    for block in content:
        if isinstance(block, str):
            # Sometimes content is a plain string (e.g., tool_result)
            blocks.append(ContentBlock(type="text", text=block))
            continue

        block_type = block.get("type", "text")
        # Handle nested content - can be string or list
        nested_content = block.get("content")
        if nested_content and isinstance(nested_content, list):
            parsed_nested = _parse_content_blocks(nested_content)
        elif nested_content and isinstance(nested_content, str):
            parsed_nested = [ContentBlock(type="text", text=nested_content)]
        else:
            parsed_nested = None

        parsed = ContentBlock(
            type=block_type,
            text=block.get("text"),
            name=block.get("name"),
            input=block.get("input"),
            content=parsed_nested,
            source=block.get("source"),
            # Anthropic content-block linking IDs (preserved so the
            # MCP get_messages tool can surface them for call/result
            # pairing — positional adjacency isn't reliable for
            # parallel tool calls).
            id=block.get("id"),
            tool_use_id=block.get("tool_use_id"),
        )
        blocks.append(parsed)
    return blocks


# CC-only flags set by claude_code_reader's collapse/fold/prelude passes
# (V1 polish, 2026-05-12). Default values so Desktop messages round-trip
# unchanged.
#
# Plumbing-fragility note (V1 polish 2026-05-13): these flags are
# hand-forwarded from the raw dict into the Pydantic `Message` model.
# Earlier we lost an hour debugging `slash_command` because the forward
# line was omitted from `_parse_message`. To prevent recurrence, we keep
# the field list in ONE place (this constant) and forward via dict-
# comprehension below. The companion test
# `test_cc_only_passthrough_fields_matches_model_fields` introspects
# `Message.model_fields` and FAILS the moment anyone adds a new CC-only
# field to the model without updating this constant — so the next field
# can't silently fall through the floor.
_CC_ONLY_PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "is_command_marker",
    "is_prelude",
    "assistant_canned_response_consumed",
    "slash_command",
)
_CC_ONLY_DEFAULTS: dict[str, Any] = {
    "is_command_marker": False,
    "is_prelude": False,
    "assistant_canned_response_consumed": False,
    "slash_command": None,
}


def _parse_message(raw: dict[str, Any]) -> Message:
    """Parse a raw message dict into a Message model."""
    content = raw.get("content", [])
    cc_kwargs = {
        k: raw.get(k, _CC_ONLY_DEFAULTS[k]) for k in _CC_ONLY_PASSTHROUGH_FIELDS
    }
    return Message(
        uuid=raw.get("uuid", ""),
        sender=raw.get("sender", "human"),
        text=raw.get("text", "") or _extract_text(content),
        content=_parse_content_blocks(content),
        created_at=_parse_datetime(raw.get("created_at")),
        updated_at=_parse_datetime(raw.get("updated_at")),
        truncated=raw.get("truncated", False),
        parent_message_uuid=raw.get("parent_message_uuid"),
        attachments=raw.get("attachments", []),
        files=raw.get("files", []),
        files_v2=raw.get("files_v2", []),
        **cc_kwargs,
    )


def resolve_active_branch(
    messages: list[dict[str, Any]], leaf_uuid: str
) -> list[dict[str, Any]]:
    """Resolve the active branch by walking from leaf to root.

    Handles circular references in parent chain by tracking visited nodes.
    """
    by_uuid = {m["uuid"]: m for m in messages}
    branch = []
    visited: set[str] = set()
    current = by_uuid.get(leaf_uuid)
    while current:
        uuid = current["uuid"]
        if uuid in visited:
            # Circular reference detected - stop here
            break
        visited.add(uuid)
        branch.append(current)
        parent_uuid = current.get("parent_message_uuid")
        current = by_uuid.get(parent_uuid) if parent_uuid else None
    return list(reversed(branch))


def has_branches(messages: list[dict[str, Any]]) -> bool:
    """Check if the conversation has any branches (message with >1 child)."""
    child_count: dict[str | None, int] = {}
    for msg in messages:
        parent = msg.get("parent_message_uuid")
        child_count[parent] = child_count.get(parent, 0) + 1
    return any(count > 1 for count in child_count.values())


def build_message_tree(messages: list[dict[str, Any]]) -> list[MessageNode]:
    """Build the full message tree from flat message list.

    Uses iterative BFS approach to handle conversations with thousands of messages
    without hitting Python's recursion limit. Handles circular references safely.
    """
    if not messages:
        return []

    # Build parent->children map
    children_map: dict[str | None, list[str]] = {}
    msg_by_uuid: dict[str, dict[str, Any]] = {}
    # Set of UUIDs that are real messages in THIS conversation. A parent that
    # is not in this set marks a root: real claude.ai exports parent the first
    # message to a synthetic placeholder UUID
    # (00000000-0000-4000-8000-000000000000), not to None. Seeding the BFS only
    # from parent==None therefore left every real Desktop conversation with an
    # empty tree (the "View branches" modal rendered blank). See
    # test_conversations_tree.py::...placeholder_root...
    msg_uuids: set[str] = {msg["uuid"] for msg in messages}

    for msg in messages:
        uuid = msg["uuid"]
        parent = msg.get("parent_message_uuid")
        # Normalize any parent that doesn't resolve to a real message in this
        # conversation down to None so it seeds the root set. Covers three
        # cases: a self-referential link (parent == uuid, which would otherwise
        # build a MessageNode containing itself and raise
        # PydanticSerializationError: Circular reference detected), the
        # placeholder root, and a dangling/orphan parent.
        if parent == uuid or (parent is not None and parent not in msg_uuids):
            parent = None
        msg_by_uuid[uuid] = msg
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(uuid)

    # Track which nodes have been added to the tree to prevent cycles
    in_tree: set[str] = set()
    nodes: dict[str, MessageNode] = {}

    # BFS from root nodes
    root_uuids = children_map.get(None, [])
    queue: list[str] = list(root_uuids)
    root_nodes: list[MessageNode] = []

    while queue:
        uuid = queue.pop(0)
        if uuid in in_tree:
            # Skip - already processed (prevents cycles)
            continue
        if uuid not in msg_by_uuid:
            continue

        in_tree.add(uuid)

        # Create node
        node = MessageNode(
            message=_parse_message(msg_by_uuid[uuid]),
            children=[],
        )
        nodes[uuid] = node

        # If this is a root node, add to root list
        parent_uuid = msg_by_uuid[uuid].get("parent_message_uuid")
        if parent_uuid is None or parent_uuid == uuid or parent_uuid not in msg_uuids:
            # Root node. Three cases collapse to "no resolvable parent":
            #   * parent_uuid is None — a genuine root.
            #   * parent_uuid == uuid — self-loop guard. This can occur when a
            #     later raw record for the same UUID overwrites msg_by_uuid
            #     after children_map was already built, restoring a
            #     self-referential parent link the construction pass cleared.
            #     Treating it as a root avoids appending the node as its own
            #     child (a Python object cycle).
            #   * parent_uuid not in msg_uuids — the placeholder root, or a
            #     dangling parent that points outside this conversation.
            root_nodes.append(node)
        elif parent_uuid in nodes:
            # Add as child of parent
            nodes[parent_uuid].children.append(node)

        # Queue children for processing
        for child_uuid in children_map.get(uuid, []):
            if child_uuid not in in_tree:
                queue.append(child_uuid)

    return root_nodes


# ----- Cowork list cache -------------------------------------------------
#
# Why: the conversation list used to parse every Cowork session's whole
# audit.jsonl on every call, on the event loop: 231 ms of each list call on
# a 67 MB corpus, cold or warm (benchmarks/bench_cowork_list.py). Cloud
# Cowork tasks that Refresh archives add to the same cost. A list row needs
# only a summary, so keep one per session, keyed by the size and mtime of
# its audit.jsonl and its sidecar. Search and export (full_content=True)
# still read the full sessions.

#: Keys that a list row never uses; the messages are most of the size.
_COWORK_HEAVY_KEYS = ("chat_messages", "compact_markers")
_cowork_summary_cache: dict[Path, tuple[tuple, dict[str, Any] | None]] = {}
_cowork_summary_lock = threading.Lock()


def clear_cowork_summary_cache() -> None:
    """Forget every cached Cowork summary (tests)."""
    with _cowork_summary_lock:
        _cowork_summary_cache.clear()


def _cowork_files_key(session_dir: Path) -> tuple:
    """Size and mtime of the session's audit.jsonl and sidecar (None if absent).

    The sidecar path matches ``cowork_reader.read_cowork_conversation``.
    """
    key = []
    for path in (session_dir / "audit.jsonl", session_dir.with_suffix(".json")):
        try:
            st = path.stat()
        except OSError:
            key.append(None)
        else:
            key.append((st.st_size, st.st_mtime_ns))
    return tuple(key)


def _cowork_summary(data: dict[str, Any]) -> dict[str, Any]:
    """A Cowork conversation without its messages, plus the counts and the
    branch flag that ``_make_summary`` would have computed from them."""
    messages = data.get("chat_messages") or []
    summary = {k: v for k, v in data.items() if k not in _COWORK_HEAVY_KEYS}
    summary["message_count"] = len(messages)
    summary["human_message_count"] = sum(1 for m in messages if m.get("sender") == "human")
    summary["has_branches"] = has_branches(messages)
    return summary


class ConversationStore:
    """Store for reading conversation data from disk and Claude Code JSONL files."""

    def __init__(
        self,
        data_dir: Path | None = None,
        claude_dir: Path | None = None,
        cowork_root: Path | None = None,
    ):
        # All directories fall back through the constructor argument first,
        # then the global Settings (which reads CLAUDE_EXPLORER_DATA_DIR
        # — or legacy CLAUDE_EXPORTER_DATA_DIR — / CLAUDE_DIR /
        # CLAUDE_DESKTOP_APP_DIR / ~/.claude-explorer/config.json), and
        # finally the platform default. claude_dir matters for tests
        # that need a synthetic ~/.claude/projects/ tree on disk;
        # cowork_root similarly for the Cowork integration tests.
        self.data_dir = data_dir or get_settings().data_dir
        self.claude_dir = claude_dir or get_settings().claude_dir
        self.cowork_root = (
            cowork_root
            or get_settings().claude_desktop_app_dir / "local-agent-mode-sessions"
        )
        # Remote Cowork sessions (cloud tasks) are archived by the fetcher
        # alongside the Desktop JSONs. See ``fetcher.remote_sessions``.
        from .cowork_reader import remote_cowork_root
        self.remote_cowork_root = remote_cowork_root(self.data_dir)

    def _get_conversation_files(self) -> list[Path]:
        """Get all conversation JSON files.

        cowork-multi-org C3 layout:

        * Primary location: ``data_dir/by-org/<org_uuid>/<uuid>.json``
        * Legacy fallback: ``data_dir/<uuid>.json`` (top-level UUID-named
          files only — excludes ``_index.json`` etc.) is included when the
          ``by-org/.migrated_v2`` sentinel is absent. Once migration runs
          (commit C4) the sentinel appears and the legacy glob returns
          nothing.

        Dedup: when the same UUID appears in both layouts during the
        migration window, the ``by-org/`` copy wins (it carries org metadata
        the flat copy lacks). Without this dedup, the UI would render the
        same conversation twice during the brief window between C3
        landing and migration completing (NEW3-P0-A).
        """
        if not self.data_dir.exists():
            return []

        by_org = sorted((self.data_dir / "by-org").glob("*/*.json")) if (self.data_dir / "by-org").exists() else []

        sentinel = self.data_dir / _MIGRATION_SENTINEL
        if sentinel.exists():
            return by_org

        legacy = sorted(
            p for p in self.data_dir.glob("*.json")
            if _UUID_FILENAME_RE.match(p.name)
        )

        # Dedup by UUID — by-org wins.
        seen_uuids = {p.stem for p in by_org}
        legacy_dedup = [p for p in legacy if p.stem not in seen_uuids]
        return by_org + legacy_dedup

    def _load_conversation(self, path: Path) -> dict[str, Any] | None:
        """Load a conversation from a JSON file (with mtime-based cache).

        Issue #0 — full-text search latency.
        ConversationStore.search() calls get_all_conversations_raw() which
        re-reads + re-parses every Desktop JSON file on every request. With
        ~100+ conversations in ~/.claude-explorer/conversations, that adds
        up to noticeable latency per keystroke. The Claude Code path
        already used backend.cache.FileCache; reusing the same cache here
        gives Desktop the same hot-path speedup. Files are only re-read
        when their mtime changes (on next fetch), so the cache stays
        consistent without explicit invalidation.
        """
        from .cache import get_conversation_cache

        cache = get_conversation_cache()
        return cache.get_or_load(path, self._load_conversation_uncached)

    @staticmethod
    def _load_conversation_uncached(path: Path) -> dict[str, Any] | None:
        """Plain disk-backed loader, called by FileCache on cache miss."""
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _make_summary(self, data: dict[str, Any], include_subagents: bool = False) -> ConversationSummary:
        """Create a ConversationSummary from raw conversation data."""
        # `data.get(k, default)` returns `default` only when the key is
        # MISSING; an explicit `null` value reaches downstream consumers.
        # The current control flow happens to avoid crashing on a null
        # chat_messages (the `if chat_messages:` branch falls through
        # and `has_branches(chat_messages)` is gated by `if not chat_messages
        # else`), but the same shape WOULD crash if a future refactor
        # moved a `for m in chat_messages` outside the guard. Normalize
        # at the boundary for parity with get_conversation /
        # get_conversation_tree (fixed in f9a2fd2).
        chat_messages = data.get("chat_messages") or []
        # Use pre-computed counts if available (from fast reader), else calculate.
        # The else-branch reads counts straight from disk and passes them to
        # Pydantic ``int``-typed fields on ``ConversationSummary``. A
        # corrupt-on-disk value (``"foo"`` / null / list / dict) would
        # propagate as a ValidationError → 500 in the ``list_conversations``
        # loop, taking out the entire sidebar for one bad row. ``_safe_int``
        # mirrors the prelude_hidden_count fix in ``get_conversation`` and
        # the parse_datetime fix in ``parsing.py`` (Council coercion audit).
        conv_uuid = data.get("uuid")
        if chat_messages:
            message_count = len(chat_messages)
            human_count = sum(1 for m in chat_messages if m.get("sender") == "human")
        else:
            message_count = _safe_int(
                data.get("message_count"),
                default=0,
                field="message_count",
                uuid=conv_uuid,
            )
            human_count = _safe_int(
                data.get("human_message_count"),
                default=0,
                field="human_message_count",
                uuid=conv_uuid,
            )

        # Parse subagents if requested
        subagents = []
        if include_subagents:
            for agent_data in data.get("subagents", []):
                subagents.append(SubagentSummary(
                    uuid=agent_data.get("uuid", ""),
                    agent_id=agent_data.get("agent_id", ""),
                    name=agent_data.get("name", ""),
                    model=agent_data.get("model", ""),
                    created_at=_parse_datetime(agent_data.get("created_at")),
                    updated_at=_parse_datetime(agent_data.get("updated_at")),
                    # Same coerce-with-fallback as the parent counts above;
                    # subagent metadata flows from cc_agent_reader (trusted)
                    # in production, but a partial-write or hand-edit on the
                    # parent JSON could surface a non-int here too.
                    message_count=_safe_int(
                        agent_data.get("message_count"),
                        default=0,
                        field="subagents[].message_count",
                        uuid=agent_data.get("uuid"),
                    ),
                ))

        # ``data.get(k, fallback_str)`` returns the fallback ONLY when the
        # key is MISSING. If the key is present with value ``None`` (legacy
        # / partial-write JSON), ``.get`` returns ``None``, which the
        # Pydantic ``str``-typed fields on ``ConversationSummary`` reject
        # with a ValidationError → HTTP 500. Same bug-class as 8ab36fc;
        # ``(value or fallback)`` collapses both None and missing into the
        # safe fallback. Pinned by
        # ``test_list_conversations_handles_null_name_summary_model_without_crashing``.
        return ConversationSummary(
            uuid=data.get("uuid") or "",
            name=data.get("name") or "Untitled",
            summary=data.get("summary") or "",
            model=data.get("model") or "",
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            is_starred=bool(data.get("is_starred") or False),
            message_count=message_count,
            human_message_count=human_count,
            # See `get_conversation` for the CC branch-flag rationale —
            # post-compact duplicate UUIDs poison the parent->children
            # count, and CC has no edit-branch UI to switch to anyway.
            has_branches=(
                False
                if data.get("source") == "CLAUDE_CODE"
                else (bool(data.get("has_branches") or False) if not chat_messages else has_branches(chat_messages))
            ),
            source=data.get("source") or "CLAUDE_AI",
            project_path=data.get("project_path"),
            git_branch=data.get("git_branch"),
            organization_id=data.get("organization_id"),
            organization_name=data.get("organization_name"),
            subagents=subagents,
            # D8: Cowork sidecar.isArchived. Defaults to False on
            # Desktop + CC (they don't surface an archived flag).
            is_archived=bool(data.get("is_archived") or False),
        )

    def _get_claude_code_conversations(
        self, full_content: bool = False, include_phantom: bool = False
    ) -> list[dict[str, Any]]:
        """Get all Claude Code conversations from JSONL files.

        Args:
            full_content: If True, read full message content (slower, for search).
                         If False, only read metadata (fast, for listing).
            include_phantom: If True, include phantom sessions (local command artifacts).
        """
        return list_claude_code_conversations(
            self.claude_dir, full_content=full_content, include_phantom=include_phantom
        )

    def _get_all_conversations_data(
        self,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        full_content: bool = False,
        include_phantom: bool = False,
    ) -> list[dict[str, Any]]:
        """Get raw conversation data from all sources.

        Args:
            source: Filter by conversation source
            full_content: If True, read full message content (for search).
                         If False, only read metadata (fast, for listing).
            include_phantom: If True, include phantom sessions (local command artifacts).
        """
        conversations = []

        # Load Claude Desktop conversations (from JSON files)
        if source in ("all", "CLAUDE_AI"):
            for path in self._get_conversation_files():
                data = self._load_conversation(path)
                if data:
                    # Skip Claude Code / Cowork conversations that might
                    # have been imported into the Desktop data dir (e.g.
                    # by a future export-and-reimport flow). Without
                    # both checks a CLAUDE_COWORK conv stamped at ingest
                    # could double-count as CLAUDE_AI.
                    if data.get("source") in ("CLAUDE_CODE", "CLAUDE_COWORK"):
                        continue
                    conversations.append(data)

        # Load Claude Code conversations (from JSONL files)
        if source in ("all", "CLAUDE_CODE"):
            conversations.extend(self._get_claude_code_conversations(
                full_content=full_content, include_phantom=include_phantom
            ))

        # Load Cowork conversations: local-agent-mode-sessions audit.jsonl
        # plus remote (cloud task) sessions archived by the fetcher. A list
        # needs only summaries, which are cached; search and export need the
        # full sessions.
        if source in ("all", "CLAUDE_COWORK"):
            if full_content:
                from .cowork_reader import list_cowork_conversations
                conversations.extend(
                    list_cowork_conversations(self.cowork_root, self.remote_cowork_root)
                )
            else:
                conversations.extend(self._cowork_summaries())

        return conversations

    def _cowork_summaries(self) -> list[dict[str, Any]]:
        """Cached summaries of every Cowork session; see the cache notes above."""
        from . import cowork_reader

        roots = [r for r in (self.cowork_root, self.remote_cowork_root) if r is not None]
        out: list[dict[str, Any]] = []
        live: set[Path] = set()
        for session_dir in cowork_reader.iter_cowork_session_dirs(
            self.cowork_root, self.remote_cowork_root
        ):
            live.add(session_dir)
            # Stat before the read: a change during the read then has a newer
            # key than the one stored, so the next call reads it again.
            key = _cowork_files_key(session_dir)
            with _cowork_summary_lock:
                hit = _cowork_summary_cache.get(session_dir)
            if hit is not None and hit[0] == key:
                summary = hit[1]
            else:
                try:
                    data = cowork_reader.read_cowork_conversation(session_dir)
                except Exception:  # pragma: no cover  one bad session must not empty the list
                    logger.exception("cowork: failed to read session %s", session_dir)
                    continue
                summary = _cowork_summary(data) if data else None
                with _cowork_summary_lock:
                    _cowork_summary_cache[session_dir] = (key, summary)
            if summary is not None:
                out.append(dict(summary))  # a copy: callers must not change the cache
        # Forget sessions under these roots that no longer exist.
        with _cowork_summary_lock:
            for cached in list(_cowork_summary_cache):
                if cached not in live and any(cached.is_relative_to(r) for r in roots):
                    del _cowork_summary_cache[cached]
        # One row per uuid, as list_cowork_conversations gives search/export.
        return cowork_reader.newest_per_uuid(out)

    def list_conversations(
        self,
        search: str | None = None,
        starred: bool | None = None,
        model: str | None = None,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
        sort: str = "updated_at",
        sort_order: Literal["asc", "desc"] = "desc",
        include_phantom: bool = False,
        include_subagents: bool = False,
        organization_id: str | None = None,
        show_archived: bool = False,
    ) -> list[ConversationSummary]:
        """List all conversations with optional filtering.

        D8 (Cowork): archived sessions (sidecar.isArchived=True) are
        hidden by default; pass ``show_archived=True`` to include
        them. Desktop + CC don't surface an archived flag, so the
        filter is effectively no-op for those sources.
        """
        conversations = []

        for data in self._get_all_conversations_data(source, include_phantom=include_phantom):
            # Apply filters
            if not show_archived and data.get("is_archived"):
                # D8: default-hide archived Cowork sessions. The toggle
                # in the sidebar passes show_archived=True to override.
                continue
            if starred is not None and data.get("is_starred") != starred:
                continue
            if model and data.get("model") != model:
                continue
            if organization_id is not None and data.get("organization_id") != organization_id:
                # cowork-multi-org C6: workspace filter. None matches only
                # exactly None (legacy untagged), so a UUID filter never
                # incidentally surfaces untagged data.
                continue
            if search:
                search_lower = search.lower()
                # `dict.get(key, "")` is unsafe when the key exists but
                # the value is `None` — `None.lower()` raises
                # AttributeError and the route crashes mid-iteration.
                # CC sessions sometimes have `project_path: null` for
                # the original cwd; Desktop conversations sometimes have
                # `summary: null` for short threads. `(value or "")`
                # collapses both None and missing to the empty string,
                # which `.lower()` handles safely. Regression test:
                # backend/tests/test_conversations.py::
                # test_sidebar_search_filters_by_name (and siblings).
                name_match = search_lower in (data.get("name") or "").lower()
                summary_match = search_lower in (data.get("summary") or "").lower()
                project_match = search_lower in (data.get("project_path") or "").lower()
                if not (name_match or summary_match or project_match):
                    continue

            conversations.append(self._make_summary(data, include_subagents=include_subagents))

        # Sort
        # Hunt #12 — Unstable sort tiebreakers. Timsort is stable, but
        # only on input order, which here depends on
        # _get_all_conversations_data() iteration of FileCache + glob
        # walk — both non-deterministic across cache rebuilds. Two
        # conversations with identical primary keys (e.g., the same
        # auto-title "Untitled", or the same fetch-time-rounded
        # updated_at) would silently flip order between sidebar
        # refreshes. Append c.uuid as the final tiebreaker; for
        # string-primary sorts (name, project) slot c.updated_at
        # BETWEEN the primary key and the UUID so same-name
        # conversations cluster by time within the name group rather
        # than UUID-scattering.
        reverse = sort_order == "desc"
        if sort == "name":
            conversations.sort(
                key=lambda c: (c.name.lower(), c.updated_at, c.uuid),
                reverse=reverse,
            )
        elif sort == "created_at":
            conversations.sort(
                key=lambda c: (c.created_at, c.uuid),
                reverse=reverse,
            )
        elif sort == "project":
            # Sort by project_name (None values go last)
            conversations.sort(
                key=lambda c: (
                    c.project_name is None,
                    (c.project_name or "").lower(),
                    c.updated_at,
                    c.uuid,
                ),
                reverse=reverse,
            )
        else:  # updated_at (default)
            conversations.sort(
                key=lambda c: (c.updated_at, c.uuid),
                reverse=reverse,
            )

        return conversations

    def _find_conversation_data(self, uuid: str) -> tuple[dict[str, Any] | None, Path | None]:
        """Find conversation data by UUID from any source.

        Returns (data, file_path) tuple.
        """
        # First check Claude Desktop JSON files
        for path in self._get_conversation_files():
            data = self._load_conversation(path)
            if data and data.get("uuid") == uuid:
                return data, path

        # Then check Claude Code JSONL files. Route through the FileCache
        # wrapper (_load_conversation_cached) so warm detail-page and
        # export calls don't re-parse the entire JSONL on every request.
        # The Desktop branch above already uses self._load_conversation
        # which goes through FileCache; CC was missed when the cache
        # wrapper was added in a prior refactor. See
        # PLANS/PERFORMANCE_PHASE_2.md §Workstream C1.
        #
        # Lookup. Since 2026-10-21 a Claude Code conversation's uuid is its
        # file name (``cc_message_transforms.cc_session_uuid``), so Pass A
        # finds every conversation that the list advertises. Before that,
        # the uuid came from the first user row's ``sessionId``, and a
        # continued session's file (whose first rows are copied from the
        # session it continues) needed Pass B, which matched on the summary
        # uuid. Pass B and the final scan now only run for a uuid that no
        # file has, and then return 404.
        cc_files = list(discover_jsonl_files(self.claude_dir))

        for jsonl_path in cc_files:
            if jsonl_path.stem == uuid:
                data = _load_conversation_cached(jsonl_path)
                if data and data.get("uuid") == uuid:
                    return data, jsonl_path

        # Pass B: scan via summary cache (warm: in-memory bulk SELECT;
        # cold: misses repopulate as a side effect — acceptable since
        # detail-page misses are user-driven, not bulk).
        from .summary_cache import get_summary_cache
        from .cc_jsonl_io import read_conversation_summary_fast

        summary_cache = get_summary_cache()
        stat_index: dict[Path, Any] = {}
        for p in cc_files:
            try:
                stat_index[p] = p.stat()
            except OSError:
                continue

        if summary_cache is not None:
            cached = summary_cache.get_many(cc_files, stat_index)
            for jsonl_path, summary in cached.items():
                if summary and summary.get("uuid") == uuid:
                    data = _load_conversation_cached(jsonl_path)
                    if data:
                        return data, jsonl_path

        # Final fallback (cache unavailable OR cache miss across the
        # board): scan paths directly with the fast reader. Slower but
        # never silently returns 404 when the data IS on disk.
        for jsonl_path in cc_files:
            if jsonl_path.stem == uuid:
                continue  # already tried in Pass A
            try:
                summary = read_conversation_summary_fast(jsonl_path)
            except Exception:
                # C3 silent-swallow fix (LLM council code-review,
                # 2026-05-21): the loop's leading comment promises
                # "never silently returns 404 when the data IS on
                # disk", but a parse / permission / decode failure on
                # ANY file here would previously silently skip it
                # with zero diagnostics. The user sees "Conversation
                # not found" and there's no operator breadcrumb for
                # which file failed. Log at WARNING with exc_info so
                # the exception type (which drives the fix) is
                # captured. Then keep ``continue`` so behavior is
                # otherwise preserved.
                logger.warning(
                    "Failed to read Claude Code summary for %s while "
                    "resolving uuid=%s",
                    jsonl_path,
                    uuid,
                    exc_info=True,
                )
                continue
            if summary and summary.get("uuid") == uuid:
                data = _load_conversation_cached(jsonl_path)
                if data:
                    return data, jsonl_path

        # Finally check Cowork sessions, local (``local_<uuid>``) and remote
        # (``remote_<id>``). ``find_cowork_session_dirs`` checks one candidate
        # per org dir, so this resolution is O(orgs), not O(sessions). Try
        # every copy: the first one can be empty or have no user turn while
        # the list shows the row from another copy. Of two readable copies,
        # open the newest, which is the one the list shows (newest_per_uuid).
        from .cowork_reader import (
            find_cowork_session_dirs,
            read_cowork_conversation,
            updated_at_key,
        )
        found: tuple[dict[str, Any], Path] | None = None
        for candidate in find_cowork_session_dirs(
            self.cowork_root, self.remote_cowork_root, uuid
        ):
            data = read_cowork_conversation(candidate)
            if not data or data.get("uuid") != uuid:
                continue
            if found is None or updated_at_key(data) > updated_at_key(found[0]):
                found = (data, candidate)
        if found is not None:
            return found[0], found[1] / "audit.jsonl"

        return None, None

    def _build_detail_from_data(
        self,
        data: dict[str, Any],
        file_path: Path | None,
        leaf_override: str | None,
    ) -> ConversationDetail:
        """Build a ConversationDetail from already-loaded raw data.

        Extracted from ``get_conversation`` (2026-05-23) so the W4 cache
        layer can call this without re-running ``_find_conversation_data``
        on every miss. Pure function of its inputs; no I/O.
        """
        # See _make_summary for the (value or fallback) rationale —
        # an explicit null on chat_messages used to crash every
        # downstream iteration (any/len/for-in/has_branches) with a
        # TypeError. Same fix shape applied to the str-typed fields
        # below at the ConversationDetail construction.
        chat_messages = data.get("chat_messages") or []
        stored_leaf = data.get("current_leaf_message_uuid") or ""
        source = data.get("source") or "CLAUDE_AI"
        # Cowork shares the chronological append-only log semantics —
        # no parentUuid links, no branches, no leaf-walking. Widen the
        # CC guard so the same render path applies to both.
        is_chronological = source in ("CLAUDE_CODE", "CLAUDE_COWORK")

        # Bug-fix (2026-05-12): Claude Code JSONLs are append-only
        # chronological logs, NOT branched message trees. CC re-serializes
        # parts of the prior conversation after every `/compact`, producing
        # duplicate message UUIDs across the pre/post-compact boundary that
        # the streaming-chunk dedupe in `claude_code_reader._get_message_key`
        # collapses into a single message. Walking the parent_message_uuid
        # chain from `current_leaf_message_uuid` then hits a synthetic
        # cycle and silently drops every pre-compact message. CC also has
        # no edit-branch UI, so `leaf_override` is meaningless. For CC we
        # render the chronological message stream and skip leaf-walking
        # entirely. Compact markers continue to render inline via the
        # `compact_markers` array on the response.
        # Cowork extension (2026-05-25): same fix applies — Cowork
        # audit.jsonl has no parentUuid field at all.
        if is_chronological:
            leaf_uuid = stored_leaf
            branch = chat_messages
            # has_branches() is also unreliable for CC: post-compact
            # duplicate UUIDs make a single parent look like it has
            # multiple children. CC has no true edit-branches.
            branches_flag = False
        else:
            leaf_uuid = leaf_override or stored_leaf
            # Validate leaf_override actually exists in this conversation;
            # fall back to the stored leaf if the caller passed something
            # stale.
            if leaf_override and not any(m.get("uuid") == leaf_override for m in chat_messages):
                leaf_uuid = stored_leaf

            if leaf_uuid and chat_messages:
                branch = resolve_active_branch(chat_messages, leaf_uuid)
            else:
                branch = chat_messages
            branches_flag = has_branches(chat_messages)

        messages = [_parse_message(m) for m in branch]
        human_count = sum(1 for m in chat_messages if m.get("sender") == "human")

        # Council coercion-audit (bug-class #1) MED finding: ``int(x)`` raises
        # ValueError on non-numeric truthy strings (e.g. ``"foo"``) and
        # TypeError on list/dict shapes. The ``or 0`` collapses None/0 but
        # NOT a corrupt non-numeric value. A hand-edited / partial-write
        # JSON file with ``"prelude_hidden_count": "foo"`` would 500 the
        # detail route. Try/except + default-to-0 mirrors the documented
        # fallback semantics of ``parse_datetime`` for the same root cause.
        raw_prelude = data.get("prelude_hidden_count")
        try:
            prelude_count = int(raw_prelude) if raw_prelude is not None else 0
        except (ValueError, TypeError):
            logger.warning(
                "Conversation %s has non-numeric prelude_hidden_count %r; "
                "defaulting to 0",
                data.get("uuid"),
                raw_prelude,
            )
            prelude_count = 0

        # ``data.get(k, fallback)`` returns the fallback ONLY when the key
        # is MISSING; an explicit ``None`` value reaches Pydantic and is
        # rejected by str/bool/list-typed fields with HTTP 500. See
        # _make_summary for the matching fix on the list path.
        return ConversationDetail(
            uuid=data.get("uuid") or "",
            name=data.get("name") or "Untitled",
            summary=data.get("summary") or "",
            model=data.get("model") or "",
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            is_starred=bool(data.get("is_starred") or False),
            message_count=len(chat_messages),
            human_message_count=human_count,
            has_branches=branches_flag,
            source=source,
            project_path=data.get("project_path"),
            git_branch=data.get("git_branch"),
            messages=messages,
            current_leaf_message_uuid=leaf_uuid,
            file_path=str(file_path) if file_path else None,
            compact_markers=data.get("compact_markers") or [],
            # V1 polish (2026-05-12): CC reader's `_flag_leading_prelude_markers`
            # emits this; Desktop data dicts won't contain it, so default 0.
            prelude_hidden_count=prelude_count,
            # D8/D9/D10 (Cowork, Phase 6): forward sidecar-derived
            # is_archived/error/sandbox_path. Defaults are safe for
            # Desktop + CC (which don't populate these keys).
            is_archived=bool(data.get("is_archived") or False),
            error=data.get("error"),
            sandbox_path=data.get("sandbox_path"),
        )

    def get_conversation(self, uuid: str, leaf_override: str | None = None) -> ConversationDetail | None:
        """Get a single conversation by UUID with resolved active branch.

        Returns a Pydantic ``ConversationDetail``. **Not cached** (callers
        that expect the Pydantic shape — export routes, tests — must keep
        working unchanged). The /api/conversations/<uuid> route hits the
        cached-dict path via :meth:`get_conversation_dict` instead.

        Args:
            uuid: Conversation UUID.
            leaf_override: If provided, render the branch ending at this message
                UUID instead of the conversation's stored current leaf.
        """
        data, file_path = self._find_conversation_data(uuid)
        if not data:
            return None
        return self._build_detail_from_data(data, file_path, leaf_override)

    def get_conversation_dict(
        self, uuid: str, leaf_override: str | None = None
    ) -> dict[str, Any] | None:
        """Get a conversation as a ``model_dump(mode='json')`` dict, cached.

        W3+W4 fast path for the /api/conversations/<uuid> route. The
        returned dict is suitable for direct hand-off to ORJSONResponse,
        bypassing both Pydantic rebuild AND FastAPI's default encoder.

        Caching:
          * Cache key: ``uuid`` only; entry value: ``(mtime, dict)``.
            On hit when ``mtime`` matches the on-disk file, returns the
            same dict instance (`is`-identical).
          * ``leaf_override`` is NEVER cached — every call with a
            non-None override rebuilds (rare path; avoids cardinality
            explosion).
          * Cap: 32 entries, LRU-evicted.

        Mutability: callers MUST treat the returned dict as immutable.
        The dict is shared across requests; mutating it poisons the
        cache for every subsequent reader.

        Args:
            uuid: Conversation UUID.
            leaf_override: If provided, NEVER cached; rebuilds every call.

        Returns:
            A dict (the ``model_dump(mode='json')`` shape of
            :class:`ConversationDetail`), or ``None`` if no conversation
            with that uuid exists.
        """
        # Leaf override: bypass cache entirely.
        if leaf_override is not None:
            data, file_path = self._find_conversation_data(uuid)
            if not data:
                return None
            detail = self._build_detail_from_data(data, file_path, leaf_override)
            return detail.model_dump(mode="json")

        # Default-leaf path: cache by (uuid, mtime).
        data, file_path = self._find_conversation_data(uuid)
        if not data:
            return None

        # Compute current mtime — if file_path is None (synthetic /
        # malformed data) we cannot key the cache; rebuild every call.
        if file_path is None:
            detail = self._build_detail_from_data(data, file_path, None)
            return detail.model_dump(mode="json")

        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            # Race: file vanished. Build once, don't cache.
            detail = self._build_detail_from_data(data, file_path, None)
            return detail.model_dump(mode="json")

        with _DETAIL_DICT_CACHE_LOCK:
            hit = _DETAIL_DICT_CACHE.get(uuid)
            if hit is not None and hit[0] == current_mtime:
                # Cache HIT — promote to MRU.
                _DETAIL_DICT_CACHE.move_to_end(uuid)
                return hit[1]

        # Cache MISS — build outside the lock so concurrent misses for
        # DIFFERENT uuids don't serialize behind each other's
        # Pydantic+dump work. Worst case: two concurrent misses for the
        # SAME uuid both build; the last writer wins. Idempotent
        # (deterministic from `data`).
        detail = self._build_detail_from_data(data, file_path, None)
        dumped = detail.model_dump(mode="json")

        with _DETAIL_DICT_CACHE_LOCK:
            # Re-fetch in case another thread populated it; if so, return
            # THEIR instance (so other observers' `is` identity holds).
            existing = _DETAIL_DICT_CACHE.get(uuid)
            if existing is not None and existing[0] == current_mtime:
                _DETAIL_DICT_CACHE.move_to_end(uuid)
                return existing[1]
            # Pop-then-reinsert is the canonical MRU promotion pattern for
            # OrderedDict (reassignment alone does NOT reposition).
            if uuid in _DETAIL_DICT_CACHE:
                del _DETAIL_DICT_CACHE[uuid]
            _DETAIL_DICT_CACHE[uuid] = (current_mtime, dumped)
            # LRU evict.
            while len(_DETAIL_DICT_CACHE) > _DETAIL_DICT_CACHE_MAX:
                _DETAIL_DICT_CACHE.popitem(last=False)
            return dumped

    def get_conversation_tree(self, uuid: str) -> ConversationTree | None:
        """Get the full message tree for a conversation."""
        data, _ = self._find_conversation_data(uuid)
        if not data:
            return None

        # Same None-safety guard as get_conversation; an explicit null
        # on chat_messages must not reach build_message_tree.
        chat_messages = data.get("chat_messages") or []
        leaf_uuid = data.get("current_leaf_message_uuid") or ""
        source = data.get("source") or "CLAUDE_AI"
        # Cowork is also chronological-only — same zero-state tree.
        is_chronological = source in ("CLAUDE_CODE", "CLAUDE_COWORK")

        # Bug-fix (2026-05-12, follow-up to get_conversation): Claude Code
        # JSONLs are append-only chronological logs with no edit-branches;
        # the same duplicate-UUID cycle that poisoned get_conversation's
        # leaf-walk also poisons build_message_tree / resolve_active_branch
        # here, and a synthesized linear-chain replacement would trip
        # Pydantic's recursive serialization at sessions ≥ ~1000 messages
        # (real CC sessions reach 1400+). The frontend's TreeViewModal is
        # already hidden for CC (has_branches=False), so a tree endpoint
        # response is semantically meaningless. Return the zero-state
        # envelope — same shape the route returns for an empty Desktop
        # conversation (see test_conversations_tree.py:476).
        if is_chronological:
            return ConversationTree(
                uuid=uuid,
                root_messages=[],
                active_path=[],
            )

        # Build full tree
        root_messages = build_message_tree(chat_messages)

        # Get active path
        if leaf_uuid:
            branch = resolve_active_branch(chat_messages, leaf_uuid)
            active_path = [m["uuid"] for m in branch]
        else:
            active_path = []

        return ConversationTree(
            uuid=uuid,
            root_messages=root_messages,
            active_path=active_path,
        )

    def get_all_conversations_raw(
        self,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
    ) -> list[dict[str, Any]]:
        """Get all raw conversation data for search/export (includes full message content)."""
        return self._get_all_conversations_data(source, full_content=True)

    def count_conversations(
        self,
        source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
    ) -> int:
        """Count total number of conversations."""
        return len(self._get_all_conversations_data(source))