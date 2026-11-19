"""L3: a sliced ``export_session`` keeps every field of the conversation.

The slice path used to rebuild ``ConversationDetail`` field by field, so
each field that the rebuild did not name (``compact_markers``,
``error``, ``sandbox_path``, ...) fell back to its default. The fix
copies the conversation with ``model_copy(update={"messages": ...})``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.xfail(strict=True, reason="L3 not implemented yet")

T0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 4, 1, 11, 0, tzinfo=UTC)
COMPACT_SUMMARY_TEXT = "COMPACT_SUMMARY_MUST_STAY_HIDDEN"


def _message(uuid: str, sender: str, text: str, parent: str | None) -> Any:
    from backend.models import Message

    return Message(
        uuid=uuid,
        sender=sender,
        text=text,
        content=[{"type": "text", "text": text}],
        created_at=T0,
        updated_at=T0,
        parent_message_uuid=parent,
    )


def _messages() -> list[Any]:
    return [
        _message("m-0", "human", "FIRST_MARKER", None),
        _message("m-1", "assistant", COMPACT_SUMMARY_TEXT, "m-0"),
        _message("m-2", "human", "THIRD_MARKER", "m-1"),
        _message("m-3", "assistant", "FOURTH_MARKER", "m-2"),
    ]


def _full_kwargs() -> dict[str, Any]:
    """A value for every ``ConversationDetail`` field, none of them a default."""
    from backend.models import CompactMarker, SubagentSummary

    return {
        "uuid": "00000000-0000-0000-0000-000000000013",
        "name": "Sliced export fields",
        "summary": "A summary",
        "model": "claude-test-model",
        "created_at": T0,
        "updated_at": T1,
        "is_starred": True,
        "message_count": 4,
        "human_message_count": 2,
        "has_branches": True,
        "source": "CLAUDE_COWORK",
        "project_path": "/work/project-l3",
        "project_name": "custom-project-name",
        "git_branch": "feature/l3",
        "organization_id": "org-l3",
        "organization_name": "Org L3",
        "subagents": [
            SubagentSummary(
                uuid="sub-1",
                agent_id="agent-1",
                name="Helper",
                model="claude-test-model",
                created_at=T0,
                updated_at=T1,
                message_count=3,
            )
        ],
        "is_archived": True,
        "messages": _messages(),
        "current_leaf_message_uuid": "m-3",
        "file_path": "/data/l3.jsonl",
        "compact_markers": [
            CompactMarker(
                message_uuid="m-1",
                summary_text="The summary of the compaction",
                timestamp="2026-04-01T10:00:00Z",
                kind="auto",
                user_prompt="Compact now",
            )
        ],
        "prelude_hidden_count": 2,
        "error": "The session ended unexpectedly",
        "sandbox_path": "/sessions/vm-l3",
    }


class _FakeStore:
    def __init__(self, conversation: Any) -> None:
        self.conversation = conversation

    def get_conversation(self, conversation_id: str) -> Any:
        return self.conversation if conversation_id == self.conversation.uuid else None


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch):
    """Serve one conversation through ``export_session``.

    Returns ``run(conversation, **kwargs) -> (markdown, exported)``, where
    ``exported`` is the object that ``export_session`` gave to the
    Markdown converter.
    """
    from mcp_server import server

    def run(conversation: Any, **kwargs: Any) -> tuple[str, Any]:
        seen: list[Any] = []
        real = server.conversation_to_markdown

        def spy(conv: Any, *args: Any, **kw: Any) -> str:
            seen.append(conv)
            return real(conv, *args, **kw)

        monkeypatch.setattr(server, "_get_store", lambda: _FakeStore(conversation))
        monkeypatch.setattr(server, "conversation_to_markdown", spy)
        fn = getattr(server.export_session, "fn", server.export_session)
        md = fn(conversation_id=conversation.uuid, **kwargs)
        assert len(seen) == 1
        return md, seen[0]

    return run


def test_sliced_export_keeps_every_field_except_messages(serve):
    from backend.models import ConversationDetail

    source = ConversationDetail(**_full_kwargs())
    _, exported = serve(source, start_position=1, end_position=2)

    lost = {
        name: (getattr(source, name), getattr(exported, name))
        for name in ConversationDetail.model_fields
        if name != "messages" and getattr(exported, name) != getattr(source, name)
    }
    assert lost == {}, f"fields changed by the slice (source, exported): {lost}"
    assert [m.uuid for m in exported.messages] == ["m-1", "m-2"]


def test_sliced_export_keeps_fields_of_a_conversation_subclass(serve):
    """A field that a later node adds to the model survives the slice."""
    from backend.models import ConversationDetail

    class ExtendedDetail(ConversationDetail):
        extra_field: str = ""

    source = ExtendedDetail(**_full_kwargs(), extra_field="EXTRA_VALUE")
    _, exported = serve(source, start_position=0, end_position=1)

    assert type(exported) is ExtendedDetail
    assert exported.extra_field == "EXTRA_VALUE"


def test_sliced_export_hides_compaction_summary_like_full_export(serve):
    """With the markers lost, a sliced export showed the compaction summary."""
    from backend.models import ConversationDetail

    source = ConversationDetail(**_full_kwargs())
    full_md, _ = serve(source)
    sliced_md, _ = serve(source, start_position=0, end_position=2)

    assert COMPACT_SUMMARY_TEXT not in full_md
    assert "FIRST_MARKER" in sliced_md
    assert COMPACT_SUMMARY_TEXT not in sliced_md
