"""Project Codex rollout records into Explorer's conversation dict.

Plan §4.4 and §4.5. Codex keeps one of two history modes:

* **paginated:** ``event_msg`` / ``item_completed`` records carry the
  visible items. A later snapshot of an item replaces the earlier one.
  ``response_item`` records are the model context, so they are skipped,
  except a tool call whose ``call_id`` is the id of no item (a failed
  freeform ``apply_patch``, the outer code-mode ``exec``, an unknown tool):
  it shows with its output, as in legacy history.
* **legacy:** ``response_item`` records are the history. A tool call
  matches its output by ``call_id``.

Slice 2 (N10) rebuilds the records that change the history: a compaction
becomes a compact marker, a rollback hides its turns, and a
``history_base`` prefix comes from ``history.resolve_lineage``. The rules
follow ``ThreadHistoryBuilder`` in
``codex-rs/app-server-protocol/src/protocol/thread_history.rs`` at
``f2d3086``.
"""

from __future__ import annotations

import hashlib
import re
import uuid as uuid_mod
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote

import orjson

from ..code_run_flow import add_data_flow
from .history import LoadBase, parent_uuid, resolve_lineage
from .rollout import RolloutRecord

ENCRYPTED_REASONING = "Encrypted reasoning (not readable)"

# Fixed, so a message id never changes between processes or versions.
_NAMESPACE = uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, "https://github.com/rpeck/claude-explorer/codex")

_RECORD_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

# The RolloutItem kinds (codex-rs/history/src/rollout_payload.rs at f2d3086).
_KNOWN_RECORD_TYPES = {
    "session_meta", "dynamic_tools_updated", "turn_context", "response_item", "event_msg",
    "inter_agent_communication", "inter_agent_communication_metadata", "world_state",
    "token_usage_record", "security_risk_score", "retained_context", "realtime_item", "compacted",
}

COMPACTED_LABEL = "Context compacted"

# Text that Codex injects into a user message as context (legacy history).
_INJECTED_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<INSTRUCTIONS>",
    "# AGENTS.md instructions",
)

_LEGACY_CALLS = {"function_call", "custom_tool_call", "local_shell_call"}
_LEGACY_OUTPUTS = {"function_call_output", "custom_tool_call_output"}
# Tool declarations and other model context: nothing to show.
_LEGACY_SKIPPED = {"additional_tools"}


def message_uuid(
    thread_id: str,
    *,
    turn_id: str | None = None,
    item_id: str | None = None,
    line_no: int | None = None,
) -> str:
    """UUID5 of (thread, turn, item) for paginated items, (thread, line) for legacy lines.

    The thread id is in the key, so a fork that reuses item ids never takes
    the rows of another session (the MCP outline cache keys on message ids).
    """
    if item_id is not None:
        name = f"{thread_id}/item/{turn_id or ''}/{item_id}"
    else:
        name = f"{thread_id}/line/{line_no}"
    return str(uuid_mod.uuid5(_NAMESPACE, name))


def project_rollout(
    records: Iterable[RolloutRecord],
    *,
    thread_id: str | None = None,
    history_mode: str | None = None,
    current_ambiguous: bool = False,
    damaged_lines: int = 0,
    load_base: LoadBase | None = None,
) -> dict[str, Any]:
    """Build the conversation dict. ``history_mode`` is the ``state_5`` value, if any.

    ``load_base`` returns the records of a ``history_base`` rollout by its
    rollout id. Without it, a session with a prefix is incomplete.
    """
    own = list(records)
    meta = next((r.payload for r in own if r.type == "session_meta"), {})
    thread_id = thread_id or meta.get("id") or ""
    lineage = resolve_lineage(meta, own, load_base)
    records = lineage.records

    paginated = history_mode == "paginated" or any(_is_item_completed(r) for r in records)
    warnings = ["damaged line skipped"] * damaged_lines
    reasons = _incomplete_reasons(records, current_ambiguous) + lineage.reasons

    # turn id -> model, from turn_context; the session model is the last turn's.
    turn_models: dict[str, str] = {}
    session_model = ""
    for r in records:
        if r.type == "turn_context" and r.payload.get("model"):
            session_model = r.payload["model"]
            if r.payload.get("turn_id"):
                turn_models[r.payload["turn_id"]] = session_model

    if paginated:
        messages = _paginated_messages(records, thread_id, turn_models, warnings)
    else:
        messages = _legacy_messages(records, thread_id, warnings)

    for prev, msg in zip([None, *messages], messages, strict=False):
        msg["parent_message_uuid"] = prev["uuid"] if prev else None
    agent_path = _link_agent_messages(messages, records, meta, thread_id)

    stamps = [r.timestamp for r in records if r.timestamp]
    created_at = meta.get("timestamp") or (stamps[0] if stamps else "")
    return {
        "uuid": thread_id,
        "chat_messages": messages,
        "compact_markers": _compact_markers(messages),
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "model": session_model,
        "project_path": meta.get("cwd") or "",
        "created_at": created_at,
        "updated_at": stamps[-1] if stamps else created_at,
        "history_mode": "paginated" if paginated else "legacy",
        "incomplete": bool(reasons),
        "incomplete_reasons": reasons,
        "warnings": warnings,
        "parent_uuid": parent_uuid(meta, thread_id),
        # Issue #91: set only on a thread with agent messages; the index keys its links.
        "agent_path": agent_path,
    }


# ---------------------------------------------------------------------------
# The incomplete classifier
# ---------------------------------------------------------------------------


def _incomplete_reasons(records: list[RolloutRecord], current_ambiguous: bool) -> list[str]:
    """The reasons why this history cannot be rebuilt, in first-seen order.

    Compaction, rollback, and ``history_base`` are rebuilt (N10). The
    lineage adds its own reasons when a prefix is missing.
    """
    reasons = ["current_ambiguous"] if current_ambiguous else []
    for r in records:
        if r.type in _KNOWN_RECORD_TYPES:
            continue
        # A raw type can hold anything; only a plain name reaches the notice.
        reason = f"unknown record type: {r.type}" if _RECORD_TYPE_RE.fullmatch(r.type) else "unknown record type"
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _rollback_turns(r: RolloutRecord) -> int | None:
    """``num_turns`` of an ``EventMsg::ThreadRolledBack`` record, else None."""
    if r.type != "event_msg" or r.payload.get("type") != "thread_rolled_back":
        return None
    n = r.payload.get("num_turns")
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _drop_last_turns(turns: list[Any], n: int) -> set[Any]:
    """The last ``n`` turns; more than the count is all of them."""
    return set(turns[-n:]) if n > 0 else set()


class _Compactions:
    """Pairs the records that Codex writes for one compaction.

    A ``compacted`` record (it holds the summary) and a ``ContextCompaction``
    item (paginated) or ``context_compacted`` event (legacy) in the same
    turn are one compaction, so they give one marker row.
    """

    def __init__(self) -> None:
        self._rows: dict[Any, list[dict[str, Any]]] = {}  # turn -> rows, in order

    def add(self, turn: Any, half: str, summary: str | None, r: RolloutRecord) -> dict[str, Any] | None:
        """The new row, or None when ``half`` completes an earlier row of the turn."""
        for row in self._rows.get(turn, []):
            if half not in row["halves"]:
                row["halves"].add(half)
                if half == "record":  # the compacted record holds the summary and the time
                    row["summary"], row["record"] = summary or "", r
                return None
        row = {"halves": {half}, "summary": summary or "", "record": r}
        self._rows.setdefault(turn, []).append(row)
        return row

    def drop(self, turns: set[Any]) -> None:
        for turn in turns:
            self._rows.pop(turn, None)


def _compaction_summary(r: RolloutRecord) -> str:
    message = r.payload.get("message")
    return message if isinstance(message, str) else ""


def _compaction_message(msg_uuid: str, row: dict[str, Any], model: str) -> dict[str, Any]:
    # The replacement history is model context, never a turn: only the summary shows.
    r = row["record"]
    msg = _message(msg_uuid, "assistant", [_text(row["summary"] or COMPACTED_LABEL)], r.timestamp, r.timestamp, model)
    msg["_compaction"] = row
    return msg


def _compact_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One marker per compaction row; the viewer swaps the row for the marker."""
    markers = []
    for msg in messages:
        row = msg.pop("_compaction", None)
        if row is None:
            continue
        # A later half of the compaction can bring the summary and the time.
        msg["content"] = [_text(row["summary"] or COMPACTED_LABEL)]
        msg["text"] = msg["content"][0]["text"]
        msg["created_at"] = msg["updated_at"] = row["record"].timestamp or ""
        markers.append({
            "message_uuid": msg["uuid"],
            "summary_text": row["summary"],
            "timestamp": msg["created_at"],
            # shortcut: no Codex record says who started a compaction; "auto" until one does.
            "kind": "auto",
            "user_prompt": None,
        })
    return markers


# ---------------------------------------------------------------------------
# Code mode (issue #90)
# ---------------------------------------------------------------------------

_EXEC_TOOL, _WAIT_TOOL, _NESTED_PREFIX = "exec", "wait", "exec-"
# core/src/tools/code_mode/ at f2d3086: the first line of each exec or wait output.
_EXEC_HEADER_RE = re.compile(r"Script (completed|failed|terminated|running with cell ID (\S+))")
_DATA_URL_RE = re.compile(r"data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=]+)")


def _exec_output(text: str) -> tuple[str | None, str | None, str]:
    """(final status or None, the cell id of a yielded cell or None, the output body)."""
    first, _, rest = text.partition("\n")
    m = _EXEC_HEADER_RE.fullmatch(first.strip())
    if m is None:
        return None, None, text.strip("\n")
    _, sep, body = rest.partition("Output:\n")
    body = body if sep else rest
    if m.group(2):
        return None, m.group(2), body.strip("\n")
    return m.group(1), None, body.strip("\n")


def _exec_media(output: Any) -> tuple[Any, list[dict[str, Any]]]:
    """(the output without its inline images, those images as media blocks) (issue #103).

    A script that returns an image (for example from ``view_image``) puts it in
    its output as a data URL. Any other image stays a label: never fetch a URL.
    """
    if not isinstance(output, list):
        return output, []
    rest, media = [], []
    for c in output:
        url = c.get("image_url") if isinstance(c, dict) else None
        m = _DATA_URL_RE.fullmatch(url) if isinstance(url, str) else None
        if m:
            media.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
        else:
            rest.append(c)
    return rest, media


class _CodeMode:
    """The exec cells of a rollout, in record order.

    A nested tool item (``exec-<uuid>``) has no parent field. It links to the
    one cell that is alive when it first shows (``exclusive_window``); with
    two or more alive it links to none of them.
    """

    def __init__(self, records: list[RolloutRecord]) -> None:
        self.runs: dict[str, dict[str, Any]] = {}  # exec call id -> code_run
        self.waits: set[str] = set()  # wait call ids that continue a known cell
        self.link: dict[str, str] = {}  # nested item id -> run id
        self.ambiguous: dict[str, list[str]] = {}  # nested item id -> the runs alive
        cells: dict[str, str] = {}  # cell id -> run id
        wait_run: dict[str, str] = {}
        alive: list[str] = []
        seen: set[str] = set()
        for r in records:
            p = r.payload
            call_id = p.get("call_id") if isinstance(p.get("call_id"), str) else None
            if r.type == "response_item" and p.get("type") == "custom_tool_call" and p.get("name") == _EXEC_TOOL and call_id:
                code = p.get("input") if isinstance(p.get("input"), str) else ""
                self.runs[call_id] = {
                    "language": "javascript", "code": code,
                    "line_count": code.count("\n") + 1 if code else 0, "description": None,
                    "status": "unknown", "background": False, "result": None, "stdout": None,
                    "stderr": None, "error": None, "refused": None, "inner_calls": [], "media": [],
                }
                alive.append(call_id)
            elif r.type == "response_item" and p.get("type") == "function_call" and p.get("name") == _WAIT_TOOL and call_id:
                run_id = cells.get(str(_decode_json_text(p.get("arguments")).get("cell_id")))
                if run_id:
                    wait_run[call_id] = run_id
                    self.waits.add(call_id)
            elif r.type == "response_item" and p.get("type") in _LEGACY_OUTPUTS and call_id:
                run_id = call_id if call_id in self.runs else wait_run.get(call_id)
                if run_id is None:
                    continue
                run = self.runs[run_id]
                output, media = _exec_media(p.get("output"))
                run["media"].extend(media)
                status, cell, body = _exec_output(_output_text(output))
                if status == "failed" and body.startswith("Script error:"):
                    # The error of a failed script is not a value it returned.
                    run["error"] = body.removeprefix("Script error:").strip("\n")
                    body = ""
                if body:
                    run["result"] = body if run["result"] is None else f"{run['result']}\n{body}"
                if cell:
                    cells[cell] = run_id
                    run["status"] = "running"
                elif status:
                    run["status"] = status
                    if run_id in alive:
                        alive.remove(run_id)
            elif _is_item_completed(r) and isinstance(p.get("item"), dict):
                item_id = str(p["item"].get("id") or "")
                if item_id.startswith(_NESTED_PREFIX) and item_id not in seen:
                    seen.add(item_id)
                    if len(alive) == 1:
                        self.link[item_id] = alive[0]
                    elif alive:
                        self.ambiguous[item_id] = list(alive)

    def finish(self) -> None:
        """Call once every nested call is in its run."""
        for run in self.runs.values():
            add_data_flow(run)

    def block(self, call_id: str) -> dict[str, Any]:
        return {"type": "code_run", "id": call_id, "name": _EXEC_TOOL, "code_run": self.runs[call_id]}

    def add_call(self, item: dict[str, Any], name: str, tool_input: dict[str, Any], output: str) -> None:
        item_id = str(item.get("id") or "")
        run_id = self.link[item_id]
        self.runs[run_id]["inner_calls"].append({
            "id": item_id, "name": name, "input": tool_input, "result": output,
            "is_error": item.get("status") == "failed", "refused": item.get("status") == "declined",
            "code_run_parent": run_id, "link_source": "exclusive_window",
        })

    def mark(self, item: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tool blocks of a nested item that links to no run."""
        item_id = str(item.get("id") or "")
        if not item_id.startswith(_NESTED_PREFIX):
            return blocks
        use = {**blocks[0], "from_code_mode": True}
        if item_id in self.ambiguous:
            use["ambiguous_with"] = self.ambiguous[item_id]
        return [use, *blocks[1:]]


# ---------------------------------------------------------------------------
# Paginated history
# ---------------------------------------------------------------------------


def _is_item_completed(r: RolloutRecord) -> bool:
    return r.type == "event_msg" and r.payload.get("type") == "item_completed"


class _ResponseCall:
    """A paginated ``response_item`` tool call that no item shows, and its output."""

    def __init__(self) -> None:
        self.call: RolloutRecord | None = None
        self.output: RolloutRecord | None = None


def _paginated_messages(
    records: list[RolloutRecord],
    thread_id: str,
    turn_models: dict[str, str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    # (turn id, item id) -> [first record, latest record], or a compaction row,
    # or a _ResponseCall (keyed by its call id);
    # (turn id, line number) -> an inter-agent message, which has no snapshots.
    # A dict keeps first-seen order.
    items: dict[tuple[str, str | int], Any] = {}
    # When an item exists, its id is the call id (codex-rs core/src/tools/events.rs).
    item_ids = {
        str(r.payload["item"].get("id")) for r in records
        if _is_item_completed(r) and isinstance(r.payload.get("item"), dict)
    }
    response_calls: dict[str, _ResponseCall] = {}  # call id -> its entry
    code_mode = _CodeMode(records)
    compactions = _Compactions()
    agent_ids: set[str] = set()  # agent messages already shown, by id
    compaction_items: set[tuple[str, str]] = set()
    turn = ""  # the turn of the latest record that names one
    for r in records:
        if r.type in ("turn_context", "event_msg") and isinstance(r.payload.get("turn_id"), str):
            turn = r.payload["turn_id"] or turn
        rollback = _rollback_turns(r)
        if rollback is not None:
            dropped = _drop_last_turns(list(dict.fromkeys(key[0] for key in items)), rollback)
            items = {key: v for key, v in items.items() if key[0] not in dropped}
            compactions.drop(dropped)
            continue
        if r.type == "compacted":
            row = compactions.add(turn, "record", _compaction_summary(r), r)
            if row is not None:
                items[(turn, f"compacted-{r.line_no}")] = row
            continue
        if _is_agent_message(r):
            if _seen_agent_message(r, agent_ids):
                continue
            own_turn = r.payload.get("turn_id")
            items[(own_turn if isinstance(own_turn, str) and own_turn else turn, r.line_no)] = r
            continue
        if r.type == "response_item" and r.payload.get("type") in _LEGACY_CALLS | _LEGACY_OUTPUTS:
            call_id = r.payload.get("call_id")
            if isinstance(call_id, str) and call_id in item_ids:
                continue  # the item shows this call
            key = call_id if isinstance(call_id, str) and call_id else f"line-{r.line_no}"
            entry = response_calls.get(key)
            if entry is None:
                entry = response_calls[key] = items[(turn, key)] = _ResponseCall()
            if r.payload["type"] in _LEGACY_CALLS:
                entry.call = entry.call or r
            else:
                entry.output = entry.output or r
            continue
        if not _is_item_completed(r) or not isinstance(r.payload.get("item"), dict):
            continue
        turn_id = str(r.payload.get("turn_id") or "")
        item_id = str(r.payload["item"].get("id") or f"line-{r.line_no}")
        if r.payload["item"].get("type") == "ContextCompaction":
            if (turn_id, item_id) not in compaction_items:  # a later snapshot changes nothing
                compaction_items.add((turn_id, item_id))
                row = compactions.add(turn_id, "item", None, r)
                if row is not None:
                    items[(turn_id, item_id)] = row
            continue
        slot = items.setdefault((turn_id, item_id), [r, r])
        slot[1] = r

    messages = []
    for (turn_id, item_id), entry in items.items():
        if isinstance(entry, RolloutRecord):
            messages.append(_agent_message(entry, thread_id, turn_id, turn_models.get(turn_id, "")))
            continue
        msg_uuid = message_uuid(thread_id, turn_id=turn_id, item_id=str(item_id))
        if isinstance(entry, dict):
            messages.append(_compaction_message(msg_uuid, entry, turn_models.get(turn_id, "")))
            continue
        if isinstance(entry, _ResponseCall):
            call_id = str(item_id)
            if call_id in code_mode.waits:
                continue  # its output is part of the run
            if call_id in code_mode.runs:
                first = entry.call or entry.output
                latest = entry.output or entry.call
                assert first is not None and latest is not None
                messages.append(_message(msg_uuid, "assistant", [code_mode.block(call_id)],
                                         first.timestamp, latest.timestamp, turn_models.get(turn_id, "")))
                continue
            messages.append(_response_call_message(msg_uuid, entry, turn_models.get(turn_id, ""), warnings))
            continue
        first, latest = entry
        item = latest.payload["item"]
        item_type = item.get("type")
        sender = "human" if item_type == "UserMessage" else "assistant"
        if item_type == "UserMessage":
            blocks = _user_content(item.get("content"))
        elif item_type == "AgentMessage":
            blocks = [_text(c.get("text", "")) for c in item.get("content") or [] if isinstance(c, dict) and c.get("text")]
        elif item_type == "Reasoning":
            blocks = [_reasoning(item.get("summary_text") or [])]
        elif item_type == "Extension":
            blocks = _tool(item, *_extension(item, warnings))
        elif item_type in _TOOL_ITEMS:
            if str(item.get("id")) in code_mode.link:
                code_mode.add_call(item, *_TOOL_ITEMS[item_type](item))
                continue
            blocks = code_mode.mark(item, _tool(item, *_TOOL_ITEMS[item_type](item)))
        else:
            blocks = [_unsupported(str(item_type), warnings)]
        if not blocks:
            continue
        messages.append(
            _message(
                msg_uuid, sender, blocks, first.timestamp, latest.timestamp,
                turn_models.get(turn_id, ""),
            )
        )
    code_mode.finish()
    return messages


def _response_call_message(
    msg_uuid: str, entry: _ResponseCall, model: str, warnings: list[str]
) -> dict[str, Any]:
    blocks = [_legacy_call(entry.call.payload)] if entry.call else []
    if entry.output is not None:
        call_id = entry.output.payload.get("call_id")
        if entry.call is None:
            warnings.append(f"tool output without a call: {call_id}")
        blocks.append({"type": "tool_result", "tool_use_id": call_id,
                       "content": [_text(_output_text(entry.output.payload.get("output")))]})
    first = entry.call or entry.output
    latest = entry.output or entry.call
    assert first is not None and latest is not None
    return _message(msg_uuid, "assistant", blocks, first.timestamp, latest.timestamp, model)


def _user_content(content: Any) -> list[dict[str, Any]]:
    blocks = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("text", "input_text") and c.get("text"):
            blocks.append(_text(c["text"]))
        elif c.get("type") in ("image", "local_image", "input_image"):
            blocks.append(_image_label(c))
    return blocks


def _tool(item: dict[str, Any], name: str, tool_input: dict[str, Any], output: str) -> list[dict[str, Any]]:
    # Only tool blocks, so the message shows and hides with Show Tools.
    tool_id = str(item.get("id") or "")
    return [
        {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input},
        {"type": "tool_result", "tool_use_id": tool_id, "content": [_text(output)]},
    ]


def _command(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    output = item.get("aggregated_output") or ""
    exit_code = item.get("exit_code")
    if exit_code not in (None, 0):
        output = f"{output}\n[exit code {exit_code}]" if output else f"[exit code {exit_code}]"
    return "shell", {"command": item.get("command"), "cwd": item.get("cwd")}, output


def _mcp_tool_call(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    # McpToolCallItem fields are camelCase.
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    output = _output_text(result.get("content") or [])
    if not output and result.get("structuredContent") is not None:
        output = orjson.dumps(result["structuredContent"]).decode()
    error = item.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    if isinstance(error, str) and error:
        output = _lines(output, error)
    name = ".".join(str(p) for p in (item.get("server"), item.get("tool")) if p) or "mcp"
    return name, _decode_json_text(item.get("arguments", {})), output


def _file_change(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
    output = _lines(*(s for s in (item.get("stdout"), item.get("stderr")) if isinstance(s, str)))
    if item.get("status") in ("failed", "declined"):
        output = _lines(output, f"[status: {item['status']}]")
    return "apply_patch", {"changes": changes}, output


def _collab_agent_tool_call(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    states = item.get("agents_states")
    output = _lines(*(f"{t}: {_agent_status(s)}" for t, s in states.items())) if isinstance(states, dict) else ""
    name = str(item.get("tool") or "collab_agent")
    return name, _present(item, "prompt", "receiver_thread_ids", "model"), output


def _agent_status(status: Any) -> str:
    # AgentStatus: "running", or {"completed": <final message or null>} / {"errored": <message>}.
    if isinstance(status, dict) and status:
        kind, detail = next(iter(status.items()))
        return f"{kind}: {detail}" if detail else str(kind)
    return str(status)


def _subagent_activity(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    output = " ".join(str(item[k]) for k in ("kind", "agent_path") if item.get(k))
    return "subagent", _present(item, "kind", "agent_path", "agent_thread_id", "model"), output


def _image_view(item: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    path = item.get("path")
    # 0.170 writes a file:// URI, older versions a plain path.
    name = unquote(path) if isinstance(path, str) and path.startswith("file:") else path
    return "view_image", {"path": path}, _image_label({"type": "local_image", "path": name})["text"]


_EXTENSION_KIND_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _extension(item: dict[str, Any], warnings: list[str]) -> tuple[str, dict[str, Any], str]:
    """``ExtensionItem``: ``kind`` names its schema, and its fields are camelCase."""
    kind = item.get("kind")
    if kind == "web.search":
        results = item.get("results") if isinstance(item.get("results"), list) else []
        urls = _lines(*(str(r["url"]) for r in results if isinstance(r, dict) and r.get("url")))
        return "web_search", _present(item, "query", "action"), urls
    if kind == "clock.sleep":
        return "sleep", _present(item, "durationMs"), ""
    if kind == "image_gen.generation":
        # Never the inline ``result`` (base64 image data), and never a read of ``savedPath``.
        label = _image_label({"type": "local_image", "path": item.get("savedPath")})["text"]
        return "image_generation", _present(item, "revisedPrompt"), label
    named = isinstance(kind, str) and _EXTENSION_KIND_RE.fullmatch(kind)
    warnings.append(f"unknown Codex extension kind: {kind}" if named else "unknown Codex extension kind")
    name = kind if isinstance(kind, str) and kind else "extension"
    return name, {k: v for k, v in item.items() if k not in ("type", "id", "kind")}, ""


_TOOL_ITEMS = {
    "CommandExecution": _command,
    "McpToolCall": _mcp_tool_call,
    "FileChange": _file_change,
    "CollabAgentToolCall": _collab_agent_tool_call,
    "SubAgentActivity": _subagent_activity,
    "ImageView": _image_view,
}


def _present(item: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: item[k] for k in keys if item.get(k) is not None}


def _lines(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


_ENCRYPTED_AGENT_MESSAGE = "[Encrypted agent message]"
_PAYLOAD_MARK = "Payload:\n"
# The multi-agent tools whose ``message`` argument is an agent message (issue #91).
_SEND_TOOLS = {"spawn_agent": "task_name", "send_message": "target", "followup_task": "target"}


def _is_agent_message(r: RolloutRecord) -> bool:
    """An agent message: the ``inter_agent_communication`` record, or (0.164) a
    ``response_item`` of type ``agent_message``."""
    return r.type == "inter_agent_communication" or (
        r.type == "response_item" and r.payload.get("type") == "agent_message"
    )


def _agent_payload(p: dict[str, Any]) -> tuple[str, str]:
    """(the text to show, the text to compare). The shown text is the part after
    "Payload:"; an encrypted part shows as a marker and compares by its ciphertext."""
    content = p.get("content")
    parts = content if isinstance(content, list) else [{"type": "input_text", "text": content}] if isinstance(content, str) else []
    texts = [str(c.get("text") or "") for c in parts if isinstance(c, dict) and c.get("type") in ("input_text", "text")]
    ciphers = [str(c["encrypted_content"]) for c in parts if isinstance(c, dict) and c.get("encrypted_content")]
    if isinstance(p.get("encrypted_content"), str) and p["encrypted_content"]:
        ciphers.append(p["encrypted_content"])
    text = "".join(texts)
    if _PAYLOAD_MARK in text:
        text = text.split(_PAYLOAD_MARK, 1)[1]
    text = text.strip()
    shown = _lines(text, *(_ENCRYPTED_AGENT_MESSAGE for _ in ciphers[:1]))  # never decode it
    return shown, ciphers[0] if ciphers else text


def _seen_agent_message(r: RolloutRecord, seen: set[str]) -> bool:
    """True for a second record of one agent message (the same id)."""
    comm_id = r.payload.get("id")
    if not isinstance(comm_id, str) or not comm_id:
        return False
    if comm_id in seen:
        return True
    seen.add(comm_id)
    return False


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _agent_message(
    r: RolloutRecord, thread_id: str, turn_id: str | None, model: str
) -> dict[str, Any]:
    """An agent message between the threads of one tree, in both history modes."""
    p = r.payload
    content, compared = _agent_payload(p)
    comm_id = p.get("id") if isinstance(p.get("id"), str) and p.get("id") else None
    msg_uuid = (
        message_uuid(thread_id, turn_id=turn_id, item_id=comm_id)
        if comm_id else message_uuid(thread_id, line_no=r.line_no)
    )
    blocks = _tool({"id": comm_id or f"line-{r.line_no}"}, "agent_message",
                   _present(p, "author", "recipient", "trigger_turn"), content)
    blocks[0]["_peer_digest"] = _digest(compared)  # private: for the links (issue #91)
    return _message(msg_uuid, "assistant", blocks, r.timestamp, r.timestamp, model)


def own_agent_path(meta: dict[str, Any]) -> str | None:
    """The thread's agent path: a spawned agent's ``thread_spawn.agent_path``, else
    "/root". None for another kind of subagent (for example a review)."""
    source = meta.get("source")
    sub = source.get("subagent") if isinstance(source, dict) else None
    if sub is None:
        return "/root"
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    path = spawn.get("agent_path") if isinstance(spawn, dict) else None
    return path if isinstance(path, str) and path else None


def _own_start(records: list[RolloutRecord], thread_id: str) -> int:
    """The index of the thread's first own record. A spawned agent's file can start
    with a copy of the parent's turns (``fork_turns``); its own turns have a parent turn."""
    foreign = [i for i, r in enumerate(records) if r.type == "session_meta" and r.payload.get("id") not in (None, thread_id)]
    if not foreign:
        return 0
    for i in range(foreign[-1], len(records)):
        r = records[i]
        if r.type == "event_msg" and r.payload.get("type") == "task_started":
            attribution = r.payload.get("turn_attribution")
            if isinstance(attribution, dict) and attribution.get("parent_turn_id"):
                return i
    return len(records)


def _link_agent_messages(messages: list[dict[str, Any]], records: list[RolloutRecord], meta: dict[str, Any], thread_id: str) -> str | None:
    """Issue #91: a ``peer_ref`` and a private index key on each received agent
    message and on each own send call. Returns the thread's agent path when it has any."""
    own_path = own_agent_path(meta)
    if own_path is None:
        return None
    start = _own_start(records, thread_id)
    sends: dict[str, tuple[str, str]] = {}  # call_id -> (recipient path, digest)
    for r in records[start:]:
        p = r.payload
        if r.type != "response_item" or p.get("type") != "function_call" or p.get("name") not in _SEND_TOOLS:
            continue
        args = _decode_json_text(p.get("arguments", {}))
        if not isinstance(args, dict):
            continue
        target, message = args.get(_SEND_TOOLS[p["name"]]), args.get("message")
        if not isinstance(target, str) or not target or not isinstance(message, str) or not isinstance(p.get("call_id"), str):
            continue
        recipient = target if target.startswith("/") else f"{own_path.rstrip('/')}/{target}"
        sends[p["call_id"]] = (recipient, _digest(message))
    found = False
    for m in messages:
        for b in m.get("content") or []:
            if b.get("type") != "tool_use":
                continue
            digest = b.pop("_peer_digest", None)
            inp = b.get("input") or {}
            if digest is not None and inp.get("recipient") == own_path and isinstance(inp.get("author"), str):
                b["peer_ref"] = {"kind": "received", "name": inp["author"], "link_state": "unknown"}
                b["_peer_key"] = f"cx|{own_path}|{inp['author']}|{digest}"
                found = True
            elif b.get("id") in sends:
                recipient, sent_digest = sends.pop(b["id"])
                b["peer_ref"] = {"kind": "sent", "name": recipient, "link_state": "unknown"}
                b["_peer_key"] = f"cx|{recipient}|{own_path}|{sent_digest}"
                found = True
    return own_path if found else None


# ---------------------------------------------------------------------------
# Legacy history
# ---------------------------------------------------------------------------


def _legacy_messages(
    records: list[RolloutRecord], thread_id: str, warnings: list[str]
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    code_mode = _CodeMode(records)
    calls: dict[str, dict[str, Any]] = {}  # call_id -> the message that holds the call
    turn_of: dict[str, int] = {}  # message uuid -> turn; each human message starts a turn
    turn = 0
    compactions = _Compactions()
    agent_ids: set[str] = set()  # agent messages already shown, by id
    model = ""
    turn_id: str | None = None  # the turn_context turn; ``turn`` counts the human messages
    for r in records:
        if r.type == "turn_context":
            model = r.payload.get("model") or model
            turn_id = r.payload.get("turn_id") or turn_id
            continue
        if _is_agent_message(r):
            if _seen_agent_message(r, agent_ids):
                continue
            msg = _agent_message(r, thread_id, r.payload.get("turn_id") or turn_id, model)
            turn_of[msg["uuid"]] = turn
            messages.append(msg)
            continue
        rollback = _rollback_turns(r)
        if rollback is not None:
            dropped = _drop_last_turns(list(dict.fromkeys(turn_of[m["uuid"]] for m in messages)), rollback)
            messages = [m for m in messages if turn_of[m["uuid"]] not in dropped]
            compactions.drop(dropped)
            continue
        is_event = r.type == "event_msg" and r.payload.get("type") == "context_compacted"
        if r.type == "compacted" or is_event:
            row = compactions.add(turn, "event" if is_event else "record", None if is_event else _compaction_summary(r), r)
            if row is not None:
                msg = _compaction_message(message_uuid(thread_id, line_no=r.line_no), row, model)
                turn_of[msg["uuid"]] = turn
                messages.append(msg)
            continue
        if r.type != "response_item":
            continue
        p = r.payload
        kind = p.get("type")
        if kind in _LEGACY_SKIPPED:
            continue
        sender = "assistant"
        blocks: list[dict[str, Any]]
        if kind == "message":
            if p.get("role") not in ("user", "assistant"):
                continue  # developer and system instructions
            sender = "human" if p["role"] == "user" else "assistant"
            blocks = _user_content(p.get("content")) if sender == "human" else [
                _text(c["text"]) for c in p.get("content") or []
                if isinstance(c, dict) and c.get("text")
            ]
            if sender == "human" and all(_is_injected(b.get("text", "")) for b in blocks):
                continue
        elif kind == "reasoning":
            summary = [s.get("text", "") for s in p.get("summary") or [] if isinstance(s, dict)]
            blocks = [_reasoning(summary)]
        elif kind in _LEGACY_CALLS and p.get("call_id") in code_mode.waits:
            continue  # its output is part of the run
        elif kind in _LEGACY_CALLS and p.get("call_id") in code_mode.runs:
            blocks = [code_mode.block(p["call_id"])]
        elif kind in _LEGACY_CALLS:
            blocks = [_legacy_call(p)]
        elif kind in _LEGACY_OUTPUTS and (p.get("call_id") in code_mode.runs or p.get("call_id") in code_mode.waits):
            continue  # the run holds it
        elif kind in _LEGACY_OUTPUTS:
            result = {"type": "tool_result", "tool_use_id": p.get("call_id"), "content": [_text(_output_text(p.get("output")))]}
            owner = calls.get(p.get("call_id"))
            if owner is not None:
                owner["content"].append(result)
                owner["updated_at"] = r.timestamp or owner["updated_at"]
                continue
            warnings.append(f"tool output without a call: {p.get('call_id')}")
            blocks = [result]
        else:
            blocks = [_unsupported(str(kind), warnings)]
        if not blocks:
            continue
        msg = _message(message_uuid(thread_id, line_no=r.line_no), sender, blocks, r.timestamp, r.timestamp, model)
        turn += sender == "human"
        turn_of[msg["uuid"]] = turn
        messages.append(msg)
        if kind in _LEGACY_CALLS and p.get("call_id"):
            calls[p["call_id"]] = msg
    code_mode.finish()
    return messages


def _is_injected(text: str) -> bool:
    return text.lstrip().startswith(_INJECTED_PREFIXES)


def _legacy_call(p: dict[str, Any]) -> dict[str, Any]:
    if p.get("type") == "local_shell_call":
        name, args = "shell", (p.get("action") or {})
    else:
        name, args = p.get("name") or "", p.get("arguments", p.get("input"))
    return {"type": "tool_use", "id": p.get("call_id"), "name": name, "input": _decode_json_text(args)}


def _decode_json_text(value: Any) -> dict[str, Any]:
    """Tool arguments are often JSON text inside a string; ``input`` must be a dict."""
    if isinstance(value, str):
        try:
            value = orjson.loads(value)
        except orjson.JSONDecodeError:
            return {"input": value}
    return value if isinstance(value, dict) else {"input": value}


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        try:
            decoded = orjson.loads(output)
        except orjson.JSONDecodeError:
            return output
        # Older Codex wrapped the output as {"output": "...", "metadata": {...}}.
        if isinstance(decoded, dict) and isinstance(decoded.get("output"), str):
            return decoded["output"]
        return output
    if isinstance(output, dict):
        return _output_text(output.get("content", output.get("output", "")))
    if isinstance(output, list):
        parts = []
        for c in output:
            if isinstance(c, dict) and c.get("text"):
                parts.append(c["text"])
            elif isinstance(c, dict) and "image" in str(c.get("type")):
                parts.append(_image_label(c)["text"])
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Blocks and messages
# ---------------------------------------------------------------------------


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _reasoning(summary: list[str]) -> dict[str, Any]:
    text = "\n\n".join(s for s in summary if isinstance(s, str) and s)
    # An empty summary is what Codex keeps when the reasoning is encrypted. Never decrypt it.
    text = text or ENCRYPTED_REASONING
    return {"type": "thinking", "thinking": text, "text": text}


def _image_label(c: dict[str, Any]) -> dict[str, Any]:
    # A label only: never fetch the URL or open the path (plan §4.4, media).
    path = c.get("path")
    if c.get("type") == "local_image" and isinstance(path, str) and path:
        return _text(f"[Image: {path.replace(chr(92), '/').rsplit('/', 1)[-1]}]")
    return _text("[Image]")


def _unsupported(item_type: str, warnings: list[str]) -> dict[str, Any]:
    warnings.append(f"unsupported Codex item: {item_type}")
    return _text(f"Unsupported Codex item: {item_type}")


def _message(
    msg_uuid: str,
    sender: str,
    blocks: list[dict[str, Any]],
    created_at: str | None,
    updated_at: str | None,
    model: str,
) -> dict[str, Any]:
    return {
        "uuid": msg_uuid,
        "sender": sender,
        "text": "\n".join(b["text"] for b in blocks if b["type"] == "text"),
        "content": blocks,
        "created_at": created_at or "",
        "updated_at": updated_at or created_at or "",
        "truncated": False,
        "parent_message_uuid": None,
        "model": model,
        "attachments": [],
        "files": [],
    }
