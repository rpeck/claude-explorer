"""Pure text-projection helpers shared by ``backend.search`` and
``backend.search_index``.

This module is a stdlib-only leaf — it imports nothing from the rest of the
``backend`` package. It exists to break the import cycle between
``backend.search`` and ``backend.search_index``: pre-cycle, ``search_index``
imported ``_extract_searchable_text`` from ``search`` at module load while
``search`` imported ``get_search_index`` from ``search_index`` (lazily, but
the structural defect remained). Extracting the three projection helpers
here makes ``search_index`` depend on this leaf only, and ``search`` may
keep its lazy import of ``search_index`` for the orthogonal reasons
documented at the call site (test patchability + ``ImportError``
fallback tolerance).

Public surface for the rest of the codebase is preserved: ``backend.search``
re-exports all three symbols, so existing test imports
(``from backend.search import _extract_searchable_text``) keep working
byte-for-byte.
"""

import re
from typing import Any

# Placeholder pattern used by Claude Desktop when a content block (tool call,
# canvas widget, etc.) can't be rendered in the current client. Mirrors the
# frontend's filter in frontend/src/lib/utils.ts (filterToolPlaceholders): when
# we render a message with showToolCalls=false, this exact pattern is stripped
# from the displayed text. We strip the same pattern from the searchable text
# projection when include_tool_calls=False so that a message whose `text`
# field consists ONLY of this placeholder is treated as "no visible text"
# (mirrors messageHasVisibleContent semantics).
_TOOL_PLACEHOLDER_RE = re.compile(
    r"```\s*\n?\s*(?:"
    r"This block is not supported on your current device yet\."
    r"|"
    r"Viewing artifacts created via the Analysis Tool web feature preview "
    r"isn't yet supported on mobile\."
    r")\s*\n?\s*```"
)


# /compact trigger-row envelope (2026-05-23). When the user runs a manual
# ``/compact``, Claude Code emits TWO related JSONL rows: the synthetic
# ``isCompactSummary: true`` summary message (rendered by the frontend as
# the compact-marker pill), AND a SECOND user row carrying the replayed
# slash-command envelope (``<command-name>/compact</command-name>`` plus
# the user's verbatim prompt inside ``<command-args>``). The second row is
# CHROME — the marker pill is the user-facing surface. Indexing the
# trigger row's body produces search hits on the user's own prompt text
# that land on the WRONG message_uuid (the trigger row's, not the
# marker's), defeating the frontend's compact-marker auto-expand chain
# (which keys on ``compact_marker.message_uuid``).
#
# Constant equivalence: this literal MUST match ``_COMPACT_COMMAND_NAME``
# in :mod:`backend.cc_image_markers` (the existing extract_compact_markers
# pass uses the same literal for lookahead classification). Defined here
# (not imported from cc_image_markers) to preserve the stdlib-only-leaf
# layering invariant documented in this module's docstring. A behavioral
# coupling test in test_search_compact_trigger_rewrite.py pins the offset
# semantics in lockstep so the two constants cannot silently drift.
_COMPACT_TRIGGER_NAME = "<command-name>/compact</command-name>"


def _is_compact_trigger_message(message: dict[str, Any]) -> bool:
    """True iff this message is the user-typed ``/compact`` trigger row.

    A manual /compact produces THREE related artifacts in CC JSONL:

      1. ``isCompactSummary: True`` synthetic user message — the LLM's
         compaction summary. The frontend's auto-expand chain keys on
         this row's UUID. This row IS searchable (its summary body is
         useful content; users should be able to navigate to it).
      2. THE TRIGGER ROW: a regular user message with text wrapping the
         ``<command-name>/compact</command-name>`` envelope plus
         ``<command-args>{user_prompt_verbatim}</command-args>``. This
         row is chrome — the runtime's replay of what the user typed.
         The marker (#1) is the user-facing surface; the trigger has
         no UI of its own.
      3. The user's typed prose, which lives ONLY inside the trigger
         row's ``<command-args>`` block (no other JSONL row carries
         that text).

    This predicate identifies #2 so the indexer and the linear-scan
    fallback can skip its body. A search for words the user typed in
    their own compact prompt would otherwise land on the trigger row's
    UUID, and the frontend's compact-marker auto-expand would not fire
    (it keys on the marker UUID, not the trigger).

    Detection rule: the message must be a USER message AND carry the
    literal ``<command-name>/compact</command-name>`` envelope somewhere
    in its text payload. The sender guard prevents accidental
    suppression of an assistant that quotes the envelope (e.g. in a
    debugging transcript). The literal-substring check is exactly the
    same rule :func:`backend.cc_image_markers.extract_compact_markers`
    uses to classify a marker as ``manual`` (it scans forward looking
    for the same string within an 8-message lookahead window), so the
    two passes cannot drift on what counts as a trigger row.

    Both message surfaces are checked: ``message["text"]`` (the flat
    string set by ``store._parse_message``) AND every text-type content
    block. CC sometimes emits the envelope as a single text block
    instead of a flat string (see
    test_extract_compact_markers_list_content_blocks in
    backend/tests/test_compact_markers.py), so both surfaces matter.

    Defensive against missing keys / None values: ``message.get("text")``
    returns None on missing key; the ``or ""`` coerces None to a safe
    empty string before the substring check. Consulted from the hot
    indexing path AND the scatter-gather rewrite — any TypeError would
    crash search.
    """
    if message.get("sender") != "human":
        return False
    text = message.get("text") or ""
    if isinstance(text, str) and _COMPACT_TRIGGER_NAME in text:
        return True
    # Fall back to scanning text blocks (CC sometimes emits the envelope
    # only inside the content array, with an empty top-level text).
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        btext = block.get("text") or ""
        if isinstance(btext, str) and _COMPACT_TRIGGER_NAME in btext:
            return True
    return False


def _extract_searchable_text(
    message: dict[str, Any],
    *,
    include_tool_calls: bool = True,
) -> str:
    """Flatten every searchable surface of a message into one string.

    Covers: message['text'] (Desktop API plain text), and all content blocks —
    text, tool_use input dicts (Bash command, file paths, prompt args), and
    tool_result content (which can be a string OR a list of text blocks).

    When ``include_tool_calls=False``:
      * Skips ``tool_use`` and ``tool_result`` content blocks.
      * Strips the Desktop "This block is not supported…" placeholder from
        ``message['text']`` (mirrors frontend ``messageHasVisibleContent``
        and ``filterToolPlaceholders``). A message whose ``text`` field is
        only that placeholder yields the empty string here.
      * ``text``-type blocks are unchanged — they ARE the user-visible body.

    ``thinking`` blocks are NEVER indexed regardless of the toggle
    (V1 polish 2026-05-13): the frontend has no `case 'thinking':`
    renderer in V1, so indexing thinking content produces search
    "ghosts" — a query that hits inside a thinking block returns a
    result whose bubble shows nothing matching. Until a `Show thinking`
    UI affordance ships, hide it from search too. Spec invariant: search
    only returns hits the user can navigate to. The accompanying
    `backend/search_index.SCHEMA_VERSION` bump (2 → 3) forces a one-time
    rebuild so stale thinking-only matches don't pollute the FTS5
    top-N ranking.

    Default ``include_tool_calls=True`` preserves prior indexing behavior
    for tool blocks; the FTS5 index ALWAYS uses the full projection so
    the index stays correct regardless of the per-query filter (the
    filter is applied at snippet/scatter time, not at index time — see
    search.py module docstring on the "include_tool_calls" architecture).

    Argless command-marker exclusion (V1 polish 2026-05-13):
    Argless slash markers (``is_command_marker=True``: ``/exit``, ``/clear``,
    ``/compact`` and the leading-prelude rows that ``_flag_leading_prelude_markers``
    flags on top of them) are CHROME, not user content. The viewer hides them
    behind ``SessionPreludeAffordance`` / ``SlashCommandBadge`` and the export
    surfaces drop them via ``export._is_excludable_marker``. The search
    projection mirrors that exclusion here — typing ``exit`` in the search
    box should NOT produce hits on ``Session: /exit`` chrome rows.

    Predicate equivalence with ``export._is_excludable_marker`` (export.py:159)
    is INTENTIONAL: both surfaces apply the same definition of "chrome" so the
    spec invariant "one truth, three surfaces" (viewer + search + export) holds.
    Argful markers (``/coding <prose>``, ``/plan <prose>``) carry
    ``is_command_marker=False`` post-Fix-2 (claude_code_reader 2026-05-13), so
    they pass through this guard and remain searchable on the user's real
    prose body AND on the ``slash_command`` token.

    Strict ``is True`` check (not truthy): defends against non-bool injections
    (e.g. ``"false"`` string, ``1`` int) from fixtures or future code paths that
    might silently exclude legitimate messages. Production data is always bool
    per the Pydantic ``Message`` model (models.py) and the CC ingester
    (claude_code_reader.py:342 sets it as a real boolean), but the function
    signature is ``dict[str, Any]`` so we don't trust truthiness.

    Index-time side effect (paired with SCHEMA_VERSION bump 3 → 4 in
    search_index.py): ``upsert_conversation`` writes ``body=""`` for these
    rows, so they contribute no tokens to the FTS5 inverted index. Title is
    still populated, so unqualified MATCH on a conversation title is still
    correct — the title pseudo-message comes from the `_search_via_index`
    title-sweep, not from marker-row body matches.
    """
    if message.get("is_command_marker") is True:
        return ""

    # /compact trigger row exclusion (2026-05-23). See
    # :func:`_is_compact_trigger_message` for the full rationale. Mirrors
    # the argless-marker early-return above: the body is unwanted at
    # index time AND at linear-scan-fallback time. Drops the entire row
    # — including the inert ``<command-message>`` preamble and the user
    # prompt inside ``<command-args>`` — so search hits on the user's
    # own prompt text can never land on the trigger row's UUID. The
    # corresponding compact-marker (isCompactSummary) row is indexed
    # normally and remains the only navigable target for compact-search
    # hits.
    #
    # The early-return MUST fire before the include_tool_calls branch
    # so toggling tool visibility cannot re-include the trigger row.
    # Pinned by test_extract_searchable_text_exclusion_holds_in_both_projection_modes.
    if _is_compact_trigger_message(message):
        return ""

    parts: list[str] = []

    # Dedupe contract (2026-05-18, doubled-snippet bug):
    # ``backend/store._parse_message`` populates ``Message.text`` from
    # ``raw.get("text", "") or _extract_text(content)``. For Claude
    # Code AND Desktop messages, the resulting ``text`` field is the
    # newline-join of every text-type content block. If we then also
    # append each content block's text inside the loop below, the
    # indexed body carries the prose twice (``"X\nX"``). FTS5's
    # ``snippet()`` echoes the duplication and the search panel
    # renders each hit twice.
    #
    # Mirror the frontend ``MessageBubble`` renderer instead: when
    # ``content`` has at least one text block, treat the blocks as
    # the canonical source and skip ``message['text']``. Fall back
    # to ``text`` only when no text blocks exist — bare-text legacy
    # shapes (Desktop messages with no ``content`` array, or messages
    # whose content is only ``tool_use``/``tool_result``/``image``
    # blocks) still need the field as the sole index source.
    content_blocks = message.get("content") or []
    has_text_block = any(
        isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        for b in content_blocks
    )

    text = message.get("text") or ""
    if text and not has_text_block:
        if not include_tool_calls:
            # Mirror frontend filterToolPlaceholders so a message whose
            # `text` is ONLY a tool placeholder is correctly treated as
            # empty when the user has hidden tool calls.
            text = _TOOL_PLACEHOLDER_RE.sub("", text).strip()
        if text:
            parts.append(text)

    # CC slash-command name (V1 polish round 3, 2026-05-12). Set by the
    # triplet collapser on synthetic "Session: /foo" markers AND on
    # argful markers where `message["text"]` is the user's prompt body.
    # We append the "/foo" string so both surface forms are searchable:
    #   * Literal substring `/coding` hits via linear-scan regex.
    #   * FTS5's `unicode61` tokenizer splits on `/` so the token
    #     `coding` ALSO appears in the indexed projection — a user
    #     searching for either form gets the marker.
    # MUST guard against None: an unguarded `parts.append(None)` would
    # raise TypeError in the trailing `"\n".join(parts)`. The truthy
    # guard also drops empty-string values without poisoning the index
    # with a stray "None" literal.
    slash_command = message.get("slash_command")
    if slash_command:
        parts.append(slash_command)

    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        if btype == "text":
            t = block.get("text") or ""
            if t:
                parts.append(t)

        elif btype == "tool_use":
            if not include_tool_calls:
                continue
            name = block.get("name") or ""
            if name:
                parts.append(name)
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                parts.append(_stringify_tool_input(tool_input))
            elif isinstance(tool_input, str):
                parts.append(tool_input)

        elif btype == "tool_result":
            if not include_tool_calls:
                continue
            tr_content = block.get("content")
            if isinstance(tr_content, str):
                parts.append(tr_content)
            elif isinstance(tr_content, list):
                for sub in tr_content:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        t = sub.get("text") or ""
                        if t:
                            parts.append(t)

        # `thinking` blocks: deliberately NOT indexed (V1 polish 2026-05-13).
        # The frontend has no renderer for `thinking` content blocks
        # (see frontend/src/components/message/MessageBubble.tsx
        # ContentBlockRenderer — only 'text', 'tool_use', 'tool_result',
        # 'image' branches). Indexing thinking would produce search hits
        # that map to bubbles where the matching text is invisible —
        # a confusing UX failure mode. Re-add this branch (gated by a
        # new `include_thinking` setting wired through SettingsContext +
        # preferences + a header toggle + the search query) when a
        # "Show thinking" affordance ships.

    return "\n".join(parts)


def _stringify_tool_input(tool_input: dict[str, Any]) -> str:
    """Render a tool_use input dict so its keys AND string values are
    searchable, WITHOUT duplicating any value-text.

    Two search axes, no overlap (Option C, SCHEMA_VERSION v8→v9):

      * **Keys axis** — every dict key reachable (top-level and nested)
        is emitted once, space-joined into a single line. A user query
        like ``command`` or ``file_path`` hits this line via the FTS5
        unicode61 tokenizer.
      * **Values axis** — every unique string value at any depth is
        emitted on its own line. A user query like ``echo hello`` hits
        a value line directly.

    Pre-v9 implementation used ``json.dumps(tool_input)`` (which already
    contained both keys AND values, including nested) AND ALSO appended
    each top-level string value verbatim. The overlap surfaced as
    user-visible doubled snippets on every tool-call search hit (e.g.
    ``"echo hello\\necho hello"``). FTS5's ``snippet()`` echoed the
    duplication faithfully. See:
      backend/tests/test_search_extract_no_double_index.py::
        test_tool_input_value_appears_once_in_body

    Set-based dedupe across the value axis (rare but possible: an
    ``Edit`` block with ``old_string == new_string``, a tool that
    repeats the same path across multiple keys) preserves the
    one-occurrence contract that the bidirectional tests pin.
    """
    keys: list[str] = []
    values: list[str] = []
    seen_values: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str):
                    keys.append(k)
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str):
            if obj and obj not in seen_values:
                seen_values.add(obj)
                values.append(obj)

    walk(tool_input)

    parts: list[str] = []
    if keys:
        parts.append(" ".join(keys))
    parts.extend(values)
    return "\n".join(parts)
