"""Message-pipeline transforms for Claude Code JSONL conversations.

This module owns the stateless, in-memory transforms that turn a raw
list of JSONL entries (already parsed into dicts by
:mod:`backend.cc_jsonl_io`) into the final ``chat_messages`` list that
the frontend renders. None of these functions touch the filesystem.

Layering:
  * Pure leaf module — depends only on ``re``, ``datetime``, and
    standard-library bits.
  * Imported by ``backend.cc_jsonl_io`` (for ``_is_system_message`` +
    ``_extract_title_from_message`` + ``_get_message_text`` used by the
    fast metadata reader), by ``backend.cc_image_markers`` (for
    ``_get_message_text``), and by the facade module
    ``backend.claude_code_reader`` (for the full transform pipeline
    inside ``read_claude_code_conversation``).

Pipeline order (as orchestrated by the facade):
  1. ``_merge_entries_to_message`` — fold CC's streaming-chunk entries
     into one message per logical turn.
  2. ``_collapse_local_command_triplets`` — replace
     <local-command-caveat>/<command-name>/<local-command-stdout>
     runs with a single synthetic marker.
  3. ``_fold_canned_assistant_responses_into_marker`` — absorb CC's
     canned ``"No response requested."`` assistant rows into the
     preceding marker.
  4. ``_flag_leading_prelude_markers`` — flag the leading run of argless
     markers with ``is_prelude: True``.

Title helpers (``_title_from_entry``, ``_extract_title_from_message``)
and the system-message classifier (``_is_system_message``) live here
because they are pure text-classification helpers operating on
already-parsed dicts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .agent_session_io import (  # noqa: F401  re-export
    _convert_entry_to_message,
    _get_message_key,
    _get_message_text,
    _merge_entries_to_message,
)
from .parsing import _parse_iso_opt


def cc_session_uuid(jsonl_path: Path) -> str:
    """The conversation uuid of a Claude Code session file: its file name.

    Claude Code names each file after its own session id. A continued
    session's file can start with rows that carry the id of the session
    it continues, so the first row's ``sessionId`` named the wrong
    session: two files claimed one uuid, the list hid one of them, and
    search hits opened the other file (2026-10-21,
    ``backend/tests/test_cc_continued_session_identity.py``). Every reader
    and the search index use this function.
    """
    return jsonl_path.stem


# Local-command boilerplate collapse (V1 polish, 2026-05-12):
# Claude Code emits a 1-3 row triplet around every slash command:
#   1. <local-command-caveat>Caveat: ...</local-command-caveat>
#   2. <command-name>/foo</command-name><command-args>...</command-args>
#   3. <local-command-stdout>...</local-command-stdout>
# Rows 1 and 3 are pure boilerplate. Row 2 carries the actual command.
# Raw, these dominate the top of any session that started with /exit or /clear.
# We collapse the consecutive run into a single synthetic marker on the FIRST
# row's slot (keeping its uuid for parent_message_uuid bridging) and drop the
# rest. /compact is handled separately by `extract_compact_markers` — we skip
# it here so the two systems don't fight.
#
# Args-preservation extension (V1 polish round 3, 2026-05-12):
# Slash commands like /coding, /plan, /compact, /metaprompt carry the
# USER'S REAL PROMPT inside <command-args>...</command-args>. The original
# collapser threw those args away, leaving an empty "Session: /coding"
# marker bubble — silent data loss of the user's actual message text.
# We now ALSO extract the args body and use it as the marker's `text`
# when non-empty (fallback to "Session: /foo" only when args is empty
# or absent). A separate `slash_command` field carries the command name
# so the frontend can render a small `[/coding]` badge above the body.
#
# REGEX CHOICES:
#   * `_LOCAL_CMD_NAME_RE`: unchanged — name is a non-whitespace, non-`<`
#     token following `<command-name>`. The leading `/` is required.
#   * `_LOCAL_CMD_ARGS_RE`: non-greedy body anchored to a domain-specific
#     trailing-context lookahead. Reads as:
#         <command-args>(.*?)</command-args>(?=\s*(?:<command-name>|$))
#     i.e. capture the shortest body that ends at a `</command-args>`
#     CLOSE TAG which is followed by either (a) the start of the NEXT
#     command triplet `<command-name>`, or (b) end-of-row. This:
#       1) Bug 1 (V1 polish, 2026-05-14): tolerates a literal
#          `<command-name>` SUBSTRING inside the args body (e.g. the user
#          pastes a prompt explaining CC's XML format). The previous
#          tempered-greedy regex `((?:(?!<command-name>).)*)` forbade
#          this and returned None, silently dropping the user's prompt
#          and falling back to the argless marker label.
#       2) Preserves the original user-paste tolerance: a literal
#          `</command-args>` inside the args body is NOT a valid match
#          terminator unless what follows it is `\s*<command-name>` or
#          end-of-row — so a body of `Explain </command-args> means...`
#          inside a wrapper still anchors at the REAL wrapper close at
#          end-of-row.
#       3) Bounds multi-block rows correctly: in a row with two glued
#          triplets `...</command-args><command-name>...</command-name>
#          <command-args>...`, the lookahead matches at the FIRST close
#          (next char is `<command-name>`), so the args body correctly
#          stops at the first block — no over-match across blocks.
#       4) Why anchor on `<command-name>` specifically and not a generic
#          `<|$`? GPT-5.2 (LLM council, 2026-05-14) flagged that a
#          permissive `<|$` lookahead would mis-terminate when the args
#          body itself ends with a literal `</command-args>` immediately
#          before the real wrapper close (`...</command-args></command-args>`
#          shape) — the next char is `<` but it's the wrapper close, not
#          a new triplet. Tying the lookahead to the actual sentinel
#          (`<command-name>`) keeps the parser pinned to the real
#          structural boundary CC emits and avoids that whole class of
#          truncation bugs.
#     Performance: classic non-greedy `(.*?)` plus a fixed-length
#     lookahead. No nested quantifiers ⇒ no catastrophic-backtracking
#     shape. Worst case is O(n·k) where k is the number of candidate
#     close tags that fail the lookahead — fine for typical args bodies
#     of 100–1000 chars.
_LOCAL_CMD_NAME_RE = re.compile(r"<command-name>(/[^<\s]+)", re.DOTALL)
_LOCAL_CMD_ARGS_RE = re.compile(
    r"<command-args>(.*?)</command-args>(?=\s*(?:<command-name>|$))",
    re.DOTALL,
)


def _local_command_kind(text: str) -> str | None:
    """Classify a message-text string as local-command boilerplate.

    Returns one of: 'caveat', 'command', 'stdout', 'stderr', or None.
    The caller uses this to detect adjacent runs that should be collapsed
    into a single marker. Pattern set mirrors `_is_system_message` so the
    two stay in sync; if you add a new pattern there, add it here too.
    """
    if not isinstance(text, str):
        return None
    if text.startswith(("<local-command-caveat>", "Caveat: The messages below were generated")):
        return "caveat"
    if text.startswith("<command-name>"):
        return "command"
    if text.startswith("<local-command-stdout>"):
        return "stdout"
    if text.startswith("<local-command-stderr>"):
        return "stderr"
    return None


def _extract_local_command_name(text: str) -> str | None:
    """Pull the `/foo` slash-command name out of a <command-name> block."""
    if not isinstance(text, str):
        return None
    m = _LOCAL_CMD_NAME_RE.search(text)
    return m.group(1) if m else None


def _extract_local_command_name_and_args(text: str) -> tuple[str | None, str | None]:
    """Pull both the `/foo` name and the `<command-args>...</command-args>`
    body out of a single command row.

    Returns `(name, args)` where either may be None:
      * `name` is None when the row has no <command-name>/foo block (e.g.
        an orphan caveat run with no command-name row at all).
      * `args` is None when the row has no `<command-args>` block. It is
        `""` when the block exists but is empty (`<command-args></command-args>`,
        which is what CC emits for argless commands like /exit).

    The caller distinguishes "no args" (None) from "empty args" (""):
      * BOTH fall through to the "Session: /foo" label in the marker.
      * Only a NON-EMPTY (post-strip) args body becomes the marker's text.
    """
    if not isinstance(text, str):
        return None, None
    m_name = _LOCAL_CMD_NAME_RE.search(text)
    m_args = _LOCAL_CMD_ARGS_RE.search(text)
    name = m_name.group(1) if m_name else None
    args = m_args.group(1) if m_args else None
    return name, args


def _collapse_local_command_triplets(messages: list[dict]) -> list[dict]:
    """Collapse runs of local-command boilerplate into a single marker.

    Walks `messages` (already in chronological order, output of
    `_merge_entries_to_message`). A "run" is a maximal sequence of adjacent
    messages whose text classifies as local-command boilerplate (caveat,
    command, stdout, stderr) per `_local_command_kind`. Each run is replaced
    with a SINGLE synthetic marker message that:

      * Reuses the FIRST run-member's uuid + parent_message_uuid (so any
        downstream message that links to it via parent_message_uuid keeps
        a valid pointer — this is the "bridge" Gemini-3-Pro flagged in the
        council review). The frontend currently renders by array order, not
        by parent_message_uuid, so this is a belt-and-suspenders safeguard.
      * Carries a short, clean `text` like `"Session: /exit"` (or generic
        `"Session: Local command"` if no <command-name> row was found in the
        run — e.g. an orphan caveat).
      * Sets `is_command_marker: true` so the frontend can style it muted
        (italic / smaller / etc.) — but the field is optional; if the
        frontend ignores it the bubble just renders as a short user message.
      * Skips runs that look like /compact — those are owned by the existing
        `extract_compact_markers` system and have their own UI affordance.

    Bidirectional guarantees:
      * A session with `[caveat, command(/exit), stdout, real_user, assistant]`
        collapses to `[marker(/exit), real_user, assistant]` (3 -> 1).
      * A session with `[caveat]` alone (pure phantom) collapses to
        `[marker(local-command)]` — never blanks the view. This preserves
        `test_phantom_caveat_carveout.py:140`'s `len(chat_messages) >= 2`
        invariant when paired with a real message.
    """
    if not messages:
        return messages

    result: list[dict] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        # Only USER messages can be boilerplate (CC emits these as type:"user").
        if msg.get("sender") != "human":
            result.append(msg)
            i += 1
            continue

        kind = _local_command_kind(msg.get("text", ""))
        if kind is None:
            result.append(msg)
            i += 1
            continue

        # Start of a run. Scan forward while consecutive user messages
        # remain boilerplate. Also break on /compact (owned by the
        # compact-marker system).
        run_start = i
        run_end = i  # inclusive
        command_name: str | None = None
        # `command_args` follows the same three-state convention as
        # `_extract_local_command_name_and_args`: None means no
        # `<command-args>` block was seen in the run; "" means the block
        # was present but empty (CC's argless triplet); a non-empty
        # string is the real user-supplied args body.
        command_args: str | None = None
        is_compact_run = False
        j = i
        while j < n:
            cand = messages[j]
            if cand.get("sender") != "human":
                break
            ctext = cand.get("text", "")
            ckind = _local_command_kind(ctext)
            if ckind is None:
                break
            if ckind == "command":
                cname, cargs = _extract_local_command_name_and_args(ctext)
                if cname == "/compact":
                    # Leave the /compact triplet alone — it's tracked by
                    # the parallel compact_markers system and has its own UI.
                    is_compact_run = True
                    break
                if cname:
                    command_name = cname
                # Only overwrite `command_args` from a row that ACTUALLY
                # carried a <command-args> block. None means "no block in
                # this row" and must not clobber a previously-seen value
                # — though in practice each run has at most one command
                # row, this guard keeps the invariant explicit.
                if cargs is not None:
                    command_args = cargs
            run_end = j
            j += 1

        if is_compact_run:
            # Pass through every message in the consumed prefix unchanged
            # PLUS the /compact trigger row itself so the compact-markers
            # pipeline gets to handle it. Crucial: we must advance i past
            # the trigger row (j) — even if j == run_start (i.e. the run
            # STARTED with /compact and we consumed nothing before it),
            # leaving i = j would loop forever on the same row.
            for k in range(run_start, j + 1):
                result.append(messages[k])
            i = j + 1
            continue

        first = messages[run_start]
        last = messages[run_end]
        # Args-preservation logic (V1 polish round 3): when the user
        # supplied real prompt text via /coding <args>, surface it as the
        # marker's `text` so the bubble carries the user's actual content
        # (the original collapser threw args away). Empty / whitespace-
        # only args fall back to the "Session: /foo" label so an argless
        # /exit doesn't render a blank bubble.
        #
        # `is_argless` is the SOURCE OF TRUTH for the prelude-flag pass.
        # We do NOT detect argless markers by comparing text strings later
        # — that would mis-flag a user whose args literally happen to be
        # `"Session: /exit"` (rare but real, e.g. a bug report about CC's
        # own boilerplate). The flag is set here, once, definitively.
        args_stripped = (command_args or "").strip()
        is_argless = not args_stripped
        if args_stripped:
            text_payload = args_stripped
        elif command_name:
            text_payload = f"Session: {command_name}"
        else:
            text_payload = "Session: Local command"
        marker = {
            "uuid": first.get("uuid", ""),
            "sender": "human",
            "text": text_payload,
            "content": [{"type": "text", "text": text_payload}],
            "created_at": first.get("created_at", ""),
            "updated_at": last.get("updated_at", first.get("created_at", "")),
            "truncated": False,
            "parent_message_uuid": first.get("parent_message_uuid"),
            "attachments": [],
            "files": [],
            # V1 polish (2026-05-13): `is_command_marker` is True ONLY for
            # argless markers (/exit, /clear, etc. — pure conversational
            # chrome). Argful markers (/coding <prompt>, /plan <prose>) get
            # is_command_marker=False because they carry REAL user content
            # and downstream consumers (export filter, prelude pass, future
            # accessibility "skip markers" affordance) MUST NOT treat them
            # as noise to suppress. Spec invariant X1: argful is never a
            # marker. The badge still renders for both (driven by
            # `slash_command`, not by this flag).
            "is_command_marker": is_argless,
            # `slash_command` is the public, serialized field consumed by
            # the frontend `<SlashCommandBadge />`. None for orphan-caveat
            # runs (no <command-name> row found) — the frontend's
            # `if (message.slash_command)` guard correctly skips the badge
            # in that case.
            "slash_command": command_name,
            # `_argless_marker` is a PRIVATE, leading-underscore flag
            # consumed only by `_flag_leading_prelude_markers` below. It
            # is NOT declared on the Pydantic `Message` model, so it gets
            # silently dropped at serialization time (Pydantic v2 default
            # `extra='ignore'`). That's intentional — this is internal
            # state between two backend passes, not part of the public
            # API surface.
            "_argless_marker": is_argless,
        }
        result.append(marker)
        i = run_end + 1

    return result


# Canned-response fold + leading-prelude flag (V1 polish, 2026-05-12, council
# round 2): after `_collapse_local_command_triplets` turns each /exit triplet
# into a single `is_command_marker: True` marker, the JSONL ALSO contains the
# orphan assistant row CC emits in reply: an `assistant` message whose text
# is exactly the literal string `"No response requested."`. Raw, the top of
# a session that opened with two /exit runs looks like
#   [marker, "No response requested.", marker, "No response requested.",
#    real_user]
# which is confusing. The fold below absorbs each canned-response assistant
# into the preceding marker; the prelude-flag pass then marks the leading
# run of markers with `is_prelude: True` so the frontend can hide them
# behind a "Session prelude: N earlier /exit runs (show)" affordance.
#
# Deliberate conservatism:
#   * Fold matches ONLY the exact (post-`.strip()`) string. A future CC
#     version that changes the wording falls back to rendering the bubble
#     verbatim — visible, fixable, no silent erasure.
#   * Fold refuses to consume an assistant that carried tool_use /
#     tool_result blocks, even if the text happens to match — those carry
#     real history we MUST NOT drop.
#   * Both passes are pure-functional: shallow-copy `out` list, shallow-copy
#     only the dicts we modify. Input list and its member dicts are never
#     mutated. Callers can safely reuse the original list.
#   * Both passes are idempotent: a second run of the fold finds no
#     marker→canned-assistant pair (the assistant is gone); a second run of
#     the flag finds the same prefix and sets the same flags.
#
# Why a SEPARATE pass instead of folding into `_collapse_local_command_triplets`?
# The triplet collapse runs over USER messages only (CC emits the boilerplate
# as `type:"user"` rows); the canned response is an `assistant` row. Keeping
# them as ordered passes makes each invariant testable in isolation.
_CANNED_NO_RESPONSE_TEXT = "No response requested."


def _is_canned_no_response_assistant(msg: dict) -> bool:
    """True iff `msg` is an assistant whose ENTIRE text content is CC's
    literal canned-response string.

    Conservative guards: sender must be 'assistant', text must be a str whose
    post-strip value equals the canned string exactly, and the content array
    must contain only text blocks (no tool_use/tool_result — those carry real
    history and we MUST NOT drop them, even if the text happens to match).
    """
    if msg.get("sender") != "assistant":
        return False
    text = msg.get("text")
    if not isinstance(text, str):
        return False
    if text.strip() != _CANNED_NO_RESPONSE_TEXT:
        return False
    for block in msg.get("content", []):
        if not isinstance(block, dict):
            return False
        if block.get("type") != "text":
            return False
    return True


def _fold_canned_assistant_responses_into_marker(messages: list[dict]) -> list[dict]:
    """Absorb each `"No response requested."` assistant row into the preceding
    `is_command_marker: True` marker.

    Behavior:
      * When a fold happens, the marker gets
        `assistant_canned_response_consumed: True` and the absorbed assistant
        is dropped from the output list.
      * Handles a RUN of consecutive canned-response assistants after a single
        marker (CC glitch case): all are absorbed.
      * The first non-canned successor's `parent_message_uuid` is remapped
        from any absorbed assistant's uuid to the marker's uuid, so a
        downstream parent-chain walker (export pipelines, tree builders)
        doesn't lose its anchor.

    Pure-functional contract: the caller's list and the caller's dicts are
    NEVER mutated. We shallow-copy the list immediately on entry so any
    in-place rewrites stay local; we shallow-copy individual dicts only
    when we need to set new keys on them.
    """
    if not messages:
        return list(messages)

    # Local copy of the LIST so we can rewrite slots (the parent-chain
    # remap below) without mutating the caller's list. We never mutate
    # the caller's dicts — those are also shallow-copied at modification.
    local: list[dict] = list(messages)
    out: list[dict] = []
    i = 0
    n = len(local)
    while i < n:
        cur = local[i]
        if cur.get("is_command_marker") is not True:
            out.append(cur)
            i += 1
            continue

        # Scan forward for a run of consecutive canned-response assistants.
        # run_end is the LAST absorbed index (inclusive); equals i when no
        # fold should happen.
        run_end = i
        j = i + 1
        while j < n and _is_canned_no_response_assistant(local[j]):
            run_end = j
            j += 1

        if run_end == i:
            out.append(cur)
            i += 1
            continue

        marker = dict(cur)
        marker["assistant_canned_response_consumed"] = True
        out.append(marker)

        # Parent-chain bridge: build the set of absorbed-assistant uuids
        # (excluding None so a missing-uuid pair can't false-positive on
        # `None == None`). If the first surviving successor's
        # `parent_message_uuid` is in that set, rewrite the local slot
        # with a shallow-copied dict that points at the marker's uuid.
        absorbed_uuids = {
            local[k].get("uuid")
            for k in range(i + 1, run_end + 1)
            if local[k].get("uuid")
        }
        successor_idx = run_end + 1
        if successor_idx < n and absorbed_uuids:
            successor = local[successor_idx]
            if successor.get("parent_message_uuid") in absorbed_uuids:
                successor = dict(successor)
                successor["parent_message_uuid"] = marker.get("uuid")
                local[successor_idx] = successor

        # Advance past the absorbed run ONLY. Do NOT pre-emit the successor —
        # the main loop's next iteration picks it up and may fold AGAIN if
        # it's another marker (the canonical multi-/exit session looks like
        # [marker, canned, marker, canned, real] and needs two folds).
        i = run_end + 1

    return out


def _flag_leading_prelude_markers(messages: list[dict]) -> tuple[list[dict], int]:
    """Flag the leading run of ARGLESS `is_command_marker` messages with
    `is_prelude: True`. Returns `(new_list, prelude_hidden_count)`.

    Stops at the FIRST message that is either:
      * NOT a command marker (real user/assistant turn or /compact summary
        the triplet-collapser passed through), OR
      * an ARGFUL command marker — i.e. a /coding/ /plan/ /metaprompt/etc
        marker whose `text` carries the user's REAL prompt body. Hiding
        those behind the prelude affordance would silently bury real user
        content; the whole point of args preservation is to surface it.

    ARGLESS/ARGFUL DETECTION
    ------------------------
    We read the PRIVATE `_argless_marker` flag set by
    `_collapse_local_command_triplets` at marker-construction time. The
    flag is the source of truth and resistant to user-injected
    "Session: /foo" args text. Three cases:

      * `_argless_marker is True`  → argless; FLAG as prelude.
      * `_argless_marker is False` → argful; BREAK the prelude run.
      * Key absent                 → legacy fixture path. Fall back to
        the pre-args-preservation behavior: any leading command marker
        is prelude. This keeps pre-existing tests that synthesize
        markers WITHOUT the flag (e.g.
        `test_canned_response_fold_and_prelude.py::_marker`) working.
        REAL markers produced by the collapser always have the flag,
        so this fallback never fires on production data.

    Pure-functional: shallow-copies only the flagged dicts; non-flagged
    entries are reference-equal in the output. The `_argless_marker`
    key remains on the dict but is stripped at serialization time by
    Pydantic v2's default `extra='ignore'` (the `Message` model never
    declares it).
    """
    out: list[dict] = []
    count = 0
    in_prefix = True
    for msg in messages:
        if in_prefix and msg.get("is_command_marker") is True:
            argless_flag = msg.get("_argless_marker")
            # True → argless (flag); False → argful (break);
            # None/absent → legacy fixture path, fall back to "flag
            # any leading marker" pre-args-preservation behavior.
            if argless_flag is True or argless_flag is None:
                flagged = dict(msg)
                flagged["is_prelude"] = True
                out.append(flagged)
                count += 1
                continue
        in_prefix = False
        out.append(msg)
    return out, count


# Title-event rule (mirrors the existing "last summary wins" precedent —
# CC JSONLs are append-only chronological logs, so the most recent title
# event represents the active truth). CC writes these row types:
#   - `custom-title` with `customTitle`  (emitted by /rename in newer CCs)
#   - `agent-name`   with `agentName`    (sibling row, emitted alongside)
#   - `summary`      with `summary`      (auto-generated, older CCs)
# We treat ALL three as title events and pick the chronologically last
# valid (non-empty, stripped) one. Empty strings fall through to the
# next-most-recent candidate so a stray empty-customTitle row from a
# future CC version can't blank the title.
_TITLE_FIELD_BY_TYPE: dict[str, str] = {
    "custom-title": "customTitle",
    "agent-name": "agentName",
    "summary": "summary",
}


def _title_from_entry(entry: dict) -> str | None:
    """Return the trimmed title text for any title-emitting row, or None."""
    field = _TITLE_FIELD_BY_TYPE.get(entry.get("type", ""))
    if not field:
        return None
    raw = entry.get(field)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _is_system_message(entry: dict) -> bool:
    """Check if a user entry is a system message (Caveat, bash I/O, tool results, commands)."""
    msg = entry.get("message", {})
    content = msg.get("content", "")

    # Check for tool_result blocks (these are not real user messages)
    if isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
        return True

    text = _get_message_text(entry)

    # Skip system-generated messages and command infrastructure
    return (
        text.startswith(
            (
                "Caveat: The messages below were generated",
                "<local-command-caveat>",
                "<bash-input>",
                "<bash-stdout>",
                "<bash-stderr>",
                "<command-message>",
                "<command-name>",
                "Unknown skill:",
                "Unknown command:",
            )
        )
    )


def _extract_title_from_message(entry: dict) -> str | None:
    """Extract a clean title from a message, handling XML tags and special formats."""
    text = _get_message_text(entry)
    if not text:
        return None

    # Try to extract command name from <command-name>/foo</command-name>
    cmd_match = re.search(r"<command-name>(/[^<]+)</command-name>", text)
    if cmd_match:
        return cmd_match.group(1)

    # Skip messages that are just XML infrastructure
    if text.startswith("<") and ">" in text:
        # Check if there's useful content after the XML tags
        # Remove all XML tags and see what's left
        clean = re.sub(r"<[^>]+>", "", text).strip()
        if clean and len(clean) > 10:
            text = clean
        else:
            return None

    # Clean up markdown and get first meaningful line
    lines = text.strip().split("\n")
    for line in lines:
        # Strip markdown headers and whitespace
        clean_line = re.sub(r"^#+\s*", "", line).strip()
        # Skip empty lines and short fragments
        if clean_line and len(clean_line) > 5:
            return clean_line[:100]

    return text[:100].strip() if text.strip() else None


def _extract_conversation_metadata(entries: list[dict], jsonl_path) -> dict:
    """Extract metadata from JSONL entries."""
    # Title rule: scan all title-emitting rows (custom-title, agent-name,
    # summary), keep the last non-empty value. See `_TITLE_FIELD_BY_TYPE`.
    # Bug-fix (2026-05-12): user-renamed sessions (`/rename`) write
    # `type:"custom-title"` rows; the old code only looked at `summary`
    # and silently fell back to first-user-message truncation, hiding
    # the friendly title shown in CC's own UI.
    name: str | None = None
    for entry in entries:
        candidate = _title_from_entry(entry)
        if candidate:
            name = candidate

    # Get user and assistant messages
    user_entries = [e for e in entries if e.get("type") == "user"]
    assistant_entries = [e for e in entries if e.get("type") == "assistant"]

    # Timestamps from all entries.
    #
    # Hunt #7 (Fragile datetime parsing): use ``_parse_iso_opt`` (the
    # ``None``-on-failure primitive from ``backend.parsing``) and
    # filter ``None``, rather than ``_parse_datetime`` which would
    # substitute ``now(utc)`` on bad rows. The latter would inflate
    # ``max()`` and bounce a conversation with a single corrupt
    # timestamp to the top of the sidebar's recent list.
    all_timestamps = [
        parsed
        for e in entries
        if (parsed := _parse_iso_opt(e.get("timestamp"))) is not None
    ]

    created_at = min(all_timestamps) if all_timestamps else datetime.now(UTC)
    updated_at = max(all_timestamps) if all_timestamps else datetime.now(UTC)

    # Fallback name from first real (non-system) user message
    if not name and user_entries:
        for entry in user_entries:
            if not _is_system_message(entry):
                name = _extract_title_from_message(entry)
                if name:
                    break

    if not name:
        name = jsonl_path.stem

    # Get metadata from first user entry (has cwd, version, etc.)
    first_user_entry = user_entries[0] if user_entries else {}
    session_id = cc_session_uuid(jsonl_path)
    cwd = first_user_entry.get("cwd", "")
    git_branch = first_user_entry.get("gitBranch", "")
    version = first_user_entry.get("version", "")

    # Get model from first assistant message
    model = ""
    if assistant_entries:
        msg = assistant_entries[0].get("message", {})
        model = msg.get("model", "")

    return {
        "uuid": session_id,
        "name": name,
        "summary": "",
        "model": model,
        "created_at": created_at,
        "updated_at": updated_at,
        "cwd": cwd,
        "git_branch": git_branch,
        "version": version,
    }
