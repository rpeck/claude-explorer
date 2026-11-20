"""Project Codex rollout records into Explorer's conversation dict (slice 1).

Plan §4.4 (N4 part) and §4.5. Codex keeps one of two history modes:

* **paginated:** ``event_msg`` / ``item_completed`` records carry the
  visible items. A later snapshot of an item replaces the earlier one.
  ``response_item`` records are the model context, so they are skipped.
* **legacy:** ``response_item`` records are the history. A tool call
  matches its output by ``call_id``.

Records that change the history (compaction, rollback, ``history_base``)
are not rebuilt here. They mark the session ``incomplete`` instead; slice 2
(N10) rebuilds them.
"""

from __future__ import annotations

import re
import uuid as uuid_mod
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote

import orjson

from .rollout import RolloutRecord

ENCRYPTED_REASONING = "Encrypted reasoning (not readable)"

# Fixed, so a message id never changes between processes or versions.
_NAMESPACE = uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, "https://github.com/rpeck/claude-explorer/codex")

_RECORD_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

# Record types that never hold a visible turn (``RolloutItem`` in Codex 0.170).
_KNOWN_RECORD_TYPES = {
    "session_meta", "dynamic_tools_updated", "turn_context", "response_item", "event_msg",
    "inter_agent_communication", "inter_agent_communication_metadata", "world_state",
    "token_usage_record", "security_risk_score", "retained_context", "realtime_item", "compacted",
}

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
) -> dict[str, Any]:
    """Build the conversation dict. ``history_mode`` is the ``state_5`` value, if any."""
    records = list(records)
    meta = next((r.payload for r in records if r.type == "session_meta"), {})
    thread_id = thread_id or meta.get("id") or ""

    paginated = history_mode == "paginated" or any(_is_item_completed(r) for r in records)
    warnings = ["damaged line skipped"] * damaged_lines
    reasons = _incomplete_reasons(records, current_ambiguous)

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

    stamps = [r.timestamp for r in records if r.timestamp]
    created_at = meta.get("timestamp") or (stamps[0] if stamps else "")
    return {
        "uuid": thread_id,
        "chat_messages": messages,
        "compact_markers": [],
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "model": session_model,
        "project_path": meta.get("cwd") or "",
        "created_at": created_at,
        "updated_at": stamps[-1] if stamps else created_at,
        "history_mode": "paginated" if paginated else "legacy",
        "incomplete": bool(reasons),
        "incomplete_reasons": reasons,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# The incomplete classifier
# ---------------------------------------------------------------------------


def _incomplete_reasons(records: list[RolloutRecord], current_ambiguous: bool) -> list[str]:
    """The reasons why slice 1 cannot rebuild this history, in first-seen order.

    It checks the records, never ``thread_source``: a fork or subagent with
    no ``history_base`` is complete, only its parent link is missing.
    """
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if current_ambiguous:
        add("current_ambiguous")
    for r in records:
        kind = r.payload.get("type") if r.type in ("event_msg", "response_item") else r.type
        kind = kind if isinstance(kind, str) else ""
        item = r.payload.get("item") if _is_item_completed(r) else None
        if (
            r.type == "compacted"
            or kind in ("context_compacted", "compacted")
            or (isinstance(item, dict) and item.get("type") == "ContextCompaction")
        ):
            add("compacted")
        elif _is_rollback(kind):
            add("rollback")
        elif r.type not in _KNOWN_RECORD_TYPES:
            # A raw type can hold anything; only a plain name reaches the notice.
            add(f"unknown record type: {r.type}" if _RECORD_TYPE_RE.fullmatch(r.type) else "unknown record type")
        if r.type == "history_base" or r.payload.get("history_base") is not None:
            add("history_base")
    return reasons


def _is_rollback(kind: str) -> bool:
    # shortcut: the slice 1 fake has no rollback record, so this matches by name;
    # pin the exact Codex type names when N10 adds them to the fake.
    k = kind.lower()
    return any(word in k for word in ("rollback", "rolled_back", "revert", "undo"))


# ---------------------------------------------------------------------------
# Paginated history
# ---------------------------------------------------------------------------


def _is_item_completed(r: RolloutRecord) -> bool:
    return r.type == "event_msg" and r.payload.get("type") == "item_completed"


def _paginated_messages(
    records: list[RolloutRecord],
    thread_id: str,
    turn_models: dict[str, str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    # (turn id, item id) -> [first record, latest record]; a dict keeps first-seen order.
    # An inter-agent message has no snapshots: its key is its line number.
    items: dict[tuple[str, str] | int, list[RolloutRecord]] = {}
    turns: dict[int, str | None] = {}  # line number -> the turn of an inter-agent message
    turn: str | None = None
    for r in records:
        if r.type == "turn_context":
            turn = r.payload.get("turn_id") or turn
        if r.type == "inter_agent_communication":
            items[r.line_no] = [r, r]
            turns[r.line_no] = r.payload.get("turn_id") or turn
        if not _is_item_completed(r) or not isinstance(r.payload.get("item"), dict):
            continue
        turn_id = str(r.payload.get("turn_id") or "")
        item_id = str(r.payload["item"].get("id") or f"line-{r.line_no}")
        slot = items.setdefault((turn_id, item_id), [r, r])
        slot[1] = r

    messages = []
    for key, (first, latest) in items.items():
        if isinstance(key, int):
            turn = turns[key]
            messages.append(_agent_message(first, thread_id, turn, turn_models.get(turn or "", "")))
            continue
        turn_id, item_id = key
        item = latest.payload["item"]
        item_type = item.get("type")
        if item_type == "ContextCompaction":
            continue  # marks the session incomplete; N10 adds a CompactMarker
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
            blocks = _tool(item, *_TOOL_ITEMS[item_type](item))
        else:
            blocks = [_unsupported(str(item_type), warnings)]
        if not blocks:
            continue
        messages.append(
            _message(
                message_uuid(thread_id, turn_id=turn_id, item_id=item_id),
                sender, blocks, first.timestamp, latest.timestamp,
                turn_models.get(turn_id, ""),
            )
        )
    return messages


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


def _agent_message(
    r: RolloutRecord, thread_id: str, turn_id: str | None, model: str
) -> dict[str, Any]:
    """An ``inter_agent_communication`` record: a message between agents, in both history modes."""
    p = r.payload
    content = p.get("content") if isinstance(p.get("content"), str) else ""
    if not content and p.get("encrypted_content"):
        content = "[Encrypted agent message]"  # never decode it
    comm_id = p.get("id") if isinstance(p.get("id"), str) and p.get("id") else None
    msg_uuid = (
        message_uuid(thread_id, turn_id=turn_id, item_id=comm_id)
        if comm_id else message_uuid(thread_id, line_no=r.line_no)
    )
    blocks = _tool({"id": comm_id or f"line-{r.line_no}"}, "agent_message",
                   _present(p, "author", "recipient", "trigger_turn"), content)
    return _message(msg_uuid, "assistant", blocks, r.timestamp, r.timestamp, model)


# ---------------------------------------------------------------------------
# Legacy history
# ---------------------------------------------------------------------------


def _legacy_messages(
    records: list[RolloutRecord], thread_id: str, warnings: list[str]
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}  # call_id -> the message that holds the call
    model = ""
    turn: str | None = None
    for r in records:
        if r.type == "turn_context":
            model = r.payload.get("model") or model
            turn = r.payload.get("turn_id") or turn
            continue
        if r.type == "inter_agent_communication":
            messages.append(_agent_message(r, thread_id, r.payload.get("turn_id") or turn, model))
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
        elif kind in _LEGACY_CALLS:
            blocks = [_legacy_call(p)]
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
        messages.append(msg)
        if kind in _LEGACY_CALLS and p.get("call_id"):
            calls[p["call_id"]] = msg
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
