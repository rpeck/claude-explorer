"""Messages between sessions (issue #91): rows, index keys, and links.

Claude Code: ``SendMessage`` sends, received messages (a ``user`` row with a
peer ``origin``, or a mid-turn ``queued_command`` attachment), and idle
notices. Codex: agent messages between the threads of one tree (keys from
``backend.codex.projection``).

The reader sets ``peer_ref`` on rows and blocks with ``link_state`` "unknown".
The store fills the other side from the ``peer_messages`` table of the search
index (``backend.search_index``), outside the detail cache: the links change
when the other session changes.

Links are exact or absent: ``msg_id`` for Claude Code; recipient path, sender
path, and equal payload for Codex, within one thread tree. Never by name,
socket, or time alone. A second key (for example a Remote Control id from a
``bridge-session`` record) is one more key kind in :func:`peer_index_rows`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

#: Bump when the Claude Code rows or keys change: the index then reads every
#: Claude Code file again (``SearchIndex._mark_stale_projections``).
CC_PEER_PROJECTION_VERSION = 1

SEND_TOOL = "SendMessage"
_ENVELOPE_RE = re.compile(r"<cross-session-message\b[^>]*>\n?(.*?)\n?</cross-session-message>", re.DOTALL)
_IDLE_PREFIX = "[Cross-session idle notice]"
_IDLE_NAME_RE = re.compile(r'^\[Cross-session idle notice\]\s*"([^"]+)"')
_BECAUSE_MAX = 200

#: Index row: (msg_id, message_uuid, block_id, direction, peer_name, because_uuid, because_text).
IndexRow = tuple[str, str, str, str, "str | None", "str | None", "str | None"]


def new_ref(kind: str, name: str | None, **fields: Any) -> dict[str, Any]:
    """A ``PeerRef`` dict before the store join."""
    return {"kind": kind, "msg_id": None, "name": name, "conversation_uuid": None, "message_uuid": None,
            "title": None, "link_state": "unknown", "mode": None, "because_text": None,
            "because_message_uuid": None, "notify_message_uuid": None, **fields}


def peer_origin(entry: dict) -> dict | None:
    """The peer ``origin`` of a received-message row, else None."""
    if entry.get("type") == "attachment":
        att = entry.get("attachment")
        origin = att.get("origin") if isinstance(att, dict) and att.get("type") == "queued_command" else None
    elif entry.get("type") == "user":
        origin = entry.get("origin")
    else:
        return None
    return origin if isinstance(origin, dict) and origin.get("kind") == "peer" else None


def queued_prompt_text(entry: dict) -> str:
    """The text of a mid-turn ``queued_command`` attachment."""
    prompt = (entry.get("attachment") or {}).get("prompt")
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        return "\n".join(b.get("text", "") for b in prompt if isinstance(b, dict) and b.get("type") == "text")
    return ""


def sent_msg_ids(entry: dict) -> dict[str, str]:
    """``{tool_use_id: msg_id}`` of a SendMessage result row."""
    result = entry.get("toolUseResult")
    msg_id = result.get("msg_id") if isinstance(result, dict) else None
    content = (entry.get("message") or {}).get("content")
    if not isinstance(msg_id, str) or not msg_id or not isinstance(content, list):
        return {}
    return {
        b["tool_use_id"]: msg_id
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str)
    }


def received_body(origin: dict, text: str) -> str:
    """The message without its envelope: ``origin.body``, else the text inside the tags."""
    body = origin.get("body")
    if isinstance(body, str):
        return body.strip()
    m = _ENVELOPE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:_BECAUSE_MAX]


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _strip_private(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_peer")} if any(k.startswith("_peer") for k in m) else m


def _is_idle(m: dict) -> bool:
    return m.get("sender") == "human" and str(m.get("text") or "").lstrip().startswith(_IDLE_PREFIX)


def _is_send(b: Any) -> bool:
    return isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == SEND_TOOL


def classify_peer_rows(messages: list[dict]) -> list[dict]:
    """Received messages and idle notices become peer rows; SendMessage calls get a ``peer_ref``.

    One received row per ``msg_id``: the first wins. Pure: changed messages
    and blocks are copies.
    """
    if not any("_peer_origin" in m or "_peer_sent" in m or _is_idle(m) for m in messages):
        return messages
    sent: dict[str, str] = {}
    for m in messages:
        sent.update(m.get("_peer_sent") or {})
    out: list[dict] = []
    seen: set[str] = set()
    # Per name: the notify_when_idle sends that no notice used yet, oldest first.
    notify: dict[str, list[str]] = {}
    for m in messages:
        origin = m.get("_peer_origin")
        if origin is not None:
            msg_id = _str(origin.get("msg_id"))
            if msg_id in seen:
                continue
            if msg_id:
                seen.add(msg_id)
            body = received_body(origin, str(m.get("text") or ""))
            ref = new_ref("received", _str(origin.get("name")) or _str(origin.get("from")),
                          msg_id=msg_id, mode=_str(origin.get("fromMode")))
            out.append({**_strip_private(m), "text": body, "content": [{"type": "text", "text": body}], "peer_ref": ref})
            continue
        if _is_idle(m):
            name_m = _IDLE_NAME_RE.match(str(m.get("text") or "").strip())
            name = name_m.group(1) if name_m else None
            asks = notify.get(name or "") or []
            ref = new_ref("idle_notice", name, notify_message_uuid=asks.pop() if asks else None)
            out.append({**_strip_private(m), "peer_ref": ref})
            continue
        blocks = m.get("content") or []
        if m.get("sender") == "assistant" and any(_is_send(b) for b in blocks):
            new_blocks = []
            for b in blocks:
                if _is_send(b):
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    to = _str(inp.get("to"))
                    msg_id = sent.get(str(b.get("id") or ""))
                    if msg_id:
                        b = {**b, "peer_ref": new_ref("sent", to, msg_id=msg_id)}
                    if inp.get("notify_when_idle") is True and to:
                        notify.setdefault(to, []).append(m.get("uuid", ""))
                new_blocks.append(b)
            m = {**m, "content": new_blocks}
        out.append(_strip_private(m))
    return out


# --- index rows ---------------------------------------------------------------


def _is_because(m: dict) -> bool:
    """A user prompt or a received message: what can lead to a send."""
    ref = m.get("peer_ref")
    if isinstance(ref, dict):
        return ref.get("kind") == "received"
    if m.get("sender") != "human" or m.get("notice_kind") or m.get("shell_command") or m.get("shell_output"):
        return False
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m.get("content") or [])


def peer_index_rows(conv: dict) -> list[IndexRow]:
    """The ``peer_messages`` rows of one conversation.

    ``direction`` is "sent", "received", or "thread" (a Codex thread's own
    agent path, for links at session level).
    """
    rows: list[IndexRow] = []
    if conv.get("agent_path"):
        rows.append((f"cxpath|{conv['agent_path']}", "", "", "thread", None, None, None))
    because: dict | None = None
    for m in conv.get("chat_messages") or []:
        uuid = m.get("uuid") or ""
        ref = m.get("peer_ref")
        if isinstance(ref, dict) and ref.get("kind") == "received" and ref.get("msg_id"):
            rows.append((ref["msg_id"], uuid, "", "received", ref.get("name"), None, None))
        for b in m.get("content") or []:
            bref = b.get("peer_ref") if isinstance(b, dict) else None
            if not isinstance(bref, dict):
                continue
            key = bref.get("msg_id") or b.get("_peer_key")
            if not key:
                continue
            cause = because if bref["kind"] == "sent" and bref.get("msg_id") else None
            rows.append((
                key, uuid, str(b.get("id") or ""), bref["kind"], bref.get("name"),
                cause.get("uuid") if cause else None,
                first_line(str(cause.get("text") or "")) if cause else None,
            ))
        if _is_because(m) and str(m.get("text") or "").strip():
            because = m
    return rows


# --- links (the store join) ---------------------------------------------------


def compute_links(
    own: list[dict], others: list[dict], same_tree: Callable[[str], bool] = lambda _uuid: True
) -> dict[tuple[str, str], dict]:
    """The links of one conversation from its index rows (:meth:`SearchIndex.peer_rows`).

    Returns ``{(message_uuid, block_id): fields}`` to merge into each
    ``peer_ref``. A row with no match is "not_in_archive". Codex rows match
    only threads of the same tree.
    """
    by_key: dict[str, list[dict]] = {}
    for r in others:
        if r["msg_id"].startswith("cx") and not same_tree(r["session_uuid"]):
            continue
        by_key.setdefault(r["msg_id"], []).append(r)
    links: dict[tuple[str, str], dict] = {}
    for r in own:
        if r["direction"] == "thread":
            continue
        matches = [m for m in by_key.get(r["msg_id"], []) if m["direction"] not in (r["direction"], "thread")]
        convs = {m["session_uuid"] for m in matches}
        fields: dict[str, Any] = {"link_state": "not_in_archive"}
        if len(convs) == 1:
            m = matches[0]
            fields = {"link_state": "linked", "conversation_uuid": m["session_uuid"], "title": m.get("title") or None,
                      "message_uuid": m["message_uuid"] or None if len(matches) == 1 else None}
            if r["direction"] == "received" and len(matches) == 1 and m.get("because_uuid"):
                fields.update(because_message_uuid=m["because_uuid"], because_text=m.get("because_text"))
        elif not convs and r["msg_id"].startswith("cx|"):
            threads = by_key.get(f"cxpath|{r['peer_name']}", [])
            if len({t["session_uuid"] for t in threads}) == 1:
                t = threads[0]
                fields = {"link_state": "linked", "conversation_uuid": t["session_uuid"],
                          "title": t.get("title") or None, "message_uuid": None}
        links[(r["message_uuid"], r["block_id"])] = fields
    return links


def _merge_refs(items: list[tuple[str, Any, str, dict]], links: dict) -> dict[int, dict]:
    """``{id(holder): the merged peer_ref}`` for ``(message_uuid, holder, block_id, ref)`` items."""
    merged: dict[int, dict] = {}
    send_refs: dict[tuple[str, str | None], dict] = {}  # (message uuid, to) -> the sent ref
    for m_uuid, holder, block_id, ref in items:
        if ref["kind"] != "idle_notice":
            merged[id(holder)] = new = {**ref, **links.get((m_uuid, block_id), {})}
            if ref["kind"] == "sent":
                send_refs.setdefault((m_uuid, ref.get("name")), new)
    keep = ("conversation_uuid", "message_uuid", "title", "link_state")
    for _m_uuid, holder, _block_id, ref in items:
        if ref["kind"] == "idle_notice":
            # Through the asker's own notify_when_idle send in this session.
            via = send_refs.get((ref.get("notify_message_uuid") or "", ref.get("name"))) or {}
            merged[id(holder)] = {**ref, **{k: via[k] for k in keep if k in via}}
    return merged


def peers_of(refs: list[dict]) -> list[dict[str, Any]]:
    """The "Messages with:" list: one entry per peer, in the order of the first message."""
    peers: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in refs:
        if ref.get("conversation_uuid"):
            key = ("c", ref["conversation_uuid"])
        elif ref.get("name"):
            key = ("n", ref["name"])
        else:
            continue
        peers.setdefault(key, {"conversation_uuid": ref.get("conversation_uuid"), "name": ref.get("name"),
                               "title": ref.get("title"), "link_state": ref.get("link_state") or "unknown"})
    return list(peers.values())


def link_detail_dict(detail: dict[str, Any], links: dict | None) -> dict[str, Any]:
    """A copy of a detail dict (the JSON shape) with the links and ``peers``.

    ``links=None``: the index cannot answer, so the links stay "unknown".
    The input is not changed.
    """
    messages = detail.get("messages") or []
    items = []
    for m in messages:
        if isinstance(m.get("peer_ref"), dict):
            items.append((m["uuid"], m, "", m["peer_ref"]))
        for b in m.get("content") or []:
            if isinstance(b.get("peer_ref"), dict):
                items.append((m["uuid"], b, b.get("id") or "", b["peer_ref"]))
    if not items:
        # The same dict as the cache holds (the dict cache's identity contract).
        return detail if detail.get("peers") == [] else {**detail, "peers": []}
    merged = _merge_refs(items, links or {})
    out = []
    for m in messages:
        new_m = {**m, "peer_ref": merged[id(m)]} if id(m) in merged else m
        blocks = m.get("content") or []
        if any(id(b) in merged for b in blocks):
            new_m = {**new_m, "content": [{**b, "peer_ref": merged[id(b)]} if id(b) in merged else b for b in blocks]}
        out.append(new_m)
    return {**detail, "messages": out, "peers": peers_of([merged[id(h)] for _, h, _, _ in items])}


def link_detail_model(detail: Any, links: dict | None) -> None:
    """:func:`link_detail_dict`, in place on a ``ConversationDetail``."""
    from .models import PeerRef, PeerSession

    items = []
    for m in detail.messages:
        if m.peer_ref is not None:
            items.append((m.uuid, m, "", m.peer_ref.model_dump()))
        for b in m.content:
            if b.peer_ref is not None:
                items.append((m.uuid, b, b.id or "", b.peer_ref.model_dump()))
    merged = _merge_refs(items, links or {})
    for _, holder, _, _ in items:
        holder.peer_ref = PeerRef(**merged[id(holder)])
    detail.peers = [PeerSession(**p) for p in peers_of([merged[id(h)] for _, h, _, _ in items])]
