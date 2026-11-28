"""Store module for reading conversation JSON files from disk."""

import json
import logging
import re
import sqlite3
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from . import session_state
from .archive_sync import archive_roots, live_first, machine_fields, pick_copies
from .cc_peer_messages import compute_links, link_detail_dict, link_detail_model
from .cc_subagents import (
    AgentFile,
    agent_display_name,
    discover_agents,
    find_agent,
    is_valid_agent_id,
    iter_agent_files,
    owner_of,
    read_agent_conversation,
    subagents_found,
)
from .claude_code_reader import (
    _load_conversation_cached,
    discover_jsonl_files,
    list_claude_code_conversations,
    # Re-exported so existing tests that patch
    # ``backend.store.read_claude_code_conversation`` keep their
    # bind point (the fast-path C1 fix routes through
    # _load_conversation_cached, which calls this name via the
    # claude_code_reader module's own binding — but tests that
    # patch store.py-side as a belt-and-suspenders measure want
    # this symbol to exist).
    read_claude_code_conversation,  # noqa: F401  (re-export for tests)
)
from .config import get_settings
from .machines import clear_label_memo, shown_labels
from .model_names import CONVERSATION_MODEL_SOURCES
from .models import (
    ContentBlock,
    ConversationDetail,
    ConversationSummary,
    ConversationTree,
    Message,
    MessageNode,
    PeerGraph,
    PeerGraphMessage,
    PeerGraphSession,
    SubagentFound,
    SubagentSummary,
)
from .parsing import parse_datetime as _parse_datetime
from .session_state import SessionState, cc_file_ended, live_session_ids, state_of
from .sources.base import SourceAdapter
from .sources.ids import SourceFilter
from .sources.registry import get_adapter_or_none, get_adapters, get_descriptor

logger = logging.getLogger(__name__)

# The sources whose rows name a machine (#92); Desktop conversations do not.
_MACHINE_SOURCES = frozenset({"CLAUDE_CODE", "CLAUDE_COWORK", "CODEX"})


# W3+W4 (2026-05-23 council decision): module-level cache of the
# rendered ``model_dump(mode='json')`` output for ConversationDetail.
#
# Why a dict cache (not a Pydantic-object cache):
#   * On warm hits, the route returns OrjsonResponse(content=cached_dict)
#     which bypasses BOTH Pydantic rebuild (~186 ms per call on the
#     user's 16K-msg conv) AND FastAPI's default encoder (~70 ms).
#   * Caching the Pydantic object would still pay model_dump on every
#     hit — the dict shape is the actual perf target.
#
# Cache key: (uuid, file path); the entry holds the mtime. Two sources (or
# two stores) can hold the same uuid, so the path is in the key. When the
# file's mtime changes, the next lookup is a miss and rebuilds.
#
# Mutability contract: the cached dict is SHARED across requests. Callers
# MUST treat it as immutable. The conversation route returns it directly
# to OrjsonResponse (backend/responses.py), which serializes without mutation.
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
_DETAIL_DICT_CACHE: OrderedDict[tuple[str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_DETAIL_DICT_CACHE_LOCK = threading.RLock()


def _agent_key(path: Path) -> tuple[str, str]:
    """An agent file's identity across roots: its session (or project) and its name."""
    return (path.parent.parent.name if path.parent.name == "subagents" else path.parent.name, path.name)


def invalidate_detail_dict_cache(uuid: str | None = None) -> None:
    """Drop one (or all) entries from the detail-dict cache.

    Call with ``uuid=None`` to clear the entire cache (used by tests
    and the CC watcher when it detects out-of-band file changes).
    """
    with _DETAIL_DICT_CACHE_LOCK:
        if uuid is None:
            _DETAIL_DICT_CACHE.clear()
        else:
            for key in [k for k in _DETAIL_DICT_CACHE if k[0] == uuid]:
                del _DETAIL_DICT_CACHE[key]


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
            code_run=block.get("code_run"),
            from_code_mode=block.get("from_code_mode"),
            ambiguous_with=block.get("ambiguous_with"),
            peer_ref=block.get("peer_ref"),
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
    "model",
    "model_from_conversation",
    "command_output",
    "command_output_is_error",
    "shell_command",
    "shell_output",
    "notice_kind",
    "peer_ref",
)
_CC_ONLY_DEFAULTS: dict[str, Any] = {
    "is_command_marker": False,
    "is_prelude": False,
    "assistant_canned_response_consumed": False,
    "slash_command": None,
    "model": None,
    "model_from_conversation": False,
    "command_output": None,
    "command_output_is_error": False,
    "shell_command": None,
    "shell_output": None,
    "notice_kind": None,
    "peer_ref": None,
}


def _conversation_model(data: dict[str, Any]) -> str | None:
    """The model that every reply of ``data`` gets, for a source that records
    one model for the conversation (issue #52, D5); else None."""
    if (data.get("source") or "CLAUDE_AI") in CONVERSATION_MODEL_SOURCES:
        return data.get("model") or None
    return None


def _parse_message(raw: dict[str, Any], conversation_model: str | None = None) -> Message:
    """Parse a raw message dict into a Message model."""
    content = raw.get("content", [])
    cc_kwargs = {
        k: raw.get(k, _CC_ONLY_DEFAULTS[k]) for k in _CC_ONLY_PASSTHROUGH_FIELDS
    }
    if conversation_model and raw.get("sender") == "assistant":
        cc_kwargs["model"] = conversation_model
        cc_kwargs["model_from_conversation"] = True
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


def build_message_tree(
    messages: list[dict[str, Any]], conversation_model: str | None = None
) -> list[MessageNode]:
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
            message=_parse_message(msg_by_uuid[uuid], conversation_model),
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
    """A Cowork conversation without its messages, plus the counts that
    ``_make_summary`` would have computed from them. A Cowork session has no
    branches, so the summary needs no branch flag."""
    messages = data.get("chat_messages") or []
    summary = {k: v for k, v in data.items() if k not in _COWORK_HEAVY_KEYS}
    summary["message_count"] = len(messages)
    summary["human_message_count"] = sum(1 for m in messages if m.get("sender") == "human")
    return summary


def _is_chronological(source: str) -> bool:
    """No branch tree: the messages are in file order. False for an unknown source."""
    descriptor = get_descriptor(source)
    return descriptor is not None and descriptor.chronological


def _peer_edges(
    rows: list[dict[str, Any]], key_of: Any, codex_root: Any,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Issue #91: the messages between sessions from the ``peer_messages`` rows.

    Returns ``(edges, names, titles)``: names and titles by session key. A send
    pairs with each receive of the same ``msg_id`` in another conversation (a
    Codex key only within one thread tree). A Codex send with no receive goes
    to the thread of its recipient path; any other side that is missing is a
    "name:<peer name>" session.
    """
    by_key: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    titles: dict[str, str] = {}
    for r in rows:
        by_key.setdefault(r["msg_id"], []).append(r)
        if r.get("title"):
            titles.setdefault(key_of(r), r["title"])

    def same_tree(a: dict, b: dict) -> bool:
        return codex_root is None or not a["msg_id"].startswith("cx") or \
            codex_root(a["session_uuid"]) == codex_root(b["session_uuid"])

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(msg_id: str, s: dict | None, r: dict | None, from_key: str, to_key: str) -> None:
        if from_key == to_key or (msg_id, from_key, to_key) in seen:
            return
        seen.add((msg_id, from_key, to_key))
        edges.append({
            "msg_id": msg_id, "kind": "message", "from_key": from_key, "to_key": to_key,
            "from_conv": s["conv_uuid"] if s else None, "from_message_uuid": (s or {}).get("message_uuid") or None,
            "from_block": (s or {}).get("block_id") or None,
            "to_conv": r["conv_uuid"] if r else None, "to_message_uuid": (r or {}).get("message_uuid") or None,
        })

    for msg_id, group in by_key.items():
        sends = [g for g in group if g["direction"] == "sent"]
        gets = [g for g in group if g["direction"] == "received"]
        for s in sends:
            matches = [g for g in gets if g["conv_uuid"] != s["conv_uuid"] and same_tree(s, g)]
            for g in matches:
                if g.get("peer_name"):
                    names.setdefault(key_of(s), g["peer_name"])
                if s.get("peer_name"):
                    names.setdefault(key_of(g), s["peer_name"])
                add(msg_id, s, g, key_of(s), key_of(g))
            if matches:
                continue
            threads = [t for t in by_key.get(f"cxpath|{s.get('peer_name')}", []) if same_tree(s, t)]
            if msg_id.startswith("cx|") and len({t["session_uuid"] for t in threads}) == 1:
                add(msg_id, s, None, key_of(s), key_of(threads[0]))
            elif s.get("peer_name"):
                add(msg_id, s, None, key_of(s), f"name:{s['peer_name']}")
        for g in gets:
            if g.get("peer_name") and not any(s["conv_uuid"] != g["conv_uuid"] and same_tree(s, g) for s in sends):
                add(msg_id, None, g, f"name:{g['peer_name']}", key_of(g))
    return edges, names, titles


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

    def _make_summary(
        self,
        data: dict[str, Any],
        include_subagents: bool = False,
        adapters: dict[str, SourceAdapter] | None = None,
    ) -> ConversationSummary:
        """Create a ConversationSummary from raw conversation data.

        ``adapters`` maps source ids to adapters; a list passes it once
        for all its rows. Without it, the adapter is looked up.
        """
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
        source = data.get("source") or "CLAUDE_AI"
        updated_at = _parse_datetime(data.get("updated_at"))
        return ConversationSummary(
            uuid=data.get("uuid") or "",
            name=data.get("name") or "Untitled",
            summary=data.get("summary") or "",
            model=data.get("model") or "",
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=updated_at,
            is_starred=bool(data.get("is_starred") or False),
            message_count=message_count,
            human_message_count=human_count,
            # See `get_conversation` for the CC branch-flag rationale —
            # post-compact duplicate UUIDs poison the parent->children
            # count, and CC has no edit-branch UI to switch to anyway.
            has_branches=self._branch_flag(data, chat_messages, adapters),
            source=source,
            project_path=data.get("project_path"),
            git_branch=data.get("git_branch"),
            organization_id=data.get("organization_id"),
            organization_name=data.get("organization_name"),
            subagents=subagents,
            # D8: Cowork sidecar.isArchived. Defaults to False on
            # Desktop + CC (they don't surface an archived flag).
            is_archived=bool(data.get("is_archived") or False),
            source_name=data.get("source_name"),
            source_home=data.get("source_home"),
            source_home_abs=data.get("source_home_abs"),
            notices=data.get("notices") or [],
            parent_uuid=data.get("parent_uuid"),
            # By age only; Claude Code rows get theirs in _set_states (registry, transcript).
            state=None if source == "CLAUDE_CODE" else self.state_for(source, updated_at, None, None),
            **self._machine_fields(data),
        )

    def _machine_fields(self, data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
        """``machine_id``, ``machine_label``, ``machine_labels`` of a row (#92); None for Desktop."""
        mid, ids = data.get("machine_id"), data.get("machine_ids")
        if not mid and path is not None and data.get("source") in _MACHINE_SOURCES:
            found = machine_fields(path)
            mid, ids = found["machine_id"], found["machine_ids"]
        if not isinstance(mid, str) or not mid:
            return {"machine_id": None, "machine_label": None, "machine_labels": []}
        labels = shown_labels(self.data_dir)
        ids = [m for m in (ids or [mid]) if isinstance(m, str)] or [mid]
        return {
            "machine_id": mid,
            "machine_label": labels.get(mid) or f"Machine {mid[:8]}",
            "machine_labels": [labels.get(m) or f"Machine {m[:8]}" for m in ids],
        }

    def _branch_flag(
        self,
        data: dict[str, Any],
        chat_messages: list[dict[str, Any]],
        adapters: dict[str, SourceAdapter] | None,
    ) -> bool:
        """The list-row branch flag; the source adapter decides it.

        A missing source is CLAUDE_AI. An unknown source computes the flag.
        """
        source = data.get("source") or "CLAUDE_AI"
        if adapters is not None:
            adapter = adapters.get(source)
        else:
            adapter = get_adapter_or_none(source, self)
        if adapter is None:
            return self._computed_branch_flag(data, chat_messages)
        return adapter.branch_flag(data, chat_messages)

    @staticmethod
    def _computed_branch_flag(data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        """The branch flag from the messages, or the stored flag of a row without them."""
        if not chat_messages:
            return bool(data.get("has_branches") or False)
        return has_branches(chat_messages)

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
            self.claude_dir, full_content=full_content, include_phantom=include_phantom,
            archive_roots=self._cc_archive_roots(),
        )

    def _get_all_conversations_data(
        self,
        source: SourceFilter = "all",
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
        for adapter in get_adapters(self):
            if source in ("all", adapter.descriptor().id):
                conversations.extend(
                    adapter.list_rows(full_content=full_content, include_phantom=include_phantom)
                )
        return conversations

    # ----- Bridge methods of the source adapters (backend/sources/) -----
    # The per-source code stays here, so that the patch points of the
    # existing tests keep working.

    def _desktop_rows(self) -> list[dict[str, Any]]:
        """Every Claude Desktop conversation (JSON files)."""
        conversations = []
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
        return conversations

    def _cowork_rows(self, full_content: bool) -> list[dict[str, Any]]:
        """Every Cowork session."""
        # Load Cowork conversations: local-agent-mode-sessions audit.jsonl
        # plus remote (cloud task) sessions archived by the fetcher. A list
        # needs only summaries, which are cached; search and export need the
        # full sessions.
        if full_content:
            from .cowork_reader import list_cowork_conversations
            return list_cowork_conversations(
                self.cowork_root, self.remote_cowork_root, self._cowork_archive_roots()
            )
        return self._cowork_summaries()

    # The archive copies (machines/<id>/...) of every machine. A copy shows
    # only when its live file is gone (AGENTS.md, guiding principle 2).
    def _cc_archive_roots(self) -> list[Path]:
        return archive_roots(self.data_dir, "claude-code")

    def _cowork_archive_roots(self) -> list[Path]:
        return archive_roots(self.data_dir, "cowork")

    def _cc_session_files(self) -> list[Path]:
        """The session files: live, then the archive copies of the other uuids."""
        roots = (self.claude_dir, *self._cc_archive_roots())
        return pick_copies([discover_jsonl_files(r) for r in roots], key=lambda p: p.stem)

    def _cc_agent_files(self) -> list[Path]:
        """The agent files: live, then the archive copies of the other agents."""
        roots = (self.claude_dir, *self._cc_archive_roots())
        return live_first([iter_agent_files(r) for r in roots], key=_agent_key)

    def _discover_claude_code(self) -> list[Path]:
        return self._cc_session_files()

    def _discover_cowork(self) -> list[Path]:
        """The ``audit.jsonl`` of each session; it shares ``.jsonl`` with Claude Code."""
        from .cowork_reader import iter_cowork_session_dirs

        audits = (
            d / "audit.jsonl"
            for d in iter_cowork_session_dirs(
                self.cowork_root, self.remote_cowork_root, self._cowork_archive_roots()
            )
        )
        return [a for a in audits if a.exists()]

    @staticmethod
    def _load_claude_code_for_index(path: Path) -> dict[str, Any] | None:
        # Not the FileCache: the index reads every file once, and the read
        # also captures the session's images (cache_all_markers).
        from . import claude_code_reader

        return claude_code_reader.read_claude_code_conversation(path)

    @staticmethod
    def _load_cowork_for_index(path: Path) -> dict[str, Any] | None:
        from . import cowork_reader

        return cowork_reader.read_cowork_conversation(path.parent)

    def _cowork_summaries(self) -> list[dict[str, Any]]:
        """Cached summaries of every Cowork session; see the cache notes above."""
        from . import cowork_reader

        archives = self._cowork_archive_roots()
        roots = [r for r in (self.cowork_root, self.remote_cowork_root, *archives) if r is not None]
        out: list[dict[str, Any]] = []
        live: set[Path] = set()
        for session_dir in cowork_reader.iter_cowork_session_dirs(
            self.cowork_root, self.remote_cowork_root, archives
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
        source: SourceFilter = "all",
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
        clear_label_memo()
        conversations = []
        adapters = {a.descriptor().id: a for a in get_adapters(self)}

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

            conversations.append(
                self._make_summary(data, include_subagents=include_subagents, adapters=adapters)
            )
        self._set_states(conversations)

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

        Returns (data, file_path) tuple. The sources are asked in
        registry order; the first hit wins.
        """
        for adapter in get_adapters(self):
            hit = adapter.find(uuid)
            if hit is not None:
                return hit
        return None, None

    def _find_desktop(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        for path in self._get_conversation_files():
            data = self._load_conversation(path)
            if data and data.get("uuid") == uuid:
                return data, path
        return None

    def _find_claude_code(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        """Find a Claude Code conversation (JSONL files)."""
        # Route through the FileCache wrapper (_load_conversation_cached)
        # so warm detail-page and export calls don't re-parse the entire
        # JSONL on every request.
        # The Desktop branch already uses self._load_conversation
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
        cc_files = self._cc_session_files()

        for jsonl_path in cc_files:
            if jsonl_path.stem == uuid:
                data = _load_conversation_cached(jsonl_path)
                if data and data.get("uuid") == uuid:
                    return data, jsonl_path

        # Pass B: scan via summary cache (warm: in-memory bulk SELECT;
        # cold: misses repopulate as a side effect — acceptable since
        # detail-page misses are user-driven, not bulk).
        from .cc_jsonl_io import read_conversation_summary_fast
        from .summary_cache import get_summary_cache

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
        return None

    def _find_cowork(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        """Find a Cowork session; the path is its ``audit.jsonl``."""
        # Local (``local_<uuid>``) and remote
        # (``remote_<id>``) sessions. ``find_cowork_session_dirs`` checks one candidate
        # per org dir, so this resolution is O(orgs), not O(sessions). Try
        # every copy: the first one can be empty or have no turns while
        # the list shows the row from another copy. Of two readable copies,
        # open the newest, which is the one the list shows (newest_per_uuid).
        from .cowork_reader import (
            find_cowork_session_dirs,
            read_cowork_conversation,
            updated_at_key,
        )
        found: tuple[dict[str, Any], Path] | None = None
        for candidate in find_cowork_session_dirs(
            self.cowork_root, self.remote_cowork_root, uuid, self._cowork_archive_roots()
        ):
            data = read_cowork_conversation(candidate)
            if not data or data.get("uuid") != uuid:
                continue
            if found is None or updated_at_key(data) > updated_at_key(found[0]):
                found = (data, candidate)
        if found is not None:
            return found[0], found[1] / "audit.jsonl"
        return None

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
        # no parentUuid links, no branches, no leaf-walking. The same
        # render path applies to every chronological source.
        is_chronological = _is_chronological(source)

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

        conversation_model = _conversation_model(data)
        messages = [_parse_message(m, conversation_model) for m in branch]
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
            source_name=data.get("source_name"),
            source_home=data.get("source_home"),
            source_home_abs=data.get("source_home_abs"),
            notices=data.get("notices") or [],
            parent_uuid=data.get("parent_uuid"),
            child_uuids=data.get("child_uuids") or [],
            agent_id=data.get("agent_id"),
            agent_description=data.get("agent_description"),
            **self._machine_fields(data, file_path),
        )

    # ----- Subagent transcripts (issue #96) -----

    def _claude_code_session_paths(self, uuid: str) -> list[Path]:
        """The session files whose name is ``uuid``, live first; a comparison, never a built path."""
        roots = (self.claude_dir, *self._cc_archive_roots())
        return [p for r in roots for p in discover_jsonl_files(r) if p.stem == uuid]

    def _claude_code_session_path(self, uuid: str) -> Path | None:
        return next(iter(self._claude_code_session_paths(uuid)), None)

    def find_agent(self, uuid: str, agent_id: str) -> tuple[Path, AgentFile] | None:
        """``(session file, agent)`` of the Claude Code session ``uuid``, through discovery.

        A live session can have lost an agent file that its archive copy keeps.
        """
        if not is_valid_agent_id(agent_id):
            return None
        for session_file in self._claude_code_session_paths(uuid):
            agent = find_agent(session_file, agent_id)
            if agent is not None:
                return session_file, agent
        return None

    def get_agent_conversation(self, uuid: str, agent_id: str) -> ConversationDetail | None:
        """The agent transcript as a ``ConversationDetail`` with ``uuid`` = the session's."""
        clear_label_memo()
        found = self.find_agent(uuid, agent_id)
        if found is None:
            return None
        session_file, agent = found
        data = read_agent_conversation(session_file, agent)
        if data is None:
            return None
        detail = self._build_detail_from_data(data, agent.path, None)
        link_detail_model(detail, self._peer_links(detail.uuid, detail.agent_id, detail.source))
        detail.state = self.state_for(detail.source, detail.updated_at, agent.path, None)
        return detail

    def get_agent_conversations_raw(self, source: SourceFilter = "all") -> list[dict[str, Any]]:
        """Every agent transcript with an owner, in the session identity (linear search)."""
        if source not in ("all", "CLAUDE_CODE"):
            return []
        out = []
        for path in self._cc_agent_files():
            owner = owner_of(path)
            data = read_agent_conversation(*owner) if owner is not None else None
            if data is not None:
                out.append(data)
        return out

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
        clear_label_memo()
        data, file_path = self._find_conversation_data(uuid)
        if not data:
            return None
        detail = self._build_detail_from_data(data, file_path, leaf_override)
        if detail.source == "CLAUDE_CODE" and file_path is not None:
            detail.subagents_found = [
                SubagentFound(**a) for a in subagents_found(file_path)
            ]
        link_detail_model(detail, self._peer_links(detail.uuid, detail.agent_id, detail.source))
        detail.state = self.state_for(
            detail.source, detail.updated_at, file_path, None if detail.agent_id else detail.uuid
        )
        return detail

    def get_conversation_dict(
        self, uuid: str, leaf_override: str | None = None
    ) -> dict[str, Any] | None:
        """:meth:`_get_conversation_dict_cached`, plus a session's ``subagents_found``.

        Discovery runs on each call, outside the dict cache: a new agent file
        does not change the session file's mtime.
        """
        detail = self._get_conversation_dict_cached(uuid, leaf_override)
        if detail and detail.get("machine_id"):
            # A label can change after the dict was cached (#92).
            clear_label_memo()
            old, fresh = detail.get("machine_label"), shown_labels(self.data_dir).get(detail["machine_id"])
            if fresh and fresh != old:
                labels = [fresh if x == old else x for x in detail.get("machine_labels") or []]
                detail = {**detail, "machine_label": fresh, "machine_labels": labels}
        if detail and detail.get("source") == "CLAUDE_CODE" and detail.get("file_path"):
            detail = {**detail, "subagents_found": subagents_found(Path(detail["file_path"]))}
        if not detail:
            return detail
        detail = link_detail_dict(detail, self._peer_links(detail["uuid"], detail.get("agent_id"), detail.get("source")))
        # Outside the dict cache: the state changes with time and the live registry.
        state = self.state_for(
            detail.get("source"), _parse_datetime(detail.get("updated_at")),
            Path(detail["file_path"]) if detail.get("file_path") else None,
            None if detail.get("agent_id") else detail["uuid"],
        )
        return detail if detail.get("state") == state else {**detail, "state": state}

    # ----- Session state (issue #91, backend/session_state.py) -----

    def state_for(
        self, source: str | None, updated_at: datetime, cc_path: Path | None,
        running_uuid: str | None, running_ids: set[str] | None = None, now: datetime | None = None,
    ) -> SessionState | None:
        """The state of one session. ``running_uuid``: the uuid to look up in the live
        registry (None for an agent transcript)."""
        if source in (None, "CLAUDE_AI"):
            return None
        now = now or session_state.now()
        if source != "CLAUDE_CODE":
            return state_of(ended=False, last_activity=updated_at, now=now)
        if running_ids is None:
            running_ids = live_session_ids(self.claude_dir) if running_uuid else set()
        return state_of(
            ended=cc_path is not None and cc_file_ended(cc_path),
            last_activity=updated_at, now=now, running=running_uuid in running_ids,
        )

    def _set_states(self, rows: list[ConversationSummary]) -> None:
        """The state of the Claude Code rows (the others have it from ``_make_summary``)."""
        cc = [r for r in rows if r.source == "CLAUDE_CODE"]
        if not cc:
            return
        now = session_state.now()
        paths = {p.stem: p for p in self._cc_session_files()}
        running = live_session_ids(self.claude_dir)
        for r in cc:
            r.state = self.state_for(r.source, r.updated_at, paths.get(r.uuid), r.uuid, running, now)

    def _peer_links(self, uuid: str, agent_id: str | None, source: str | None) -> dict | None:
        """Issue #91: the links between sessions, from the search index; outside the
        dict cache, because they change when the other session changes. None: the
        index cannot answer (no index, the cloud-sync gate is closed, a read error)."""
        if source not in ("CLAUDE_CODE", "CODEX"):
            return {}
        from .search_index import get_search_index, index_key

        idx = get_search_index()
        if idx is None:
            return None
        try:
            own, others = idx.peer_rows(index_key(uuid, agent_id))
        except sqlite3.Error:
            logger.warning("peer links: index read failed for %s", uuid, exc_info=True)
            return None
        if not own:
            return {}
        same_tree = (lambda _u: True) if source != "CODEX" else self._codex_tree_test(uuid)
        return compute_links(own, others, same_tree)

    # ----- The Messages tab (issue #91) -----

    def peer_graph(self, center: str | None, hops: int | None, subagents: bool) -> PeerGraph | None:
        """The sessions and messages of the Messages tab. None: ``center`` is unknown.

        ``center=None``: every running or open session, and the messages whose
        two ends are in that set. Else the sessions at most ``hops`` messages
        from ``center`` (None = the connected group), both directions, and
        every message between them. A peer that is not in the archive is a
        session "name:<peer name>" (hops scope only); the search never goes on
        from it, because a name is no link. ``subagents=False``: an agent's
        messages count for its main session.
        """
        from .search_index import get_search_index, index_key

        rows = self.list_conversations()
        by_uuid = {r.uuid: r for r in rows}
        if center is not None and center not in by_uuid and self._find_conversation_data(center)[0] is None:
            return None
        idx = get_search_index()
        peer_rows: list[dict[str, Any]] = []
        if idx is not None:
            try:
                peer_rows = idx.peer_graph_rows()
            except sqlite3.Error:
                logger.warning("peer graph: index read failed", exc_info=True)
                idx = None

        def key_of(r: dict[str, Any]) -> str:
            return index_key(r["session_uuid"], r["agent_id"] or None) if subagents else r["session_uuid"]

        roots = self._codex_roots() if any(r["msg_id"].startswith("cx") for r in peer_rows) else None
        edges, names, titles = _peer_edges(peer_rows, key_of, roots)

        # Subagents: every agent transcript of a session in scope (both layouts),
        # also one that sent no message. Found when the scope reaches its session.
        agents: dict[str, dict[str, Any]] = {}
        agent_keys: dict[str, list[str]] = {}
        session_files: dict[str, Path] = {}

        def agents_of(uuid: str) -> list[str]:
            if not subagents or "::agent::" in uuid or uuid.startswith("name:"):
                return []
            if uuid not in agent_keys:
                if not session_files:
                    session_files.update({p.stem: p for p in self._cc_session_files()})
                path = session_files.get(uuid)
                keys = []
                for a in discover_agents(path) if path else []:
                    key = index_key(uuid, a.agent_id)
                    try:
                        when: datetime | None = datetime.fromtimestamp(a.path.stat().st_mtime, UTC)
                    except OSError:
                        when = None
                    agents[key] = {"agent_id": a.agent_id, "title": agent_display_name(a), "updated_at": when}
                    keys.append(key)
                    edges.append({"msg_id": None, "kind": "subagent", "from_key": uuid, "to_key": key,
                                  "timestamp": when, "text": a.description})
                agent_keys[uuid] = keys
            return agent_keys[uuid]

        hop: dict[str, int | None] = {}
        via: dict[str, str | None] = {}
        if center is None:
            for r in rows:
                if r.state in ("running", "open"):
                    for k in (r.uuid, *agents_of(r.uuid)):
                        hop[k], via[k] = None, None
        else:
            adj: dict[str, set[str]] = {}
            for e in edges:
                adj.setdefault(e["from_key"], set()).add(e["to_key"])
                adj.setdefault(e["to_key"], set()).add(e["from_key"])

            def neighbors(k: str) -> set[str]:
                main, _, agent_id = k.partition("::agent::")
                return adj.get(k, set()) | set(agents_of(k)) | ({main} if agent_id else set())

            hop[center], via[center] = 0, None
            frontier = [center]
            while frontier and (hops is None or hop[frontier[0]] < hops):
                nxt = []
                for k in frontier:
                    if k.startswith("name:"):
                        continue
                    for n in sorted(neighbors(k)):
                        if n not in hop:
                            hop[n], via[n] = hop[k] + 1, k
                            nxt.append(n)
                frontier = nxt
        scoped = [e for e in edges if e["from_key"] in hop and e["to_key"] in hop]
        if idx is not None:
            scoped += self._idle_edges(idx, set(hop), subagents)
        self._edge_facts(idx, scoped)
        def when(e: dict[str, Any]) -> datetime:
            t = e["timestamp"] or datetime.min
            return t if t.tzinfo else t.replace(tzinfo=UTC)

        scoped.sort(key=lambda e: (e["timestamp"] is None, when(e), e["msg_id"] or ""))

        # Each session's first message by its own record time (clocks can differ).
        first: dict[str, tuple[datetime, str | None]] = {}
        never = datetime.max.replace(tzinfo=UTC)
        for e in scoped:
            if e["kind"] != "message":
                continue
            for key, when, other in ((e["to_key"], e.get("to_time"), e["from_key"]),
                                     (e["from_key"], e.get("from_time"), None)):
                when = when or e["timestamp"] or never
                if key not in first or when < first[key][0]:
                    first[key] = (when, other)
        parent = {k: v[1] for k, v in first.items()}
        sessions = [self._graph_session(k, hop[k], via[k], parent.get(k), by_uuid, agents, names, titles)
                    for k in hop]
        if center is None:
            sessions.sort(key=lambda s: (s.updated_at or datetime.min.replace(tzinfo=UTC), s.key), reverse=True)
        else:
            sessions.sort(key=lambda s: (s.hop or 0, s.key))
        return PeerGraph(
            scope="active" if center is None else "hops", center_key=center, hops=hops, subagents=subagents,
            sessions=sessions,
            messages=[PeerGraphMessage(**{k: e.get(k) for k in PeerGraphMessage.model_fields}) for e in scoped],
        )

    def _graph_session(
        self, key: str, hop: int | None, via: str | None, parent: str | None,
        by_uuid: dict[str, ConversationSummary], agents: dict[str, dict[str, Any]],
        names: dict[str, str], titles: dict[str, str],
    ) -> PeerGraphSession:
        common = {"key": key, "hop": hop, "via_key": via, "derived_parent_key": parent, "name": names.get(key)}
        if key.startswith("name:"):
            return PeerGraphSession(**{**common, "name": key[5:]}, in_archive=False)
        uuid, _, agent_id = key.partition("::agent::")
        row = by_uuid.get(uuid)
        if agent_id:
            a = agents.get(key) or {}
            when = a.get("updated_at")
            return PeerGraphSession(
                **common, uuid=uuid, agent_id=agent_id, title=titles.get(key) or a.get("title"),
                source="CLAUDE_CODE", project_name=row.project_name if row else None,
                project_path=row.project_path if row else None, updated_at=when,
                state=self.state_for("CLAUDE_CODE", when, None, None) if when else None,
            )
        if row is None:
            return PeerGraphSession(**common, uuid=uuid, title=titles.get(key))
        return PeerGraphSession(
            **common, uuid=uuid, title=row.name or titles.get(key), source=row.source,
            project_name=row.project_name, project_path=row.project_path, state=row.state,
            updated_at=row.updated_at,
        )

    def _idle_edges(self, idx: Any, keys: set[str], subagents: bool) -> list[dict[str, Any]]:
        """Idle notices of the sessions in ``keys``, linked through the asker's own
        notify_when_idle send (``link_detail_dict``)."""
        out: list[dict[str, Any]] = []
        try:
            notices = idx.idle_notice_messages()
        except sqlite3.Error:
            logger.warning("peer graph: idle notices read failed", exc_info=True)
            return out
        wanted: dict[str, set[str]] = {}
        for conv, mu in notices:
            if "::agent::" not in conv and conv in keys:
                wanted.setdefault(conv, set()).add(mu)
        for conv, mus in wanted.items():
            detail = self.get_conversation_dict(conv) or {}
            for m in detail.get("messages") or []:
                ref = m.get("peer_ref") or {}
                if m.get("uuid") not in mus or ref.get("kind") != "idle_notice":
                    continue
                other = ref.get("conversation_uuid") or (f"name:{ref['name']}" if ref.get("name") else None)
                if other in keys:
                    out.append({"msg_id": None, "kind": "idle_notice", "from_key": other, "to_key": conv,
                                "from_conv": None, "from_message_uuid": None, "to_conv": conv,
                                "to_message_uuid": m.get("uuid"), "timestamp": _parse_datetime(m.get("created_at")),
                                "text": str(m.get("text") or "").strip().splitlines()[0][:200] or None})
        return out

    def _edge_facts(self, idx: Any, edges: list[dict[str, Any]]) -> None:
        """The time and text of each message edge, from the search index (in place)."""
        wanted: dict[str, set[str]] = {}
        for e in edges:
            if e["kind"] == "message":
                for side in ("from", "to"):
                    if e.get(f"{side}_conv") and e.get(f"{side}_message_uuid"):
                        wanted.setdefault(e[f"{side}_conv"], set()).add(e[f"{side}_message_uuid"])
        facts: dict[tuple[str, str], tuple[str, str]] = {}
        if idx is not None and wanted:
            try:
                facts = idx.message_facts(wanted)
            except sqlite3.Error:
                logger.warning("peer graph: message facts read failed", exc_info=True)
        for e in edges:
            if e["kind"] != "message":
                continue
            sent = facts.get((e.get("from_conv"), e.get("from_message_uuid")))
            got = facts.get((e.get("to_conv"), e.get("to_message_uuid")))
            e["from_time"] = _parse_datetime(sent[0]) if sent and sent[0] else None
            e["to_time"] = _parse_datetime(got[0]) if got and got[0] else None
            e["timestamp"] = e["from_time"] or e["to_time"]
            # The received row holds the body; a Codex send call holds it too.
            text = got[1] if got else (sent[1] if sent and (e["msg_id"] or "").startswith("cx") else None)
            e["text"] = text.strip() or None if text else None
        self._send_texts([e for e in edges if e["kind"] == "message" and e["text"] is None and e.get("from_block")])

    def _send_texts(self, edges: list[dict[str, Any]]) -> None:
        """The text of a send whose receiver is not in the archive: the SendMessage
        input in the sender's own transcript (the index keeps only tool text)."""
        by_conv: dict[str, list[dict[str, Any]]] = {}
        for e in edges:
            by_conv.setdefault(e["from_conv"], []).append(e)
        for conv, group in by_conv.items():
            uuid, _, agent_id = conv.partition("::agent::")
            if agent_id:
                agent = self.get_agent_conversation(uuid, agent_id)
                messages = agent.model_dump()["messages"] if agent else []
            else:
                messages = (self.get_conversation_dict(uuid) or {}).get("messages") or []
            inputs = {
                b.get("id"): b.get("input")
                for m in messages for b in m.get("content") or []
                if b.get("type") == "tool_use" and isinstance(b.get("input"), dict)
            }
            for e in group:
                inp = inputs.get(e["from_block"]) or {}
                body = inp.get("message") if isinstance(inp.get("message"), str) else inp.get("content")
                e["text"] = body.strip() or None if isinstance(body, str) else None

    def _codex_roots(self):
        """A function: the root thread of a Codex thread."""
        adapter = get_adapter_or_none("CODEX", self)
        rows = adapter.list_rows(full_content=False, include_phantom=True) if adapter else []
        parent = {r["uuid"]: r.get("parent_uuid") for r in rows if r.get("uuid")}

        def root(u: str) -> str:
            seen = set()
            while parent.get(u) and u not in seen:
                seen.add(u)
                u = parent[u]
            return u

        return root

    def _codex_tree_test(self, uuid: str):
        """A test: is a Codex thread in the same thread tree as ``uuid``?"""
        root = self._codex_roots()
        mine = root(uuid)
        return lambda other: root(other) == mine

    def _get_conversation_dict_cached(
        self, uuid: str, leaf_override: str | None = None
    ) -> dict[str, Any] | None:
        """Get a conversation as a ``model_dump(mode='json')`` dict, cached.

        W3+W4 fast path for the /api/conversations/<uuid> route. The
        returned dict is suitable for direct hand-off to OrjsonResponse,
        bypassing both Pydantic rebuild AND FastAPI's default encoder.

        Caching:
          * Cache key: ``(uuid, file path)``; entry value: ``(mtime, dict)``.
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
            # A source whose detail reads other files (Codex) gives its own key.
            adapter = get_adapter_or_none(data.get("source") or "CLAUDE_AI", self)
            token = getattr(adapter, "cache_token", None)
            current_mtime = token(file_path) if token else file_path.stat().st_mtime
        except OSError:
            # Race: file vanished. Build once, don't cache.
            detail = self._build_detail_from_data(data, file_path, None)
            return detail.model_dump(mode="json")

        key = (uuid, str(file_path))
        with _DETAIL_DICT_CACHE_LOCK:
            hit = _DETAIL_DICT_CACHE.get(key)
            if hit is not None and hit[0] == current_mtime:
                # Cache HIT — promote to MRU.
                _DETAIL_DICT_CACHE.move_to_end(key)
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
            existing = _DETAIL_DICT_CACHE.get(key)
            if existing is not None and existing[0] == current_mtime:
                _DETAIL_DICT_CACHE.move_to_end(key)
                return existing[1]
            # Pop-then-reinsert is the canonical MRU promotion pattern for
            # OrderedDict (reassignment alone does NOT reposition).
            if key in _DETAIL_DICT_CACHE:
                del _DETAIL_DICT_CACHE[key]
            _DETAIL_DICT_CACHE[key] = (current_mtime, dumped)
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
        is_chronological = _is_chronological(source)

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
        root_messages = build_message_tree(chat_messages, _conversation_model(data))

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
        source: SourceFilter = "all",
    ) -> list[dict[str, Any]]:
        """Get all raw conversation data for search/export (includes full message content)."""
        return self._get_all_conversations_data(source, full_content=True)

    def count_conversations(
        self,
        source: SourceFilter = "all",
    ) -> int:
        """Count total number of conversations."""
        return len(self._get_all_conversations_data(source))