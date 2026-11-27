"""The display name of a model and the sender label of a reply (issue #52).

The MCP server imports this module, so it must stay free of heavy imports
(``mcp_server/tests/test_mcpb_closure.py``). The frontend twin is
``frontend/src/lib/modelName.ts``; keep the two in step.
"""

from __future__ import annotations

import re

#: The model that Claude Code writes on the rows that it makes itself,
#: such as an API error. No model wrote them.
SYNTHETIC_MODEL = "<synthetic>"

#: The sources that record one model for the whole conversation, not one
#: for each reply (decision D5). Their replies use the conversation model.
CONVERSATION_MODEL_SOURCES = frozenset({"CLAUDE_AI", "CODEX"})

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
# claude-opus-4-5, claude-sonnet-5
_FAMILY_FIRST_RE = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d+)*)$")
# claude-3-5-sonnet (the older order)
_VERSION_FIRST_RE = re.compile(r"^claude-(\d+(?:-\d+)*)-([a-z]+)$")


def model_display_name(model_id: str) -> str:
    """``claude-opus-4-5-20251101`` → ``Opus 4.5``. An id that does not
    match a Claude model id comes back as it is."""
    if model_id == SYNTHETIC_MODEL:
        return "Claude Code"
    bare = _DATE_SUFFIX_RE.sub("", model_id)
    m = _FAMILY_FIRST_RE.match(bare)
    if m:
        family, version = m.groups()
    else:
        m = _VERSION_FIRST_RE.match(bare)
        if not m:
            return model_id
        version, family = m.groups()
    return f"{family.capitalize()} {version.replace('-', '.')}"


def reply_label(assistant_label: str, model: str | None) -> str:
    """``Claude Opus 5.5``; ``Claude Code`` for a synthetic row; the
    assistant label alone when there is no model."""
    if model == SYNTHETIC_MODEL:
        return "Claude Code"
    if not model:
        return assistant_label
    return f"{assistant_label} {model_display_name(model)}"


def conversation_model_tooltip(source_label: str) -> str:
    """The tooltip of a reply label that comes from the conversation model."""
    return (
        "From the conversation's model setting. "
        f"{source_label} does not record the model of each reply."
    )


NOTICE_LABELS = {
    "task-notification": "Task notification",
    "bash-notification": "Bash notification",
    "scheduled-task": "Scheduled task",
    "system-reminder": "System reminder",
}


def sender_label(message: object, assistant_label: str) -> str:
    """The sender line of a message in the exports and the MCP server: the
    reply label, "You", or the kind of a row that Claude Code wrote."""
    peer_ref = getattr(message, "peer_ref", None)
    if getattr(peer_ref, "kind", None) == "received":
        # Issue #91: a message from another session.
        return f"Message from @{peer_ref.name}" if peer_ref.name else "Message from another session"
    notice_kind = getattr(message, "notice_kind", None)
    if notice_kind:
        return NOTICE_LABELS.get(notice_kind, notice_kind)
    if getattr(message, "shell_command", None) or getattr(message, "shell_output", None):
        return "Shell"
    if getattr(message, "sender", None) == "human":
        return "You"
    return reply_label(assistant_label, getattr(message, "model", None))
