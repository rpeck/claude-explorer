"""Reader for Claude Desktop "Cowork" local-agent-mode sessions.

Cowork is the Desktop app's Claude Code-style local-agent harness for
non-coding workflows. Sessions live entirely on disk under

    <claude_desktop_app_dir>/local-agent-mode-sessions/
        <deployment_uuid>/<org_uuid>/
            local_<session_uuid>/audit.jsonl       (append-only messages)
            local_<session_uuid>.json              (sidecar: title, model, ...)

The ``audit.jsonl`` shape is byte-similar to Claude Code's
``projects/*.jsonl``: one JSON object per line, ``{type, message, ...}``,
``message.content`` is a string OR list of content blocks. Differences:

* Cowork uses snake_case ``session_id`` instead of camelCase ``sessionId``.
* Cowork has no ``parentUuid`` — the only parent link is
  ``parent_tool_use_id`` (tool-result -> tool-use), which we ignore.
  Cowork is a chronological append-only log; the store's
  ``is_chronological`` branch handles rendering without parent links.
* Cowork stamps ``_audit_timestamp`` instead of ``timestamp``.
* Cowork stamps ``_audit_hmac`` (a per-line integrity hash). We do NOT
  verify it (D4) and strip it before any downstream code sees it.
* Cowork lines include extra types we silently filter at grouping:
  ``system/init``, ``system/status``, ``rate_limit_event``, plus any
  future ``type`` we don't yet model.

This module is intentionally thinner than ``claude_code_reader``: there
are NO slash-command triplets, no canned-response fold, no prelude
flagging — those are CC-specific boilerplate that Cowork's UI doesn't
generate. We import the format-agnostic message merge from
``agent_session_io`` and stop.

See ``PLANS/2026.05.24-SUPPORT-COWORK-SESSIONS.md`` Phase 1 for the
behavioral contract pinned by ``backend/tests/test_cowork_reader.py``.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from fetcher.paths import REMOTE_COWORK_DIRNAME, REMOTE_SESSION_PREFIX

from .agent_session_io import (
    _get_message_key,
    _merge_entries_to_message,
    normalize_session_fields,
    parse_jsonl_file,
)
from .compact_prefixes import is_compaction_prefix_text

log = logging.getLogger(__name__)


# D11: max bytes per single content payload before we truncate with a
# ``[truncated; N bytes]`` marker. 1 MB is well above any normal
# Cowork user prompt and any reasonable tool_result, but bounds the
# pathological case (a multi-MB pasted log) so the SSE stream + the
# frontend renderer don't OOM. Hard-coded for V1; could be made
# configurable later if a user hits a legitimate larger payload.
_CONTENT_BYTE_LIMIT = 1 * 1024 * 1024


def read_cowork_conversation(session_dir: Path) -> dict | None:
    """Read a single Cowork session into the canonical conversation dict.

    ``session_dir`` is the directory containing ``audit.jsonl`` (e.g.
    ``.../local_<uuid>/``). The sidecar JSON lives at
    ``<session_dir>.json`` (a SIBLING file, not inside the dir).

    Returns ``None`` for:
      * an empty / missing audit.jsonl;
      * a session with no user record (D6 — there's nothing to render).
    """
    audit_path = session_dir / "audit.jsonl"
    sidecar_path = session_dir.with_suffix(".json")

    entries = parse_jsonl_file(audit_path)
    if not entries:
        return None

    # Step 1: normalize each line to the CC-canonical field shape so the
    # shared transforms (_get_message_key, _merge_entries_to_message)
    # work unchanged.
    normalized = [normalize_session_fields(e, fmt="cowork") for e in entries]

    # Step 2: D11 — truncate any single content payload over the byte
    # limit BEFORE the merge so the truncated text is what every
    # downstream caller sees.
    normalized = [_truncate_oversized_content(e) for e in normalized]

    # Step 3: group consecutive entries that belong to the same logical
    # message (same _get_message_key) and merge each group into one
    # message dict. _get_message_key returns None for system/init,
    # rate_limit_event, and any unknown future type — those drop here
    # without crashing.
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for entry in normalized:
        key = _get_message_key(entry)
        if key:
            groups.setdefault(key, []).append(entry)

    messages: list[dict] = []
    for group in groups.values():
        msg = _merge_entries_to_message(group)
        if msg:
            messages.append(msg)

    # D6: a session with no user turn doesn't render usefully — drop it
    # from the sidebar instead of showing a phantom row.
    if not any(m.get("sender") == "human" for m in messages):
        return None

    sidecar = _read_sidecar(sidecar_path)

    # Title from sidecar; fall back to "Untitled" when sidecar missing /
    # title key missing / title empty.
    title = sidecar.get("title") or "Untitled"

    # Cowork uuid derivation (autonomous decision #1 per the
    # smooth-Meandering-cat agent plan): the sidecar's ``sessionId`` is
    # ``local_<uuid>`` — strip the prefix so it matches the directory
    # stem (``local_<uuid>/``) AND the user-visible URL slug, and so
    # cross-source dedup at the store layer can compare uuid strings
    # without prefix bookkeeping.
    #
    # Remote sessions (``remote_<id>/``) store the bare API id (``cse_...``)
    # as ``sessionId``; the prefix strip covers a missing sidecar.
    raw_session_id = sidecar.get("sessionId") or session_dir.name
    uuid = raw_session_id.removeprefix(LOCAL_SESSION_PREFIX).removeprefix(
        REMOTE_SESSION_PREFIX
    )

    first_msg_ts = messages[0]["created_at"]
    last_msg_ts = messages[-1]["updated_at"]
    created_at = _iso_from_epoch_ms(sidecar.get("createdAt")) or first_msg_ts
    updated_at = _iso_from_epoch_ms(sidecar.get("lastActivityAt")) or last_msg_ts

    # D10: ``cwd`` is the sandbox path (e.g. ``/sessions/<vm>``). Carry
    # it through as both ``project_path`` (for compat with the existing
    # detail-view "Project path" field) AND ``sandbox_path`` (so the
    # detail view can label it "Sandbox path" when the source is
    # CLAUDE_COWORK). For some sessions ``cwd`` is the host path
    # instead — render either as plain text, no filesystem linking.
    cwd = sidecar.get("cwd", "")

    return {
        "uuid": uuid,
        "name": title,
        "summary": "",
        "model": sidecar.get("model", ""),
        "created_at": created_at,
        "updated_at": updated_at,
        "is_starred": False,
        "project_path": cwd,
        "git_branch": "",
        "source": "CLAUDE_COWORK",
        "chat_messages": messages,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        # Cowork compaction markers (2026-05-26 fix). Cowork's
        # audit.jsonl does NOT have CC's ``isCompactSummary: true``
        # field, so we detect by text-prefix matching on the canonical
        # Claude compaction prompt (the runtime itself injects this
        # exact string after a context-overflow). Mirrors the FTS
        # ``is_compaction_summary`` tagging path through
        # ``upsert_conversation``.
        "compact_markers": _extract_cowork_compact_markers(messages),
        "prelude_hidden_count": 0,
        # D8: archived flag drives the "Show archived" toggle.
        "is_archived": bool(sidecar.get("isArchived", False)),
        # D9: sidecar.error is a plain string when the Cowork harness
        # logged a session-ending fault; None otherwise. Detail view
        # renders a banner when non-None.
        "error": sidecar.get("error") or None,
        # D10: aliased for the detail view's "Sandbox path" label.
        "sandbox_path": cwd,
        # D2: only a remote task has a workspace. See
        # ``list_cowork_conversations``.
        "organization_id": _remote_organization_id(session_dir, sidecar),
        # 2026-10-23: the list groups by name; without it a remote task
        # showed as "Workspace (<uuid prefix>)".
        "organization_name": _remote_organization_name(session_dir, sidecar),
    }


def _remote_organization_name(session_dir: Path, sidecar: dict) -> str | None:
    """The workspace name of a remote task (sidecar ``organizationName``).

    ``None`` for a local session, and for a task fetched before the name
    was recorded; the next fetch adds it.
    """
    if not session_dir.name.startswith(REMOTE_SESSION_PREFIX):
        return None
    name = sidecar.get("organizationName")
    return name if isinstance(name, str) and name else None


def _remote_organization_id(session_dir: Path, sidecar: dict) -> str | None:
    """The workspace of a remote task, or ``None`` for a local session.

    ``fetcher.remote_sessions`` writes each task under
    ``<remote_root>/<org_uuid>/`` and records the same uuid as the sidecar
    ``organizationId``. The directory name is the fallback when the sidecar
    is missing or has no value.
    """
    if not session_dir.name.startswith(REMOTE_SESSION_PREFIX):
        return None
    org = sidecar.get("organizationId")
    if isinstance(org, str) and org:
        return org
    return session_dir.parent.name or None


# Canonical Claude compaction-prompt prefix the runtime injects after a
# context-overflow lives in ``backend.compact_prefixes`` (leaf module,
# zero internal imports) so the same constant is shared with the search-
# index title-leak gate (2026-05-26 bug fix). Cowork's audit.jsonl does
# NOT carry CC's ``isCompactSummary: true`` field, so text-prefix
# matching is the only reliable detector. The gate is anchored
# (``.lstrip().startswith``) so a regular user message that quotes the
# prefix mid-text does not false-positive.


def _extract_cowork_compact_markers(messages: list[dict]) -> list[dict]:
    """Scan post-merge Cowork messages for compaction-summary turns.

    Returns marker dicts mirroring CC's ``extract_compact_markers``
    shape so downstream consumers (``search_index.upsert_conversation``,
    the viewer's ``hasCompactMarkers`` gate, the export render path)
    work unchanged. ``kind`` is always ``"auto"`` for Cowork — the
    runtime injects these after context-overflow with no equivalent of
    CC's manual ``/compact`` command replay.
    """
    markers: list[dict] = []
    for msg in messages:
        if msg.get("sender") != "human":
            continue
        text = msg.get("text") or ""
        # The shared helper handles the non-string defensive case AND the
        # ``.lstrip()`` whitespace tolerance the original code had.
        if not is_compaction_prefix_text(text):
            continue
        markers.append({
            "message_uuid": msg.get("uuid", ""),
            "summary_text": text,
            "timestamp": msg.get("created_at", ""),
            "kind": "auto",
            "user_prompt": None,
        })
    return markers


LOCAL_SESSION_PREFIX = "local_"


def remote_cowork_root(data_dir: Path) -> Path:
    """Where ``fetcher.remote_sessions`` archives remote Cowork sessions."""
    return data_dir / REMOTE_COWORK_DIRNAME


def iter_cowork_session_dirs(
    cowork_root: Path | None, remote_root: Path | None = None
) -> list[Path]:
    """Every Cowork session directory, local and remote.

    Local sessions (written by Claude Desktop):
        ``<cowork_root>/<deployment>/<org>/local_<uuid>/``
    Remote sessions (cloud tasks, written by ``fetcher.remote_sessions``):
        ``<remote_root>/<org>/remote_<session_id>/``

    Missing or unreadable roots contribute nothing; this never raises.
    """
    out: list[Path] = []
    for org_dir in _subdirs_at_depth(cowork_root, 2):
        out.extend(_session_dirs_in(org_dir, LOCAL_SESSION_PREFIX))
    for org_dir in _subdirs_at_depth(remote_root, 1):
        out.extend(_session_dirs_in(org_dir, REMOTE_SESSION_PREFIX))
    return out


def find_cowork_session_dirs(
    cowork_root: Path | None, remote_root: Path | None, uuid: str
) -> list[Path]:
    """Every session directory for conversation ``uuid``, in list order.

    Usually there is one. The same session directory can exist under two
    deployment or org directories, and one copy can be empty or have no
    user turn. The list builds its row from a readable copy, so a caller
    that opens the conversation must try each directory, not only the
    first.

    Checks one candidate per org directory, so the cost is O(orgs), not
    O(sessions).
    """
    out: list[Path] = []
    for org_dir in _subdirs_at_depth(cowork_root, 2):
        candidate = org_dir / f"{LOCAL_SESSION_PREFIX}{uuid}"
        if candidate.is_dir():
            out.append(candidate)
    for org_dir in _subdirs_at_depth(remote_root, 1):
        candidate = org_dir / f"{REMOTE_SESSION_PREFIX}{uuid}"
        if candidate.is_dir():
            out.append(candidate)
    return out


def _subdirs_at_depth(root: Path | None, depth: int) -> list[Path]:
    level = [root] if root is not None and root.is_dir() else []
    for _ in range(depth):
        nxt: list[Path] = []
        for d in level:
            try:
                nxt.extend(c for c in d.iterdir() if c.is_dir())
            except OSError:
                continue
        level = nxt
    return level


def _session_dirs_in(org_dir: Path, prefix: str) -> list[Path]:
    try:
        return [e for e in org_dir.iterdir() if e.is_dir() and e.name.startswith(prefix)]
    except OSError:
        return []


def list_cowork_conversations(
    cowork_root: Path, remote_root: Path | None = None
) -> list[dict]:
    """Read every local and remote Cowork session.

    D2: the deployment + org directory layers are flattened in the
    output — callers receive a single list of conversation dicts with
    no deployment/org metadata leaking through. Local Cowork's ``<org>``
    directories reuse Claude Desktop's internal org UUIDs but don't
    map to user-facing workspaces, so we deliberately leave
    ``organization_id`` unset on local sessions.

    Remote tasks are different: the fetcher downloads each task from one
    workspace (the ``x-organization-uuid`` of the sessions API call) and
    records that workspace uuid as the sidecar ``organizationId``. That is
    the same uuid that the sidebar workspace filter uses for Desktop
    conversations, so a remote task carries it as ``organization_id``.
    Without it, a workspace filter hides every remote task.

    Returns ``[]`` (not raises) when neither root exists — a user
    without Cowork data installed must not see a crash.
    """
    session_dirs = iter_cowork_session_dirs(cowork_root, remote_root)

    out: list[dict] = []
    for sd in session_dirs:
        try:
            conv = read_cowork_conversation(sd)
        except Exception:  # pragma: no cover  defensive: one bad session
            # can't kill the whole sidebar
            log.exception("cowork: failed to read session %s", sd)
            continue
        if conv:
            out.append(conv)
    return newest_per_uuid(out)


_OLDEST = datetime.min.replace(tzinfo=UTC)


def updated_at_key(conv: dict) -> datetime:
    """``conv["updated_at"]`` as an aware datetime, for "newest" comparisons.

    A missing or unparseable value sorts as the oldest.
    """
    value = conv.get("updated_at")
    if not isinstance(value, str) or not value:
        return _OLDEST
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return _OLDEST
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def newest_per_uuid(convs: list[dict]) -> list[dict]:
    """One conversation per uuid: the copy with the newest ``updated_at``.

    The same session can exist in two org or deployment directories (one
    remote task written under two orgs, or one local session under two
    deployments). Without this, the sidebar shows two rows with one uuid.
    On a tie the first copy wins. The output keeps the order in which each
    uuid first appears.
    """
    best: dict[str, dict] = {}
    for conv in convs:
        uuid = conv.get("uuid") or ""
        kept = best.get(uuid)
        if kept is None or updated_at_key(conv) > updated_at_key(kept):
            best[uuid] = conv
    return list(best.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_sidecar(path: Path) -> dict:
    """Tolerate a missing / corrupt sidecar — return ``{}`` so the
    caller falls back to defaults (title="Untitled", model="", etc.).

    A user mid-migration or mid-write may have a session dir without
    its sidecar (or with a half-written one). We log the parse failure
    at WARNING so it's visible in supervised-job logs, but never raise.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        log.warning("cowork: sidecar %s unreadable", path, exc_info=True)
        return {}


def _truncate_oversized_content(entry: dict) -> dict:
    """D11: truncate any single content payload over the byte limit.

    Operates on the raw entry (pre-merge) so the truncated bytes are
    what gets baked into both the merged ``text`` AND the per-block
    ``content`` array — no double-truncation, no off-by-one between
    the two views.
    """
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return entry
    content = msg.get("content")

    if isinstance(content, str):
        encoded = content.encode("utf-8")
        if len(encoded) <= _CONTENT_BYTE_LIMIT:
            return entry
        # Decode the truncated prefix back with errors="ignore" so a
        # multi-byte character split at the boundary doesn't raise.
        truncated = encoded[:_CONTENT_BYTE_LIMIT].decode("utf-8", errors="ignore")
        truncated += f"\n\n[truncated; {len(encoded)} bytes]"
        out = dict(entry)
        out_msg = dict(msg)
        out_msg["content"] = truncated
        out["message"] = out_msg
        return out

    if isinstance(content, list):
        mutated = False
        new_blocks: list[dict] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = block["text"]
                encoded = text.encode("utf-8")
                if len(encoded) > _CONTENT_BYTE_LIMIT:
                    truncated = encoded[:_CONTENT_BYTE_LIMIT].decode(
                        "utf-8", errors="ignore"
                    )
                    truncated += f"\n\n[truncated; {len(encoded)} bytes]"
                    new_block = dict(block)
                    new_block["text"] = truncated
                    new_blocks.append(new_block)
                    mutated = True
                    continue
            new_blocks.append(block)
        if mutated:
            out = dict(entry)
            out_msg = dict(msg)
            out_msg["content"] = new_blocks
            out["message"] = out_msg
            return out

    return entry


def _iso_from_epoch_ms(value) -> str | None:
    """Convert a sidecar epoch-ms timestamp to ISO-8601 UTC.

    Returns ``None`` for missing / non-numeric / zero values so the
    caller can fall back to the message-derived timestamp.
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None
