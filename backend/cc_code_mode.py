"""Fold Claude Code's Code Mode records into one ``code_run`` block per run (issue #90).

Claude Code 2.1.331 records a run in four parts (shape from a recorded
session; see ``backend/tests/cc_code_mode_fake.py``):

* the outer assistant ``tool_use`` named ``REPL`` (``input.code``);
* the outer ``tool_result``, with ``toolUseResult`` or only the
  ``<repl-output>`` text (subagent files);
* the inner calls: ``isVirtual`` records with ``ranByToolUseId`` = the outer
  id, written AFTER the outer result; their content is in ``hostContent``;
* for a background script, ``(dispatched #N)`` and a later ``poll_events``
  attachment with ``<event kind="repl-eval" eval="N" ...>``.

Inner calls link only by ``ranByToolUseId``. A virtual call with a Code Mode
id and no parent field stays a tool call, marked ``from_code_mode``.
"""

from __future__ import annotations

import html
import re
from typing import Any

# The UI label of the tool is "Code"; ``Eval`` is an alias in the binary.
REPL_TOOL_NAMES = frozenset({"REPL", "Eval"})
CODE_MODE_ID_PREFIXES = ("toolu_cc_", "repl_")

_TAG_RE = re.compile(r"<(result|log|error|refused)>(.*?)</\1>", re.DOTALL)
_EVENT_RE = re.compile(r"<event\b([^>]*)>(.*?)</event>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_DISPATCHED_RE = re.compile(r"^\(dispatched #(\d+)\)$")


def _repl_parts(text: str) -> dict[str, str]:
    """The ``<result>``, ``<log>``, ``<error>``, ``<refused>`` parts of a REPL output text."""
    return {m.group(1): html.unescape(m.group(2)) for m in _TAG_RE.finditer(text)}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def _new_run(block: dict[str, Any]) -> dict[str, Any]:
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    code = tool_input.get("code") if isinstance(tool_input.get("code"), str) else ""
    return {
        "language": "javascript",
        "code": code,
        "line_count": code.count("\n") + 1 if code else 0,
        "description": tool_input.get("description") or None,
        "status": "unknown",
        "background": False,
        "result": None,
        "stdout": None,
        "stderr": None,
        "error": None,
        "refused": None,
        "inner_calls": [],
    }


def _apply_result(run: dict[str, Any], block: dict[str, Any], tur: Any) -> int | None:
    """Fill the run from its outer result; the eval id of a background run, else None."""
    text = _text_of(block.get("content"))
    tur = tur if isinstance(tur, dict) and tur.get("code") is not None else {}
    dispatched = _DISPATCHED_RE.match(text.strip())
    if dispatched or tur.get("asyncDispatched"):
        run["background"] = True
        run["status"] = "running"
        eval_id = tur.get("evalId")
        return int(eval_id) if isinstance(eval_id, int) else int(dispatched.group(1)) if dispatched else None
    parts = _repl_parts(text)
    if tur:
        run["result"] = tur.get("result") if isinstance(tur.get("result"), str) else None
        run["stdout"] = tur.get("stdout") or None
        run["stderr"] = tur.get("stderr") or None
        run["error"] = tur.get("error") or None
        run["refused"] = tur.get("refusedCalls") or None
    else:
        run["result"] = parts.get("result")
        run["stdout"] = parts.get("log")
        run["error"] = parts.get("error")
        run["refused"] = parts.get("refused")
    run["status"] = "failed" if run["error"] or block.get("is_error") else "completed"
    return None


def _settle(run: dict[str, Any], body: str, attrs: dict[str, str]) -> None:
    parts = _repl_parts(body)
    run["result"] = parts.get("result")
    run["stdout"] = parts.get("log") or run["stdout"]
    run["error"] = parts.get("error")
    run["refused"] = parts.get("refused") or run["refused"]
    if run["error"]:
        run["status"] = "failed"
    elif attrs.get("status") == "settled":
        run["status"] = "completed"
    else:
        run["status"] = "unknown"


def _host_result(entry: dict[str, Any], call_id: str) -> Any:
    """The content of an inner result: ``hostContent`` when the message content is empty."""
    for block in (entry.get("message") or {}).get("content") or []:
        if isinstance(block, dict) and block.get("tool_use_id") == call_id and block.get("content"):
            return block["content"]
    for block in entry.get("hostContent") or []:
        if isinstance(block, dict) and block.get("tool_use_id") == call_id:
            return block.get("content")
    return ""


def fold_code_mode_runs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the entries with each Code Mode run folded into its outer block.

    Entries are copied before a change; the input list is not changed.
    """
    runs: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("type") != "assistant" or entry.get("isVirtual"):
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in REPL_TOOL_NAMES:
                runs[block.get("id") or ""] = _new_run(block)
    runs.pop("", None)
    if not runs and not any(e.get("isVirtual") for e in entries):
        return entries

    by_eval: dict[int, str] = {}
    calls: dict[str, dict[str, Any]] = {}  # inner call id -> its call
    dropped: dict[str, str | None] = {}  # uuid -> parentUuid, for each removed entry
    out: list[dict[str, Any]] = []
    for entry in entries:
        kind = entry.get("type")
        content = (entry.get("message") or {}).get("content")
        parent = entry.get("ranByToolUseId")
        if entry.get("isVirtual") and kind == "assistant" and parent in runs:
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    call = {
                        "id": block.get("id") or "", "name": block.get("name") or "",
                        "input": block.get("input") if isinstance(block.get("input"), dict) else None,
                        "result": "", "is_error": False, "refused": False,
                        "code_run_parent": parent, "link_source": "transcript_field",
                    }
                    runs[parent]["inner_calls"].append(call)
                    calls[call["id"]] = call
            dropped[entry.get("uuid", "")] = entry.get("parentUuid")
            continue
        if kind == "user" and isinstance(content, list):
            kept = []
            for block in content:
                use_id = block.get("tool_use_id") if isinstance(block, dict) else None
                if use_id in calls:
                    call = calls[use_id]
                    call["result"] = _text_of(_host_result(entry, use_id))
                    call["is_error"] = bool(block.get("is_error"))
                    call["refused"] = bool(entry.get("toolDenialKind"))
                elif use_id in runs:
                    eval_id = _apply_result(runs[use_id], block, entry.get("toolUseResult"))
                    if eval_id is not None:
                        by_eval[eval_id] = use_id
                else:
                    kept.append(block)
            if len(kept) != len(content):
                if not kept:
                    dropped[entry.get("uuid", "")] = entry.get("parentUuid")
                    continue
                entry = {**entry, "message": {**entry["message"], "content": kept}}
            elif entry.get("isVirtual"):
                entry = _with_host_results(entry)
        elif kind == "assistant" and isinstance(content, list):
            if any(isinstance(b, dict) and b.get("id") in runs for b in content):
                entry = {**entry, "message": {**entry["message"], "content": [
                    {"type": "code_run", "id": b["id"], "name": b.get("name"), "code_run": runs[b["id"]]}
                    if isinstance(b, dict) and b.get("id") in runs else b
                    for b in content
                ]}}
            elif entry.get("isVirtual"):
                entry = _mark_unlinked(entry)
        elif kind == "attachment":
            att = entry.get("attachment") or {}
            if att.get("type") == "poll_events":
                for envelope in att.get("envelopes") or []:
                    for m in _EVENT_RE.finditer(envelope if isinstance(envelope, str) else ""):
                        attrs = dict(_ATTR_RE.findall(m.group(1)))
                        run_id = by_eval.get(int(attrs["eval"])) if attrs.get("eval", "").isdigit() else None
                        if attrs.get("kind") == "repl-eval" and run_id:
                            _settle(runs[run_id], m.group(2), attrs)
        out.append(entry)

    if dropped:
        out = [_reparent(e, dropped) for e in out]
    return out


def _mark_unlinked(entry: dict[str, Any]) -> dict[str, Any]:
    content = entry["message"]["content"]
    marked = [
        {**b, "from_code_mode": True}
        if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("id", "")).startswith(CODE_MODE_ID_PREFIXES)
        else b
        for b in content
    ]
    return {**entry, "message": {**entry["message"], "content": marked}}


def _with_host_results(entry: dict[str, Any]) -> dict[str, Any]:
    """An unlinked virtual result: fill an empty content from ``hostContent``."""
    content = [
        {**b, "content": _host_result(entry, b["tool_use_id"])}
        if isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("content") and b.get("tool_use_id")
        else b
        for b in entry["message"]["content"]
    ]
    return {**entry, "message": {**entry["message"], "content": content}}


def _reparent(entry: dict[str, Any], dropped: dict[str, str | None]) -> dict[str, Any]:
    parent = entry.get("parentUuid")
    if parent not in dropped:
        return entry
    seen: set[str] = set()
    while parent in dropped and parent not in seen:
        seen.add(parent)
        parent = dropped[parent]
    return {**entry, "parentUuid": parent}
