"""Full-text search implementation.

Two paths:
  * **FTS5 fast path** (preferred). When :mod:`backend.search_index` reports
    the index is ready and FTS5 is available, queries hit the SQLite FTS5
    inverted index. The index returns ``(conv_uuid, message_uuid)`` pairs;
    we then walk only those conversations from the cache and run the same
    snippet/sort code as the linear path. Latency target: <50 ms per query.
  * **Linear-scan fallback**. When the index isn't ready (initial build
    still in progress), FTS5 isn't compiled into the local sqlite3 build,
    or any sqlite3 error fires, we fall back to the original full-walk
    code path. Search never goes "down".

The two paths produce byte-for-byte identical ``SearchResult`` objects for
whole-word queries (the common case). Sub-string queries that don't align
with token boundaries (e.g., ``"py"`` matching the substring inside
``"happy"``) are a documented behavior change: the FTS5 path returns
prefix-matches only.
"""

import logging
import re
import sqlite3
from typing import Any, Literal

from .compact_prefixes import is_compaction_prefix_text
from .models import MessageSnippet, SearchResponse, SearchResult, SnippetFragment
from .search_text import (
    _TOOL_PLACEHOLDER_RE,
    _extract_searchable_text,
    _is_compact_trigger_message,
    _stringify_tool_input,
)
from .store import ConversationStore, _parse_datetime

# Re-exports for backwards compatibility — multiple test modules import these
# directly from ``backend.search`` (e.g. ``from backend.search import
# _extract_searchable_text``). The canonical definitions live in
# ``backend.search_text`` (the stdlib-only leaf created to break the
# search.py ↔ search_index.py import cycle); we re-bind them here so the
# existing import path continues to work byte-for-byte.
__all__ = [
    "_TOOL_PLACEHOLDER_RE",
    "_extract_searchable_text",
    "_stringify_tool_input",
]


logger = logging.getLogger(__name__)


SNIPPET_CONTEXT = 150  # Characters of context on each side of the match (~3 lines total)
WORD_BOUNDARY_SEARCH = 25  # Max chars to extend outward to avoid cutting mid-word


def parse_user_query(query: str) -> tuple[str | None, list[str]]:
    """Split a free-form user query into (phrase, tokens).

    Returns:
      * Phrase mode: ``("foo bar", ["foo bar"])`` when the user's entire query
        is wrapped in matching double quotes — exact-phrase semantics.
      * Token mode: ``(None, ["foo", "bar", "baz"])`` when the query is
        unquoted whitespace-separated — AND-of-tokens semantics.
      * Single token: ``(None, ["foo"])`` — same as token mode with one term.
      * Empty: ``(None, [])`` — caller skips search.

    The phrase detection is intentionally narrow (entire query quoted, no
    mixed `foo "bar baz"` syntax). That keeps the predicate stable and
    matches user expectations: quotes mean "the whole thing is literal".

    Why no boundary characters in the returned tokens: the snippet regex
    built downstream (see ``_make_snippet_regex``) deliberately avoids
    ``\\b`` boundaries because the FTS5 index uses the ``porter`` stemmer +
    ``unicode61 remove_diacritics 1`` tokenizer (search_index.py:121).
    FTS5 matches ``running`` for query ``run`` and ``café`` for ``cafe``;
    a Python regex with ``\\b`` would fail to find those tokens in the
    raw message text and silently drop FTS5 hits. The fallback path in
    the callers handles that drift by emitting a 0-length highlight when
    no Python match is found.
    """
    stripped = query.strip()
    if not stripped:
        return None, []
    # Phrase mode: starts and ends with " (and has at least 3 chars so we
    # don't treat a single empty-string "" as a phrase).
    if len(stripped) >= 3 and stripped[0] == '"' and stripped[-1] == '"':
        inner = stripped[1:-1].strip()
        if inner:
            return inner, [inner]
    return None, stripped.split()


def _make_snippet_regex(phrase: str | None, tokens: list[str]) -> "re.Pattern[str] | None":
    """Build the regex used to locate a highlight position in a message.

    Phrase mode → literal escaped phrase. Token mode (>=1 token) → an
    alternation ``(t1|t2|...)`` matching ANY token. No word boundaries —
    see ``parse_user_query`` for the stemmer-drift rationale.

    Returns ``None`` when there are no tokens (caller short-circuits).
    """
    if phrase is not None:
        return re.compile(re.escape(phrase), re.IGNORECASE)
    if not tokens:
        return None
    return re.compile(
        "|".join(re.escape(t) for t in tokens),
        re.IGNORECASE,
    )


# Default length of the leading-text fallback snippet emitted when the
# Python regex can't find a query token in an FTS5-matched message body
# (stemmer/diacritic drift). 300 chars ≈ 6 lines of prose — enough for
# the user to recognize what they matched even without a yellow <mark>.
_FALLBACK_SNIPPET_LEN = 300


# Lookahead window for the /compact-trigger-row mapping. Matches
# ``backend.cc_image_markers._COMPACT_LOOKAHEAD = 8`` by design. Kept
# independent (NOT imported) to preserve the leaf-module layering — a
# behavioral coupling test in
# backend/tests/test_search_compact_trigger_rewrite.py
# (test_build_compact_trigger_uuid_map_respects_lookahead_window) pins
# the two constants in lockstep so they cannot silently drift.
_COMPACT_TRIGGER_LOOKAHEAD = 8


def _build_compact_trigger_uuid_map(conv: dict[str, Any]) -> dict[str, str]:
    """Map each ``/compact`` trigger row's UUID to its compact-marker UUID.

    Background: a manual ``/compact`` produces TWO related user messages in
    ``conv['chat_messages']``: the synthetic ``isCompactSummary: True`` row
    (the LLM's compaction summary, represented in ``conv['compact_markers']``
    as ``{message_uuid, summary_text, kind: 'manual', user_prompt, ...}``)
    and a SECOND user row carrying the ``<command-name>/compact</command-name>``
    envelope plus the verbatim user prompt inside ``<command-args>``. The
    trigger row's UUID is what FTS5 returns when the user searches for
    text from their own prompt (pre-v11 indexing exposed the trigger
    row's body to the index). The frontend's compact-marker auto-expand
    chain keys on the marker's UUID, not the trigger's — so a search hit
    on the trigger UUID prevents the marker from auto-expanding.

    This helper builds the trigger→marker mapping the scatter-gather
    body-emit code uses to REWRITE any latent trigger-row hits into the
    correct marker UUID. Belt-and-suspenders for two drift modes:

      * the SQLite FTS5 index hasn't completed the v10→v11 rebuild yet
        (so it still carries stale trigger-row body tokens); and
      * the linear-scan fallback path, which inherits the index-time
        exclusion via ``_extract_searchable_text`` BUT still benefits
        from rewrite if a future code path re-introduces trigger-row
        text into search without bumping SCHEMA_VERSION.

    Algorithm: for each marker in ``conv['compact_markers']``, locate the
    marker's row in ``chat_messages`` by message_uuid; then scan forward
    up to :data:`_COMPACT_TRIGGER_LOOKAHEAD` messages looking for the
    trigger row (identified by :func:`_is_compact_trigger_message`). The
    scan window matches
    :data:`backend.cc_image_markers._COMPACT_LOOKAHEAD` exactly — the
    same window that pass uses to classify a marker as ``manual``. If
    no trigger is found in window, the marker is auto-compact (no
    user prompt to map). Silent skip rather than crash or guess.

    Returns ``{}`` for conversations with no compact markers (the common
    case — Desktop conversations and most CC sessions). The helper is
    pure-functional and does NOT mutate the input conversation.

    Idempotency: callers apply ``trigger_to_marker.get(uuid, uuid)`` at
    emit sites. The values in the returned dict are NEVER themselves
    keys (a marker UUID cannot also be a trigger UUID — different
    JSONL rows, distinct UUIDs), so multiple applications of the
    mapping are safe.
    """
    markers = conv.get("compact_markers") or []
    if not markers:
        return {}
    messages = conv.get("chat_messages") or []
    if not messages:
        return {}

    # Index messages by uuid for O(1) marker-row lookup. We walk the
    # message list once even for multiple markers; the per-marker scan
    # then jumps directly to the marker's position and reads at most
    # _COMPACT_TRIGGER_LOOKAHEAD messages forward.
    uuid_to_index: dict[str, int] = {}
    for i, msg in enumerate(messages):
        u = msg.get("uuid")
        if isinstance(u, str) and u:
            uuid_to_index[u] = i

    mapping: dict[str, str] = {}
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        marker_uuid = marker.get("message_uuid")
        if not isinstance(marker_uuid, str) or not marker_uuid:
            continue
        start = uuid_to_index.get(marker_uuid)
        if start is None:
            continue
        # Scan forward through the lookahead window. Mirrors
        # cc_image_markers.extract_compact_markers, which uses
        # ``range(idx + 1, min(len(entries), idx + 1 + _COMPACT_LOOKAHEAD))``
        # against the raw JSONL entries. We scan the same window over
        # ``chat_messages``; the post-parse message list is in the same
        # order as the raw entries (store._parse_message preserves
        # order), so the window semantics carry over.
        end = min(len(messages), start + 1 + _COMPACT_TRIGGER_LOOKAHEAD)
        for j in range(start + 1, end):
            cand = messages[j]
            if not isinstance(cand, dict):
                continue
            if _is_compact_trigger_message(cand):
                trigger_uuid = cand.get("uuid")
                if isinstance(trigger_uuid, str) and trigger_uuid:
                    mapping[trigger_uuid] = marker_uuid
                break
    return mapping


def create_snippet(text: str, match_start: int, match_end: int) -> tuple[str, int, int]:
    """Create a snippet with context around the match.

    Extends outward to word boundaries (up to WORD_BOUNDARY_SEARCH chars)
    so we don't cut mid-word. Falls back to the raw char boundary if no
    whitespace is nearby — the ellipsis prefix/suffix signals the cut.
    """
    snippet_start = max(0, match_start - SNIPPET_CONTEXT)
    snippet_end = min(len(text), match_end + SNIPPET_CONTEXT)

    # Extend snippet_start LEFTWARD to a word boundary (keeps the preceding word intact)
    if snippet_start > 0:
        extended = max(0, snippet_start - WORD_BOUNDARY_SEARCH)
        space_pos = text.rfind(" ", extended, snippet_start + 1)
        if space_pos >= 0:
            snippet_start = space_pos + 1

    # Extend snippet_end RIGHTWARD to a word boundary (keeps the following word intact)
    if snippet_end < len(text):
        extended = min(len(text), snippet_end + WORD_BOUNDARY_SEARCH)
        space_pos = text.find(" ", snippet_end - 1, extended)
        if space_pos >= 0:
            snippet_end = space_pos

    snippet = text[snippet_start:snippet_end]

    # Add ellipsis if truncated
    prefix = "..." if snippet_start > 0 else ""
    suffix = "..." if snippet_end < len(text) else ""

    # Adjust match positions for the snippet
    new_match_start = len(prefix) + (match_start - snippet_start)
    new_match_end = new_match_start + (match_end - match_start)

    return prefix + snippet + suffix, new_match_start, new_match_end


def _derive_project_name(project_path: str | None) -> str | None:
    """Mirror ConversationSummary.model_post_init project_name derivation."""
    if not project_path:
        return None
    stripped = project_path.rstrip("/")
    return stripped.split("/")[-1] if "/" in stripped else stripped


SortField = Literal["updated_at", "created_at", "name", "project"]
SortOrder = Literal["asc", "desc"]


def search_conversations(
    store: ConversationStore,
    query: str,
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
    context_size: Literal["snippet", "full"] = "snippet",
    sort: SortField = "updated_at",
    sort_order: SortOrder = "desc",
    conversation_uuid: str | None = None,
    project_path: str | None = None,
    bookmarks: set[str] | None = None,
    include_tool_calls: bool = True,
    include_compactions: bool = True,
    organization_id: str | None = None,
    conversation_uuids: set[str] | None = None,
    limit: int = 1000,
) -> SearchResponse:
    """Search across all conversations for matching messages.

    Dispatches to the FTS5 fast path when the index is ready (see module
    docstring); falls back to the linear-scan path on any failure mode
    (index not ready, FTS5 unavailable, sqlite3 error). Both paths produce
    byte-for-byte identical ``SearchResult`` objects for whole-word
    queries.

    Scope filters (manual finding 2026-05-04 + sidebar-scope propagation
    2026-05-14):
      - ``conversation_uuid``: restrict to a single conversation. Most
        specific filter; wins over ``project_path`` / ``bookmarks`` /
        ``conversation_uuids`` when more than one is passed.
      - ``project_path``: restrict to conversations whose project_path
        matches exactly (CC sessions grouped by their cwd).
      - ``bookmarks``: restrict to a set of conversation UUIDs (the
        client passes the bookmark set when the sidebar's Starred filter
        is active).
      - ``organization_id`` (sidebar Workspace dropdown, 2026-05-14):
        restrict to conversations whose organization_id matches exactly.
        ``None`` matches only None on the conv side — a UUID filter never
        incidentally surfaces untagged data (mirrors
        ``ConversationStore.list_conversations``).
      - ``conversation_uuids`` (active-filter set, 2026-05-14): restrict
        to a set of UUIDs. The frontend computes this from the active
        filter graph (atoms/groups under
        ``frontend/src/lib/filterEngine.ts``); MCP does not use this.
        ``None`` means "no constraint"; the empty set means "filter
        excludes everything" — caller short-circuits to ``[]``.

    All filters AND-compose with each other and with the existing
    ``source`` filter. Backend-side because tool_use / tool_result
    payloads are large; client-side post-filtering would waste bandwidth
    and break ranking.

    ``include_tool_calls`` (2026-05-11): when False, search ignores
    tool_use / tool_result / thinking content. Hit messages whose only
    matching text lives in those blocks are silently dropped — the
    sidebar should only show results the user can navigate to. The FTS5
    index itself is still built over the full text; the filter is applied
    at scatter/snippet time so toggling the setting doesn't require a
    rebuild.

    ``include_compactions`` (2026-05-26): when False, search ignores
    hits whose match falls inside an ``isCompactSummary`` row body. This
    is the wire-side mirror of the "Show Compactions" checkbox in the
    conversation header. Architecturally identical to
    ``include_tool_calls=False`` but keyed on a per-row boolean column
    (``is_compaction_summary``) populated at index time from
    ``conv['compact_markers']``. The SQL ``WHERE is_compaction_summary
    = 0`` clause applies BEFORE bm25 ranking and LIMIT, so the
    truncation envelope's ``total_messages_matched`` is accurate
    (Council 2026-05-26 chose this over scatter-time post-filter to
    avoid empty-result-set under LIMIT when top-bm25 hits cluster in
    compaction-summary bodies).

    Note on FTS5 ``LIMIT 5000`` and ``include_tool_calls=False``: if a
    user's corpus has more than 5000 messages whose only token-match is
    in tool content, FTS5 may return them as the top 5000 ranked hits
    (we then drop all of them) while a plain-text match further down the
    ranking gets missed. The linear-scan fallback would still find it.
    This is an accepted theoretical drift; on Ray's 1,222-file corpus
    it's unrealizable. If a bug report ever shows count mismatch on a
    real query, pagination is the fix.
    """
    if not query or len(query.strip()) < 1:
        return SearchResponse()

    # Empty-set short-circuit: an active filter that excludes everything
    # passes ``conversation_uuids=set()``. Same semantic as ``bookmarks``
    # — distinct from ``None`` (no constraint). Spec §2 (2026-05-14).
    # We DON'T short-circuit on empty bookmarks here for backward compat
    # with the existing router contract; that path's empty handling lives
    # in ``_search_via_linear_scan`` and ``SearchIndex.query``. The
    # conversation_uuids check is hoisted up so we don't waste a query on
    # the FTS5 index either.
    if conversation_uuids is not None and not conversation_uuids:
        return SearchResponse()

    # ``conversation_uuid`` (singular pin scope) is most-specific; when
    # set, it overrides ``project_path``, ``bookmarks``, AND
    # ``conversation_uuids``. We strip the latter three here so the
    # downstream paths only ever see the pin gate.
    if conversation_uuid is not None:
        conversation_uuids = None
        bookmarks = None
        project_path = None

    # Fast path: FTS5 inverted index when ready. Imported lazily so the
    # test suite can patch get_search_index() without import cycles.
    #
    # Phase-2 Workstream A: two FTS5 paths now exist.
    #   * ``_search_via_index_fast`` (context_size="snippet"): pure SQL,
    #     no corpus walk. FTS5's snippet() produces structured
    #     fragments. The dominant code path; replaces the scatter-
    #     gather walk that cost ~15 s cold / ~750 ms warm.
    #   * ``_search_via_index`` (context_size="full"): the existing
    #     Python scatter-gather. Required for "show the whole matched
    #     message" UX — FTS5 snippet() can't produce the full body.
    #     Falls back to FileCache, slow but correct, rare branch.
    try:
        from .search_index import get_search_index

        idx = get_search_index()
        if idx is not None and idx.is_ready():
            try:
                if context_size == "snippet":
                    return _search_via_index_fast(
                        store, idx, query,
                        source=source,
                        sort=sort, sort_order=sort_order,
                        conversation_uuid=conversation_uuid,
                        project_path=project_path,
                        bookmarks=bookmarks,
                        include_tool_calls=include_tool_calls,
                        include_compactions=include_compactions,
                        organization_id=organization_id,
                        conversation_uuids=conversation_uuids,
                        limit=limit,
                    )
                # context_size == "full" fast path (2026-05-22 cold-
                # cache perf fix). Reads `body`/`body_text` directly
                # from FTS5 instead of walking 150+ JSONL files from
                # disk. Cold full-mode goes from ~13 s to ~0.5 s.
                return _search_via_index_fast_full(
                    store, idx, query,
                    source=source,
                    sort=sort, sort_order=sort_order,
                    conversation_uuid=conversation_uuid,
                    project_path=project_path,
                    bookmarks=bookmarks,
                    include_tool_calls=include_tool_calls,
                    include_compactions=include_compactions,
                    organization_id=organization_id,
                    conversation_uuids=conversation_uuids,
                    limit=limit,
                )
            except sqlite3.Error:
                logger.exception(
                    "search_index: query failed; falling back to linear scan"
                )
                # fall through to linear scan
    except ImportError:
        # search_index module isn't importable — definitely use linear scan.
        pass

    linear_results = _search_via_linear_scan(
        store, query,
        source=source, context_size=context_size,
        sort=sort, sort_order=sort_order,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        include_tool_calls=include_tool_calls,
        include_compactions=include_compactions,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
    )
    # Linear scan never truncates — it walks every conversation and
    # emits every match. envelope.truncated = False; total == returned.
    return _wrap_envelope_no_truncation(linear_results)


def _wrap_envelope_no_truncation(results: list[SearchResult]) -> SearchResponse:
    """Wrap a results list as a non-truncated SearchResponse envelope.

    Used by code paths that don't observe a LIMIT (linear scan,
    context_size="full" scatter-gather). The total equals the
    returned count, so truncated is always False.

    Message count semantics: sums per-conversation matching_messages so
    the envelope's counts agree with how the FTS5 fast path's envelope
    is built (which counts message-level FTS5 hits, NOT conversations).
    """
    msg_count = sum(len(r.matching_messages) for r in results)
    return SearchResponse(
        results=results,
        total_messages_matched=msg_count,
        returned_messages=msg_count,
        truncated=False,
    )


# ---------------------------------------------------------------------------
# FTS5 fast path (Phase-2 Workstream A)
# ---------------------------------------------------------------------------


# Sentinel byte sequences FTS5 ``snippet()`` wraps around matches.
# Must stay in lockstep with ``SearchIndex._SNIPPET_OPEN`` /
# ``SearchIndex._SNIPPET_CLOSE``. Defining the constants twice
# (here + index) is deliberate: this module is the consumer; the
# index module is the producer; coupling them via import would
# muddle the layering. The test
# ``test_search_snippet_fragments.test_fast_path_populates_fragments_for_snippet_mode``
# catches any drift.
_FRAG_OPEN = "\u0001\u0001MARK\u0001\u0001"
_FRAG_CLOSE = "\u0001\u0001/MARK\u0001\u0001"


def _parse_snippet_to_fragments(
    raw_snippet: str,
) -> tuple[str, list[SnippetFragment], int, int]:
    """Parse FTS5 ``snippet()`` output into structured fragments.

    Input: ``"...lorem \\x01\\x01MARK\\x01\\x01python\\x01\\x01/MARK\\x01\\x01 ipsum..."``
    Output:
      * Rendered snippet (sentinels stripped):
        ``"...lorem python ipsum..."``
      * Fragments:
        ``[Frag('...lorem ', False), Frag('python', True),
          Frag(' ipsum...', False)]``
      * ``match_start``, ``match_end``: position of the FIRST marked
        span in the rendered snippet (for backward-compat with
        consumers that read the legacy match_start/match_end pair).

    Robust against:
      * No marks (FTS5 sometimes returns the raw snippet without
        marks for stemmer-drift cases): single unmarked fragment;
        match_start = match_end = 0.
      * Multiple marks: each becomes its own fragment.
      * Empty unmarked spans between consecutive marks: skipped
        so the fragment list never has zero-length entries (the
        invariant the frontend renderer relies on).
      * Malformed input (an open without a close): the trailing
        text after the dangling open is treated as unmarked; we
        never raise on a producer drift bug — falling back to
        "no highlight" is preferable to a 500.
    """
    if _FRAG_OPEN not in raw_snippet:
        # No marks at all — return a single unmarked fragment.
        rendered = raw_snippet
        if not rendered:
            return "", [], 0, 0
        return rendered, [SnippetFragment(text=rendered, mark=False)], 0, 0

    fragments: list[SnippetFragment] = []
    rendered_parts: list[str] = []
    rendered_len = 0
    match_start = 0
    match_end = 0
    first_match_recorded = False

    # Walk the string segment-by-segment around open/close pairs.
    cursor = 0
    while cursor < len(raw_snippet):
        open_idx = raw_snippet.find(_FRAG_OPEN, cursor)
        if open_idx < 0:
            # Tail — everything left is unmarked.
            tail = raw_snippet[cursor:]
            if tail:
                fragments.append(SnippetFragment(text=tail, mark=False))
                rendered_parts.append(tail)
                rendered_len += len(tail)
            break

        # Leading unmarked segment (open_idx may equal cursor for
        # a snippet that starts with a mark — skip the empty span).
        if open_idx > cursor:
            seg = raw_snippet[cursor:open_idx]
            fragments.append(SnippetFragment(text=seg, mark=False))
            rendered_parts.append(seg)
            rendered_len += len(seg)

        body_start = open_idx + len(_FRAG_OPEN)
        close_idx = raw_snippet.find(_FRAG_CLOSE, body_start)
        if close_idx < 0:
            # Malformed: open without close. Treat remainder as
            # unmarked and stop.
            tail = raw_snippet[body_start:]
            if tail:
                fragments.append(SnippetFragment(text=tail, mark=False))
                rendered_parts.append(tail)
                rendered_len += len(tail)
            break

        marked_text = raw_snippet[body_start:close_idx]
        if marked_text:
            fragments.append(SnippetFragment(text=marked_text, mark=True))
            rendered_parts.append(marked_text)
            if not first_match_recorded:
                match_start = rendered_len
                match_end = rendered_len + len(marked_text)
                first_match_recorded = True
            rendered_len += len(marked_text)

        cursor = close_idx + len(_FRAG_CLOSE)

    rendered = "".join(rendered_parts)
    return rendered, fragments, match_start, match_end


def _search_via_index_fast(
    store: ConversationStore,
    idx: Any,
    query: str,
    *,
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"],
    sort: SortField,
    sort_order: SortOrder,
    conversation_uuid: str | None,
    project_path: str | None,
    bookmarks: set[str] | None,
    include_tool_calls: bool = True,
    include_compactions: bool = True,
    organization_id: str | None = None,
    conversation_uuids: set[str] | None = None,
    limit: int = 1000,
) -> SearchResponse:
    """Pure-SQL FTS5 fast path for ``context_size="snippet"`` queries.

    Replaces the scatter-gather walk in :func:`_search_via_index` with
    two SQL queries:

      1. ``SearchIndex.query_with_snippets`` — body MATCH + FTS5
         ``snippet()`` per row + conv-level metadata (title,
         timestamps, project_path).
      2. ``SearchIndex.title_match_snippets`` — LIKE-based title
         substring sweep that also returns conv-level metadata so
         title-only hits build SearchResult without a body row.

    Zero conversation-file reads. Zero corpus walk. Latency target:
    <200 ms cold / <50 ms warm on the user's 991-conv corpus
    (PLANS/PERFORMANCE_PHASE_2.md §Workstream A measured payoff).

    The output shape matches the legacy path's ``SearchResult`` /
    ``MessageSnippet`` shape with an additional ``fragments`` field
    populated on each body-match row. The legacy ``snippet`` /
    ``match_start`` / ``match_end`` fields stay populated (derived
    from the same fragments) so clients that don't consume
    fragments continue working.

    ``include_tool_calls=False`` (2026-05-16, SEARCH_TOOL_AWARENESS
    plan §A): plumbed all the way down to ``query_with_snippets`` so
    the FTS5 MATCH targets the ``body_text`` column instead of
    ``body``. body_text excludes tool_use / tool_result, so a hit
    whose only token lives in a hidden tool block is dropped at
    MATCH time — exact parity with the linear-scan path's runtime
    filter, but without the corpus walk. Replaces the prior
    accepted-residual divergence (Phase-2 Workstream A).
    """
    # Step 1: body MATCH + snippet() — one SQL query, no JSON reads.
    rows = idx.query_with_snippets(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_tool_calls=include_tool_calls,
        include_compactions=include_compactions,
        limit=limit,
    )

    # Step 1b: COUNT(*) under the same WHERE — drives the truncation
    # envelope's total_messages_matched. ~5-10 ms on the user's corpus.
    # Same scope filters as the snippet query above (Risk #5 in the
    # plan: enforced via shared _build_match_where_clause).
    total_messages_matched = idx.count_matches(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_tool_calls=include_tool_calls,
        include_compactions=include_compactions,
    )

    # Step 2: title-substring sweep (catches mid-token matches FTS5
    # can't see via prefix tokenizer; e.g. "edul" in "scheduled").
    # v14 (2026-05-26): pass include_compactions through so the sweep
    # filters out conversations whose TITLE is the canonical compaction-
    # summary prefix when Show Compactions is OFF. Closes the bug where
    # a CC session whose title was fallback-derived from the compaction
    # body still surfaced as a title-only hit even with the toggle off.
    title_hits = idx.title_match_snippets(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_compactions=include_compactions,
    )

    # Group body matches by conv_uuid. We also stash the per-conv
    # metadata from the first row we see — every row for a given
    # conv carries the same title/project/timestamps so the first
    # one wins.
    by_conv: dict[str, dict[str, Any]] = {}
    for r in rows:
        cu = r["conv_uuid"]
        slot = by_conv.setdefault(
            cu,
            {
                "title": r.get("title") or "Untitled",
                "project_path": r.get("project_path") or None,
                "conv_created_at": r.get("conv_created_at") or "",
                "conv_updated_at": r.get("conv_updated_at") or "",
                "body_messages": [],
                "title_marked": None,
            },
        )

        body_snippet_raw = r.get("body_snippet") or ""
        # Skip rows whose body is empty AND not marked — these are the
        # sentinel "title only" rows (upsert_conversation writes one
        # for messageless convs). They don't carry useful snippet text
        # and the title sweep handles title-only matches separately.
        if not body_snippet_raw or (
            _FRAG_OPEN not in body_snippet_raw and not body_snippet_raw.strip()
        ):
            continue

        rendered, frags, m_start, m_end = _parse_snippet_to_fragments(
            body_snippet_raw,
        )
        if not rendered:
            continue
        slot["body_messages"].append(
            MessageSnippet(
                message_uuid=r.get("message_uuid", "") or "",
                sender=r.get("sender", "") or "",
                snippet=rendered,
                match_start=m_start,
                match_end=m_end,
                created_at=_parse_datetime(r.get("created_at")),
                fragments=frags,
            )
        )

    # Merge title-hit conv-uuids into the by_conv map. A conv that
    # had no body hit but a title hit needs its metadata populated
    # from the title-sweep result.
    for cu, meta in title_hits.items():
        slot = by_conv.setdefault(
            cu,
            {
                "title": meta.get("title") or "Untitled",
                "project_path": meta.get("project_path") or None,
                "conv_created_at": meta.get("conv_created_at") or "",
                "conv_updated_at": meta.get("conv_updated_at") or "",
                "body_messages": [],
                "title_marked": None,
            },
        )
        # Stash the marked title for emission below.
        slot["title_marked"] = meta.get("marked_title")

    # Build SearchResult list.
    results: list[SearchResult] = []
    for cu, slot in by_conv.items():
        matching: list[MessageSnippet] = []

        # Title pseudo-message FIRST (mirrors linear-scan ordering).
        if slot.get("title_marked"):
            rendered, frags, m_start, m_end = _parse_snippet_to_fragments(
                slot["title_marked"],
            )
            matching.append(
                MessageSnippet(
                    message_uuid="title",
                    sender="title",
                    snippet=rendered,
                    match_start=m_start,
                    match_end=m_end,
                    fragments=frags,
                )
            )

        matching.extend(slot["body_messages"])

        if not matching:
            continue

        results.append(
            SearchResult(
                conversation_uuid=cu,
                conversation_name=slot["title"],
                conversation_updated_at=_parse_datetime(slot["conv_updated_at"]),
                conversation_created_at=_parse_datetime(slot["conv_created_at"]),
                project_name=_derive_project_name(slot["project_path"]),
                matching_messages=matching,
            )
        )

    sorted_results = _sort_results(results, sort=sort, sort_order=sort_order)
    # Truncation envelope (plan §B). returned_messages counts the body
    # rows returned by query_with_snippets (capped at ``limit``), NOT
    # the per-conv rollup. Title-only pseudo-messages from
    # title_match_snippets are NOT counted in either number — the FTS5
    # bm25 LIMIT only applies to body rows; the title sweep returns
    # every match and never truncates.
    returned_messages = len(rows)
    truncated = returned_messages < total_messages_matched
    return SearchResponse(
        results=sorted_results,
        total_messages_matched=total_messages_matched,
        returned_messages=returned_messages,
        truncated=truncated,
    )


def _search_via_index_fast_full(
    store: ConversationStore,
    idx: Any,
    query: str,
    *,
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"],
    sort: SortField,
    sort_order: SortOrder,
    conversation_uuid: str | None,
    project_path: str | None,
    bookmarks: set[str] | None,
    include_tool_calls: bool = True,
    include_compactions: bool = True,
    organization_id: str | None = None,
    conversation_uuids: set[str] | None = None,
    limit: int = 1000,
) -> SearchResponse:
    """FTS5 fast path for ``context_size='full'`` — body from the index, NO file walk.

    The previous full-mode path (:func:`_search_via_index`) walked
    every matched conversation's JSONL file from disk to extract the
    full message body. On a 152-candidate cold corpus that was
    ~10–13 s of `parse_jsonl_fast` + `_extract_searchable_text`
    overhead. Since `upsert_conversation` already stores
    `_extract_searchable_text(msg, include_tool_calls=True/False)`
    in the FTS5 ``body``/``body_text`` columns, we can serve the
    full-mode response directly from the index with ZERO file I/O.

    Measured (152 candidates, ``this image`` query):
      * Cold _search_via_index (file walk):   ~13 s
      * Cold _search_via_index_fast_full:     ~0.5 s

    Output shape is byte-identical to the legacy path:
      * ``MessageSnippet.snippet`` = full body text
      * ``MessageSnippet.match_start/end`` = first regex hit offsets
        in the full text (so the frontend's HighlightedSnippet can
        place a ``<mark>`` band)
      * ``MessageSnippet.fragments`` = None (full mode doesn't use
        the structured fragment path; the legacy linear-scan code
        also returns None for fragments in full mode)

    Falls back to the file-walk slow path on:
      * empty or invalid query (no FTS5 MATCH expression)
      * SQL errors (sqlite3.Error bubbles up to the caller)
    """
    rows = idx.query_with_full_body(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_tool_calls=include_tool_calls,
        include_compactions=include_compactions,
        limit=limit,
    )

    total_messages_matched = idx.count_matches(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_tool_calls=include_tool_calls,
        include_compactions=include_compactions,
    )

    # v14 (2026-05-26): plumb include_compactions into the title sweep
    # so the full-mode fast path applies the same filter as
    # _search_via_index_fast above.
    title_hits = idx.title_match_snippets(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_compactions=include_compactions,
    )

    # Parse query once for regex-based highlight placement on bodies.
    phrase, tokens = parse_user_query(query)
    pattern = _make_snippet_regex(phrase, tokens) if tokens else None
    tokens_lower = [t.lower() for t in tokens] if tokens else []

    # Group body rows by conv_uuid (mirrors _search_via_index_fast).
    by_conv: dict[str, dict[str, Any]] = {}
    for r in rows:
        cu = r["conv_uuid"]
        body = r.get("body") or ""
        slot = by_conv.setdefault(
            cu,
            {
                "title": r.get("title") or "Untitled",
                "project_path": r.get("project_path") or None,
                "conv_created_at": r.get("conv_created_at") or "",
                "conv_updated_at": r.get("conv_updated_at") or "",
                "body_messages": [],
                "title_marked": None,
            },
        )

        # Skip rows whose body is empty (the title-only sentinel
        # rows upsert_conversation writes for messageless convs).
        if not body or not body.strip():
            continue

        # Mirror the linear-scan AND-of-tokens body gate. FTS5
        # already filtered by MATCH at SQL time, but title-only
        # FTS5 hits (where MATCH fired because of a TITLE token,
        # not a body token) need to be re-filtered here. Same
        # invariant as the slow path at search.py:1048-1050.
        if tokens_lower:
            body_lower = body.lower()
            if not all(t in body_lower for t in tokens_lower):
                continue

        # Find first regex hit in the full body for highlight
        # placement. Mirrors slow-path semantics at search.py:1058.
        # Stemmer/diacritic drift case (FTS5 matched but Python
        # regex doesn't): emit start=0/end=0 fallback, same as
        # slow path.
        m_start, m_end = 0, 0
        if pattern is not None:
            match = pattern.search(body)
            if match is not None:
                m_start, m_end = match.start(), match.end()

        slot["body_messages"].append(
            MessageSnippet(
                message_uuid=r.get("message_uuid", "") or "",
                sender=r.get("sender", "") or "",
                snippet=body,
                match_start=m_start,
                match_end=m_end,
                created_at=_parse_datetime(r.get("created_at")),
                fragments=None,
            )
        )

    # Merge title-only hits (mirrors _search_via_index_fast).
    for cu, meta in title_hits.items():
        slot = by_conv.setdefault(
            cu,
            {
                "title": meta.get("title") or "Untitled",
                "project_path": meta.get("project_path") or None,
                "conv_created_at": meta.get("conv_created_at") or "",
                "conv_updated_at": meta.get("conv_updated_at") or "",
                "body_messages": [],
                "title_marked": None,
            },
        )
        slot["title_marked"] = meta.get("marked_title")

    results: list[SearchResult] = []
    for cu, slot in by_conv.items():
        matching: list[MessageSnippet] = []

        if slot.get("title_marked"):
            rendered, frags, m_start, m_end = _parse_snippet_to_fragments(
                slot["title_marked"],
            )
            matching.append(
                MessageSnippet(
                    message_uuid="title",
                    sender="title",
                    snippet=rendered,
                    match_start=m_start,
                    match_end=m_end,
                    fragments=frags,
                )
            )

        matching.extend(slot["body_messages"])

        if not matching:
            continue

        results.append(
            SearchResult(
                conversation_uuid=cu,
                conversation_name=slot["title"],
                conversation_updated_at=_parse_datetime(slot["conv_updated_at"]),
                conversation_created_at=_parse_datetime(slot["conv_created_at"]),
                project_name=_derive_project_name(slot["project_path"]),
                matching_messages=matching,
            )
        )

    sorted_results = _sort_results(results, sort=sort, sort_order=sort_order)
    returned_messages = len(rows)
    truncated = returned_messages < total_messages_matched
    return SearchResponse(
        results=sorted_results,
        total_messages_matched=total_messages_matched,
        returned_messages=returned_messages,
        truncated=truncated,
    )


def _search_via_linear_scan(
    store: ConversationStore,
    query: str,
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all",
    context_size: Literal["snippet", "full"] = "snippet",
    sort: SortField = "updated_at",
    sort_order: SortOrder = "desc",
    conversation_uuid: str | None = None,
    project_path: str | None = None,
    bookmarks: set[str] | None = None,
    include_tool_calls: bool = True,
    include_compactions: bool = True,
    organization_id: str | None = None,
    conversation_uuids: set[str] | None = None,
) -> list[SearchResult]:
    """Original linear-scan implementation; now the fallback path.

    Walks every conversation, runs a Python regex against each message's
    flattened searchable text. Slow on large corpora (~0.8-2.3s on Ray's
    1.5GB corpus) but always correct and never depends on an index file
    being present.

    Sidebar-scope params (2026-05-14):
      * ``organization_id`` — workspace gate; mirrors
        ConversationStore.list_conversations behavior. None on the conv
        side does NOT match a UUID filter (and vice versa).
      * ``conversation_uuids`` — active-filter set gate; ANDs with the
        other scope filters. None means "no constraint".
    """
    phrase, tokens = parse_user_query(query)
    if not tokens:
        return []
    pattern = _make_snippet_regex(phrase, tokens)
    if pattern is None:
        return []
    # Lowercased token list used to AND-filter messages on the linear path
    # (phrase mode is a single literal "token"). The FTS5 path filters
    # AND-semantics in SQL; the linear path is the fallback and must
    # enforce the same contract here.
    tokens_lower = [t.lower() for t in tokens]
    results = []

    for conv in store.get_all_conversations_raw(source=source):
        if conversation_uuid:
            # Most specific filter; wins over project_path / bookmarks /
            # conversation_uuids. (We also strip those three at the
            # search_conversations entry point — this is defense in depth.)
            if conv.get("uuid") != conversation_uuid:
                continue
        else:
            if project_path and conv.get("project_path") != project_path:
                continue
            if bookmarks is not None and conv.get("uuid") not in bookmarks:
                continue
            if (
                conversation_uuids is not None
                and conv.get("uuid") not in conversation_uuids
            ):
                continue
        # Workspace filter ANDs always (not part of the most-specific
        # override). None on the filter side means "no constraint";
        # otherwise an exact-equality gate matches conv organization_id
        # (None on the conv side is NOT a wildcard match).
        if organization_id is not None and conv.get("organization_id") != organization_id:
            continue
        matching_messages: list[MessageSnippet] = []

        # Per-conv /compact trigger→marker UUID rewrite map (2026-05-23).
        # Empty for the common case (no compact markers). When a body hit
        # would otherwise land on a /compact trigger row's UUID, the
        # rewrite redirects it to the corresponding isCompactSummary
        # marker UUID so the frontend's auto-expand chain (which keys on
        # ``compact_marker.message_uuid``) can fire. Belt-and-suspenders
        # — the index-time exclusion in _extract_searchable_text should
        # already prevent trigger-row hits after the v11 rebuild, but
        # this layer catches (a) stale-index hits during the rebuild
        # window and (b) any future code path that re-introduces
        # trigger-row text into linear-scan without bumping
        # SCHEMA_VERSION. ``emitted_seen`` dedupes UUIDs so a transient
        # double-hit (marker AND stale trigger) does not produce two
        # MessageSnippet rows pointing at the same emitted UUID — the
        # frontend does NOT dedupe at render time (see
        # frontend/src/contexts/SearchPanelContext.tsx).
        trigger_to_marker = _build_compact_trigger_uuid_map(conv)
        emitted_seen: set[str] = set()

        # v13 (2026-05-26): compaction-aware filter, linear-scan branch.
        # Build the set of UUIDs that ARE compaction-summary rows so we
        # can drop any emitted hit whose UUID matches. Empty when the
        # conv has no compactions OR when include_compactions=True
        # (the gate below short-circuits in that case). Mirrors the SQL
        # ``WHERE is_compaction_summary = 0`` clause on the FTS5 path.
        compact_marker_uuids: set[str] = set()
        if not include_compactions:
            compact_marker_uuids = {
                m.get("message_uuid", "")
                for m in (conv.get("compact_markers") or [])
                if isinstance(m, dict) and m.get("message_uuid")
            }

        # Search in conversation name. Title-match policy:
        #   * Phrase mode → literal substring (the whole phrase appears).
        #   * Token mode  → CURRENT behavior preserved: substring match on
        #     the FULL query string (so a typed 3-word query that wasn't
        #     intended as a title hunt doesn't unexpectedly match more
        #     titles). This is intentionally conservative; revisit if a
        #     user reports title hits being too narrow.
        # `conv.get("name", "")` only defaults when the key is MISSING;
        # if the key is present with value None (legacy Desktop or
        # partial-write CC sessions), `.lower()` raises AttributeError.
        # `or ""` collapses both None and missing to "". Sibling fix to
        # `backend/store.py:list_conversations`.
        name = conv.get("name") or ""
        name_lower = name.lower()
        title_needle = (phrase if phrase is not None else query).lower()
        # v14 (2026-05-26): compaction-titled gate, linear-scan branch.
        # When Show Compactions is OFF, suppress title pseudo-messages
        # whose conversation TITLE is the canonical compaction-summary
        # prefix. Mirrors the SQL-side gate the FTS5 title-sweep helpers
        # apply via the ``is_compaction_titled`` column. The shared
        # ``is_compaction_prefix_text`` helper is the single source of
        # truth for the predicate — keeps linear and FTS paths from
        # drifting (Council 2026-05-26).
        if (
            title_needle
            and title_needle in name_lower
            and (include_compactions or not is_compaction_prefix_text(name))
        ):
            # Add a pseudo-message for title match. Titles are short, so we
            # always use the snippet helper here regardless of context_size.
            match = pattern.search(name)
            if match:
                snippet, start, end = create_snippet(name, match.start(), match.end())
                matching_messages.append(
                    MessageSnippet(
                        message_uuid="title",
                        sender="title",
                        snippet=snippet,
                        match_start=start,
                        match_end=end,
                    )
                )

        # Search in messages. The `or []` guard handles the case where
        # the key is present with explicit `None` (legacy / partial-write
        # Desktop JSON); `data.get(k, [])` returns the default ONLY when
        # the key is missing. Same bug-class as the 8ab36fc fix on
        # name/summary/project_path; pinned by
        # test_search_handles_null_chat_messages_without_crashing.
        for msg in conv.get("chat_messages", []) or []:
            # Issue #0 — cache the searchable-text projection on the
            # message dict itself. The cached conversation dict is the
            # same instance on every call (via backend.cache.FileCache),
            # so this memo survives across search requests until the
            # source file's mtime changes (which invalidates the cache
            # entry and rebuilds the dict). Profile showed
            # _stringify_tool_input -> json.dumps was the dominant warm
            # cost (~0.3s of the ~0.9s search loop).
            #
            # 2026-05-11: dynamic cache key so the include_tool_calls=True
            # and False projections coexist without poisoning each other.
            # A query that toggles the setting between calls re-uses the
            # other projection on the cached dict.
            cache_key = (
                "__search_text_full__" if include_tool_calls
                else "__search_text_textonly__"
            )
            text = msg.get(cache_key)
            if text is None:
                text = _extract_searchable_text(
                    msg, include_tool_calls=include_tool_calls,
                )
                msg[cache_key] = text

            if not text:
                continue

            # AND-of-tokens gate (linear path): every required token must
            # appear (case-insensitive substring) in the message text.
            # Phrase mode reduces to "the phrase must appear" because
            # tokens_lower has a single element equal to the phrase. This
            # matches the FTS5 path's MATCH semantics. Without this gate,
            # the snippet regex's alternation (foo|bar) would happily
            # surface a message containing only "foo" — silent OR drift.
            text_lower = text.lower()
            if not all(t in text_lower for t in tokens_lower):
                continue

            msg_created_at = _parse_datetime(msg.get("created_at"))

            # Snippet placement: find the FIRST token occurrence (any
            # token; first-match wins). The frontend's HighlightedSnippet
            # only supports a single contiguous <mark>, so highlighting
            # multiple tokens isn't worth the complexity — the user sees
            # WHY they landed, and the ±150 char window typically shows
            # the other tokens around it.
            match = pattern.search(text)
            if match is not None:
                if context_size == "full":
                    snippet = text
                    start = match.start()
                    end = match.end()
                else:
                    snippet, start, end = create_snippet(
                        text, match.start(), match.end()
                    )
            else:
                # Fallback: tokens AND-pass but regex didn't find them
                # literally (stemmer-drift on the FTS5 side OR a unicode
                # normalization quirk). Emit a leading-text snippet with
                # a 0-length highlight rather than dropping the FTS5 hit.
                if context_size == "full":
                    snippet = text
                else:
                    snippet = text[:_FALLBACK_SNIPPET_LEN] + (
                        "..." if len(text) > _FALLBACK_SNIPPET_LEN else ""
                    )
                start = 0
                end = 0
            # Apply /compact trigger→marker UUID rewrite (no-op when the
            # message is not a trigger row — empty mapping returns the
            # uuid unchanged via dict.get(uuid, uuid)). Dedupe across
            # emitted UUIDs so a stale-index double-hit on the same
            # conceptual row collapses to ONE MessageSnippet.
            raw_uuid = msg.get("uuid", "")
            emitted_uuid = trigger_to_marker.get(raw_uuid, raw_uuid)
            # v13 (2026-05-26): compaction-aware drop. Apply AFTER the
            # trigger→marker rewrite so a hit on a stale trigger row
            # (which the rewrite redirects to the marker UUID) is ALSO
            # caught here — otherwise pre-v11 stale-index trigger hits
            # would slip past the filter under include_compactions=False.
            if emitted_uuid in compact_marker_uuids:
                continue
            if emitted_uuid in emitted_seen:
                continue
            emitted_seen.add(emitted_uuid)
            matching_messages.append(
                MessageSnippet(
                    message_uuid=emitted_uuid,
                    sender=msg.get("sender", ""),
                    snippet=snippet,
                    match_start=start,
                    match_end=end,
                    created_at=msg_created_at,
                )
            )

        if matching_messages:
            results.append(
                SearchResult(
                    conversation_uuid=conv.get("uuid", ""),
                    conversation_name=conv.get("name", "Untitled"),
                    conversation_updated_at=_parse_datetime(conv.get("updated_at")),
                    conversation_created_at=_parse_datetime(conv.get("created_at")),
                    project_name=_derive_project_name(conv.get("project_path")),
                    matching_messages=matching_messages,
                )
            )

    return _sort_results(results, sort=sort, sort_order=sort_order)


def _search_via_index(
    store: ConversationStore,
    idx: Any,
    query: str,
    *,
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"],
    context_size: Literal["snippet", "full"],
    sort: SortField,
    sort_order: SortOrder,
    conversation_uuid: str | None,
    project_path: str | None,
    bookmarks: set[str] | None,
    include_tool_calls: bool = True,
    include_compactions: bool = True,
    organization_id: str | None = None,
    conversation_uuids: set[str] | None = None,
) -> list[SearchResult]:
    """FTS5 fast path: scatter-gather over the inverted index.

    1. Ask the FTS5 index for the set of matched ``(conv_uuid,
       message_uuid)`` pairs (subject to source/scope filters at the SQL
       layer too — they're cheap UNINDEXED-column filters).
    2. Title-substring sweep over conversation summaries (lightweight)
       to catch sub-token substrings the FTS5 prefix-tokenizer can't
       see (e.g., "edul" inside "scheduled").
    3. Walk ONLY the matched conversations via
       :meth:`ConversationStore.get_conversation` (warm via FileCache)
       and run the existing :func:`create_snippet` / regex finditer
       loop on each. This avoids loading the entire conversation
       corpus on every query.

    Why we don't call back into ``_search_via_linear_scan``: that function
    re-walks ``store.get_all_conversations_raw()`` which loads every
    JSON/JSONL file. Even with FileCache hot, that's ~1 s for a 1.5 GB
    corpus. Walking only the matched conversations is the entire point
    of the index.
    """
    # Step 1: ask FTS5 for body+title MATCH hits (subject to scope at
    # the SQL layer).
    # v14 (2026-05-26): plumb include_compactions so the legacy slow
    # path also drops compaction-summary message-body hits at SQL time
    # (same vector v13 closed on the fast paths via
    # _build_match_where_clause).
    matches = idx.query(
        query,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_compactions=include_compactions,
    )
    body_matched_uuids: set[str] = {m["conv_uuid"] for m in matches}

    # Parse user query once — drives both title sweep and snippet regex.
    phrase, tokens = parse_user_query(query)
    if not tokens:
        return []
    title_needle = phrase if phrase is not None else query
    # Step 2: title-substring sweep. The linear scan emits a title pseudo-
    # message when the conversation NAME contains the query (case-
    # insensitive substring). FTS5 catches token-aligned title matches,
    # but a substring that crosses token boundaries (e.g. "ned" inside
    # "scheduled") would NOT hit FTS5 yet WOULD hit the linear scan.
    # We use the FTS5 index's own ``title`` column as the source of
    # truth — it's stored UNINDEXED so a SELECT DISTINCT is cheap and
    # avoids the multi-second cost of ``store.list_conversations()``
    # (which rebuilds the agent index on every call).
    #
    # Multi-word note: we sweep on the FULL query string (or stripped
    # phrase) — conservative substring semantics, mirroring the linear
    # path. Token-level AND on titles is a future enhancement (see
    # `_search_via_linear_scan` for the matching policy).
    #
    # Council A1, 2026-05-21: previously this block reached into
    # ``idx._get_read_conn()`` + ``idx._populate_allowed_conv()`` directly
    # and built the SQL inline, crossing the module boundary into
    # SearchIndex internals. The equivalent public API is
    # :meth:`SearchIndex.title_match_uuids`, which preserves the exact SQL
    # shape (SELECT DISTINCT conv_uuid + LIKE substring + scope filters +
    # per-connection allowed_conv TEMP table). Byte-for-byte equivalence
    # is pinned by
    # ``test_title_match_uuids_byte_for_byte_matches_legacy_reach_through``.
    # v14 (2026-05-26): plumb include_compactions so the slow-path
    # title-sweep helper also gates compaction-titled conversations
    # when Show Compactions is OFF. Matches the fast-path treatment.
    title_matched_uuids = idx.title_match_uuids(
        title_needle,
        source=source,
        conversation_uuid=conversation_uuid,
        project_path=project_path,
        bookmarks=bookmarks,
        organization_id=organization_id,
        conversation_uuids=conversation_uuids,
        include_compactions=include_compactions,
    )
    # ``query_lower`` is consumed below by the per-conversation walk to
    # decide whether to emit a "title" pseudo-message (mirroring the
    # linear-scan behavior); keep it local since the public title sweep
    # has already done its work above.
    query_lower = title_needle.lower()

    candidate_uuids = body_matched_uuids | title_matched_uuids

    # AND with any user scope (extra defense — the SQL WHERE clauses
    # already enforce these).
    if bookmarks is not None:
        candidate_uuids &= bookmarks
    if conversation_uuid is not None:
        candidate_uuids &= {conversation_uuid}
    if conversation_uuids is not None:
        candidate_uuids &= conversation_uuids

    if not candidate_uuids:
        return []

    # Step 3: walk ONLY the matched conversations. For each matched
    # conv, find which of its messages were FTS5-hit, then run the
    # snippet regex on those messages to produce snippets byte-for-byte
    # identical to the linear path.
    pattern = _make_snippet_regex(phrase, tokens)
    if pattern is None:
        return []
    tokens_lower = [t.lower() for t in tokens]
    msgs_per_conv: dict[str, set[str]] = {}
    for m in matches:
        cu = m["conv_uuid"]
        if cu in candidate_uuids:
            msgs_per_conv.setdefault(cu, set()).add(m["message_uuid"])

    # Load ONLY the matched conversations (not the whole corpus).
    #
    # 2026-05-22 cold-cache perf fix: the previous version walked
    # `store.get_all_conversations_raw(source=source)` which loads
    # EVERY conv file from disk into the FileCache, then skipped non-
    # matches. On a 1062-file corpus that's ~13 s cold (file I/O
    # dominated) even when only 156 of those convs actually matched
    # the query. The user's "tens of seconds" search reports were
    # this code path on a freshly-restarted server.
    #
    # `_find_conversation_data(uuid)` loads exactly ONE conv (Pass A:
    # stem==uuid; Pass B: summary-cache lookup). Iterating it over
    # candidate_uuids loads only the rows we'll actually use.
    #
    # Measured cold cache (this image query, 156 candidates on
    # ~/.claude-explorer):
    #   * get_all_conversations_raw('all'):    12.86 s
    #   * 156 × _find_conversation_data:        2.44 s  (5.3× faster)
    #
    # Warm cache (FileCache hits): difference is negligible (~50 ms
    # for either path — file reads aren't happening at all).
    # Walk ONLY the matched conversations. On warm cache (FileCache
    # hits) this is sub-100ms even for the whole corpus. On cold
    # cache, this loop parses 100-200 JSONL files at ~50ms each =
    # ~5-10s — that's the inherent cost of the slow path because it
    # needs FULL message bodies from disk to apply the per-message
    # regex.
    #
    # The fast path (_search_via_index_fast for context_size="snippet")
    # avoids this cost entirely by using FTS5's snippet() and body
    # columns. A future enhancement (PLANS/search-fast-full.md): make
    # context_size="full" use the same FTS5 columns since `body`
    # already stores the full extracted text. That would skip file
    # I/O entirely. Not in scope for V1.
    results: list[SearchResult] = []
    for conv in store.get_all_conversations_raw(source=source):
        cu = conv.get("uuid", "")
        if cu not in candidate_uuids:
            continue
        if conv is None:
            continue
        cu = conv.get("uuid", "")
        if cu not in candidate_uuids:
            continue
        # Belt-and-suspenders: every conv we just loaded has uuid=cu by
        # construction, but the source filter on the FTS5 query may
        # have passed a uuid that lives under a non-matching source on
        # disk. Re-apply the source gate here so a `source='CLAUDE_AI'`
        # caller can never see a Claude Code conv slip through (the
        # FTS5 SQL already filters this; the gate is defensive).
        if source != "all" and conv.get("source") != source:
            continue

        matching_messages: list[MessageSnippet] = []
        name = conv.get("name", "") or ""

        # /compact trigger→marker rewrite + emit dedupe (2026-05-23).
        # See the linear-scan emit site for the full rationale. Defense-
        # in-depth in case the v11 index rebuild hasn't completed (so
        # the SQL hits below still include stale trigger-row uuids in
        # ``wanted_msg_uuids``) — rewrite redirects them to the marker
        # uuid the frontend's auto-expand chain keys on, and dedupe
        # prevents a transient marker+trigger double-hit from emitting
        # two MessageSnippet rows for the same conceptual row.
        trigger_to_marker = _build_compact_trigger_uuid_map(conv)
        emitted_seen: set[str] = set()

        # v14 (2026-05-26): mirror the linear-scan compaction-aware
        # filter (search.py: _search_via_linear_scan). The SQL gate in
        # idx.query() above already drops compaction-summary rows when
        # include_compactions=False, but this per-conv set provides
        # belt-and-suspenders for the case where (a) the SQL gate
        # silently no-op'd (stale index missing the v13 column —
        # shouldn't happen post-rebuild, but defensive) OR
        # (b) the trigger→marker rewrite below redirects a body uuid
        # to a compaction-marker uuid that the SQL gate didn't see.
        compact_marker_uuids: set[str] = set()
        if not include_compactions:
            compact_marker_uuids = {
                m.get("message_uuid", "")
                for m in (conv.get("compact_markers") or [])
                if isinstance(m, dict) and m.get("message_uuid")
            }

        # Title pseudo-message — emitted only when the conv NAME contains
        # the query (mirrors the linear-scan emit at search.py:264).
        # v14 (2026-05-26): also gate on the compaction-titled predicate
        # when Show Compactions is OFF. The title-sweep SQL helpers
        # (title_match_snippets / title_match_uuids) filter by the
        # stored is_compaction_titled column, but this Python-side
        # emit is independent — it walks every candidate conv and
        # re-checks the NAME at snippet-build time, so it would leak
        # the compaction-titled hit even if the title sweep dropped
        # the uuid. The shared helper applies the same anchored
        # ``.lstrip().startswith()`` predicate that powers the stored
        # column, so the two layers never drift.
        if query_lower in name.lower() and (
            include_compactions or not is_compaction_prefix_text(name)
        ):
            tmatch = pattern.search(name)
            if tmatch:
                snippet, start, end = create_snippet(name, tmatch.start(), tmatch.end())
                matching_messages.append(
                    MessageSnippet(
                        message_uuid="title",
                        sender="title",
                        snippet=snippet,
                        match_start=start,
                        match_end=end,
                    )
                )

        # Body matches: walk only the FTS5-matched messages (the rest
        # are guaranteed not to match the query).
        wanted_msg_uuids = msgs_per_conv.get(cu, set())
        # 2026-05-11: dynamic cache key keyed to include_tool_calls so the
        # two projections coexist on the same cached message dict (FTS5
        # index always stores the FULL text; the filter is applied at
        # snippet-build time here). A query that toggles the setting
        # between calls re-uses the other projection without thrashing.
        cache_key = (
            "__search_text_full__" if include_tool_calls
            else "__search_text_textonly__"
        )
        # See the linear-scan callsite above for the `or []` rationale —
        # explicit-None chat_messages must not crash the FTS5 fast path
        # either. Pinned by
        # test_search_handles_null_chat_messages_without_crashing.
        for msg in conv.get("chat_messages", []) or []:
            if msg.get("uuid") not in wanted_msg_uuids:
                continue
            text = msg.get(cache_key)
            if text is None:
                text = _extract_searchable_text(
                    msg, include_tool_calls=include_tool_calls,
                )
                msg[cache_key] = text
            if not text:
                continue
            # FTS5 returns a row for every message in a conversation
            # whose TITLE column matches (title is indexed too); a
            # title-only hit will surface every message row for that
            # conv. To match the linear path's body-only emission, we
            # require the body to contain every token (case-insensitive
            # substring) before emitting a body snippet. This is also
            # the gate that lets us emit a fallback snippet for stemmer
            # drift (e.g. query `run` finds `running` in body — the
            # substring check passes, the regex fails, fallback fires).
            text_lower = text.lower()
            if not all(t in text_lower for t in tokens_lower):
                continue
            msg_created_at = _parse_datetime(msg.get("created_at"))
            # FTS5 already filtered by MATCH (AND-of-tokens). The regex
            # below is for PLACING the highlight, not gating inclusion.
            # If the regex finds nothing, that's stemmer/diacritic drift
            # (porter+unicode61 on FTS5 side vs literal regex on Python
            # side) — we still emit a leading-text fallback snippet so
            # the user sees the FTS5 hit instead of an invisible drop.
            match = pattern.search(text)
            if match is not None:
                if context_size == "full":
                    snippet = text
                    start = match.start()
                    end = match.end()
                else:
                    snippet, start, end = create_snippet(
                        text, match.start(), match.end()
                    )
            else:
                if context_size == "full":
                    snippet = text
                else:
                    snippet = text[:_FALLBACK_SNIPPET_LEN] + (
                        "..." if len(text) > _FALLBACK_SNIPPET_LEN else ""
                    )
                start = 0
                end = 0
            # /compact trigger→marker rewrite + dedupe — see linear-scan
            # site comment above. Empty mapping for the common case;
            # ``dict.get(uuid, uuid)`` is a no-op when no mapping exists.
            raw_uuid = msg.get("uuid", "")
            emitted_uuid = trigger_to_marker.get(raw_uuid, raw_uuid)
            # v14 (2026-05-26): defense-in-depth compaction filter.
            # Apply AFTER the trigger→marker rewrite so a hit on a
            # stale trigger row redirected to a compaction marker uuid
            # is also caught — matches the linear-scan pattern at
            # search.py:1207.
            if emitted_uuid in compact_marker_uuids:
                continue
            if emitted_uuid in emitted_seen:
                continue
            emitted_seen.add(emitted_uuid)
            matching_messages.append(
                MessageSnippet(
                    message_uuid=emitted_uuid,
                    sender=msg.get("sender", ""),
                    snippet=snippet,
                    match_start=start,
                    match_end=end,
                    created_at=msg_created_at,
                )
            )

        if matching_messages:
            results.append(
                SearchResult(
                    conversation_uuid=conv.get("uuid", ""),
                    conversation_name=conv.get("name", "Untitled"),
                    conversation_updated_at=_parse_datetime(conv.get("updated_at")),
                    conversation_created_at=_parse_datetime(conv.get("created_at")),
                    project_name=_derive_project_name(conv.get("project_path")),
                    matching_messages=matching_messages,
                )
            )

    # Apply the same sort logic as the linear path (extracted into
    # _sort_results below so both code paths stay byte-identical).
    return _sort_results(results, sort=sort, sort_order=sort_order)


def _sort_results(
    results: list[SearchResult],
    *,
    sort: SortField,
    sort_order: SortOrder,
) -> list[SearchResult]:
    """Same sort logic as _search_via_linear_scan's tail block. Extracted
    so both paths share the implementation byte-for-byte.

    Bug B fix (V1 polish 2026-05-14, second attempt): for sort fields
    ``updated_at`` and ``created_at``, the conversation-level sort key
    is ``r.conversation_updated_at`` / ``r.conversation_created_at``
    EXACTLY — no max/min over matched-message timestamps.

    Why we removed the prior message-aware sort:

      * The UI displays the conversation's own ``updated_at`` in the
        date column of each result card (frontend SearchPanel.tsx
        renders ``match.createdAt`` first, then falls back to the
        conversation timestamp). The sort label in the same panel is
        "Last Activity" — users reasonably read that as conversation
        activity, not "newest matched message time".
      * The prior key ``max([m.created_at for m in matching_messages])``
        produced a user-visible inversion: a conversation updated
        yesterday whose matched message body is a month old would sort
        BELOW a conversation updated last week with a recent matched
        message. The user sees "yesterday" labeled card BELOW "last
        week" labeled card. Live reproduced 2026-05-14:
          curl /api/search?q=comprehensive+medium&sort=updated_at&sort_order=desc
        showed position 4 with conv_updated_at=2026-05-14 BELOW position 3
        with conv_updated_at=2026-05-01.

    Within-conversation message ordering is unchanged: messages inside
    a single result card group are still sorted by their per-message
    ``created_at`` (with fallback to conversation_updated_at for nulls),
    so multiple matches inside the same conversation show in time order.
    """
    reverse = sort_order == "desc"

    def _match_time(m: MessageSnippet, fallback):
        return m.created_at if m.created_at is not None else fallback

    # Hunt #12 — Unstable sort tiebreakers. Timsort is stable, but
    # "stable" only preserves INPUT order on ties, and the input order
    # here depends on upstream non-determinism (sqlite3 SELECT without
    # ORDER BY when FTS5 ranks tie; os.scandir/listdir filesystem walk
    # order in the linear-scan path; set iteration order under
    # PYTHONHASHSEED randomization). Without an explicit tiebreaker,
    # two results with identical primary keys silently flip order
    # between calls → UI flicker on refresh, pagination drift.
    # The UUID is stable, unique, non-null per the model, and invisible
    # to the user — it just pins the order. For string-primary sorts
    # (name, project) we slot the conversation timestamp BETWEEN the
    # primary key and the UUID so visually-identical-name conversations
    # cluster by time within the name group (Gemini-3-Pro UX call:
    # 20 "Untitled" conversations should not UUID-scatter).
    for r in results:
        fallback = r.conversation_updated_at
        r.matching_messages.sort(
            key=lambda m, fb=fallback: (_match_time(m, fb), m.message_uuid),
            reverse=reverse,
        )

    if sort in ("updated_at", "created_at"):
        def _conv_time_key(r: SearchResult):
            return (
                r.conversation_updated_at
                if sort == "updated_at"
                else r.conversation_created_at,
                r.conversation_uuid,
            )

        results.sort(key=_conv_time_key, reverse=reverse)
    elif sort == "name":
        results.sort(
            key=lambda r: (
                (r.conversation_name or "").lower(),
                r.conversation_updated_at,
                r.conversation_uuid,
            ),
            reverse=reverse,
        )
    elif sort == "project":
        results.sort(
            key=lambda r: (
                r.project_name is None,
                (r.project_name or "").lower(),
                r.conversation_updated_at,
                r.conversation_uuid,
            ),
            reverse=reverse,
        )

    return results