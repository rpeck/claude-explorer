"""MCP regression tests for the ConversationListItem split.

These two tests pin the MCP-side public contract that the
``ConversationSummary`` -> ``ConversationListItem`` split MUST NOT
break:

* ``list_sessions`` still includes ``human_message_count`` per session
  (schema-stable in ``mcp_server/SPEC.md``).
* ``export_session`` still receives ``summary`` from the underlying
  ``ConversationDetail`` and threads it through the Markdown export.

NOTE: The plan and the article both originally cited a non-existent
``get_session`` tool here. The real ``summary``-consuming tool is
``export_session`` — see ``mcp_server/server.py`` line ~633 where
``conversation.summary`` is copied into the sliced ``ConversationDetail``
before ``conversation_to_markdown`` runs. Test #8 below targets
``export_session`` accordingly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_server.server import export_session, list_sessions


def _call_list_sessions(**kwargs: Any) -> Any:
    fn = getattr(list_sessions, "fn", list_sessions)
    return fn(**kwargs)


def _call_export_session(**kwargs: Any) -> Any:
    fn = getattr(export_session, "fn", export_session)
    return fn(**kwargs)


def _write_desktop_with_summary(
    data_dir: Path,
    *,
    uuid: str,
    name: str,
    summary: str,
) -> None:
    """Plant a Desktop JSON with a known ``summary`` field. The MCP
    server reads through ``ConversationStore`` and ``ConversationDetail``,
    both of which already carry ``summary`` via the unchanged base shape.
    """
    blob = {
        "uuid": uuid,
        "name": name,
        "summary": summary,
        "model": "claude-sonnet-4-6",
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-04-01T11:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": "msg-2",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "sender": "human",
                "text": "Hi there.",
                "content": [{"type": "text", "text": "Hi there."}],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
            {
                "uuid": "msg-2",
                "sender": "assistant",
                "text": "Hello back.",
                "content": [{"type": "text", "text": "Hello back."}],
                "created_at": "2026-04-01T10:00:30Z",
                "updated_at": "2026-04-01T10:00:30Z",
                "parent_message_uuid": "msg-1",
            },
        ],
    }
    (data_dir / f"{uuid}.json").write_text(json.dumps(blob))


def test_mcp_list_sessions_still_includes_human_message_count(mcp_data):
    """7. ``list_sessions`` MUST keep emitting ``human_message_count`` on
    every session entry. That field is schema-stable in
    ``mcp_server/SPEC.md`` and was the reason the audit kept it on
    ``ConversationSummary`` rather than dropping it outright. The split
    moves it OFF the list-item wire format but leaves it on the underlying
    Pydantic model — and therefore on this MCP tool's output.
    """
    u1 = mcp_data.add_desktop_session("u-1", name="Session with two messages")
    result = _call_list_sessions()
    assert result["total"] == 1
    s = result["sessions"][0]
    assert s["uuid"] == u1
    assert "human_message_count" in s, (
        f"list_sessions dropped human_message_count from its output; "
        f"keys present = {sorted(s.keys())}"
    )
    # Underlying store value: two messages, one human.
    assert s["human_message_count"] == 1


def test_mcp_export_session_preserves_summary_on_sliced_copy(mcp_data):
    """8. ``export_session`` constructs a sliced ``ConversationDetail``
    copy when ``start_position``/``end_position`` is given
    (``mcp_server/server.py`` line ~630). The constructor explicitly
    threads ``summary=conversation.summary`` from the source to the
    sliced copy, so the field MUST remain on ``ConversationSummary``
    (the base class) — otherwise the kwarg becomes invalid and
    ``export_session`` raises at call time.

    NOTE: The plan's original wording said "MCP get_session output
    includes summary" but ``get_session`` does not exist as a real
    MCP tool (the five real tools are ``list_sessions``,
    ``list_projects``, ``get_session_outline``, ``get_messages``,
    ``export_session``), and ``conversation_to_markdown`` does not
    currently emit the summary field into the rendered markdown.
    The actual schema-stable contract being protected here is that
    the sliced-copy construction path in ``export_session`` keeps
    working — which requires ``summary`` to stay on the
    ``ConversationSummary`` Pydantic model.

    The test exercises the sliced-copy branch and asserts the call
    succeeds end-to-end with no ``TypeError`` / ``ValidationError``.
    A separate Pydantic-level guard asserts the field still exists
    on the model.
    """
    from backend.models import ConversationSummary

    # Pydantic-level guard: summary MUST still be a field on the model
    # that export_session passes to ConversationDetail's constructor.
    assert "summary" in ConversationSummary.model_fields, (
        "ConversationSummary.summary must remain on the base model so "
        "export_session's sliced-copy kwarg keeps validating. The split "
        "moves the field off the LIST wire format only — not the model."
    )

    needle = "EXPORT_SUMMARY_NEEDLE_TOKEN"
    u1 = mcp_data.uuid_for("u-export-1")
    _write_desktop_with_summary(
        mcp_data.data_dir,
        uuid=u1,
        name="Export summary test",
        summary=needle,
    )

    # The sliced-copy branch in export_session() runs when start_position
    # is set. If `summary` were dropped from ConversationSummary, the
    # `summary=conversation.summary` kwarg would either AttributeError
    # (no field on source) or Pydantic-ValidationError (no field on
    # target). Either case would surface here as an exception.
    md = _call_export_session(conversation_id=u1, start_position=0)
    assert isinstance(md, str)
    # End-to-end sanity: the export contains the conversation title,
    # proving the sliced ConversationDetail constructor returned a
    # valid object that the markdown converter accepted.
    assert "Export summary test" in md
