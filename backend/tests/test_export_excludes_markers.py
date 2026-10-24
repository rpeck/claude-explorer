"""V1 polish (2026-05-13, Fix 3) — Audit invariants #3, #4, #5:

Exports MUST mirror the viewer's hidden-by-default behavior for
slash-command chrome. The article promises "one truth, three surfaces":
viewer, search, AND export all agree on what counts as user-visible
content. Pre-fix, markdown bundle / inline markdown / PDF exports
included raw "Session: /exit" lines and leading-prelude markers — the
recipient saw chrome the user themselves did not.

Bidirectional contracts:
  * NEGATIVE (these tests): argless markers + prelude markers do NOT
    appear in markdown / PDF / bundle exports.
  * POSITIVE: real user messages, assistant replies, and ARGFUL slash
    commands (e.g. /coding <prose>) DO appear — argful markers have
    is_command_marker=False after Fix 2, so they pass through the
    excluder.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.export import (
    _is_excludable_marker,
    conversation_to_html,
    conversation_to_markdown,
)
from backend.models import ContentBlock, ConversationDetail, Message


def _msg(
    *,
    uuid: str,
    sender: str,
    text: str,
    is_command_marker: bool = False,
    is_prelude: bool = False,
    slash_command: str | None = None,
) -> Message:
    """Build a Message with default Desktop-compat values, overriding
    only the fields a given test cares about. Mirrors the public
    Pydantic model shape so the export pipeline sees real instances."""
    return Message(
        uuid=uuid,
        sender=sender,  # type: ignore[arg-type]
        text=text,
        content=[ContentBlock(type="text", text=text)] if text else [],
        created_at=datetime(2026, 4, 19, 1, 31, 14, tzinfo=UTC),
        updated_at=datetime(2026, 4, 19, 1, 31, 14, tzinfo=UTC),
        is_command_marker=is_command_marker,
        is_prelude=is_prelude,
        slash_command=slash_command,
    )


def _conv(messages: list[Message]) -> ConversationDetail:
    return ConversationDetail(
        uuid="conv-1",
        name="Test conversation",
        summary="",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 4, 19, 1, 31, 14, tzinfo=UTC),
        updated_at=datetime(2026, 4, 19, 1, 31, 14, tzinfo=UTC),
        message_count=len(messages),
        human_message_count=sum(1 for m in messages if m.sender == "human"),
        source="CLAUDE_CODE",
        messages=messages,
    )


# ----- _is_excludable_marker unit tests ------------------------------------


def test_excludable_marker_true_for_argless() -> None:
    """Argless /exit marker: excludable."""
    m = _msg(
        uuid="u1", sender="human", text="Session: /exit",
        is_command_marker=True, slash_command="/exit",
    )
    assert _is_excludable_marker(m) is True


def test_excludable_marker_true_for_prelude() -> None:
    """Leading prelude marker (always argless per invariant): excludable."""
    m = _msg(
        uuid="u1", sender="human", text="Session: /exit",
        is_command_marker=True, is_prelude=True, slash_command="/exit",
    )
    assert _is_excludable_marker(m) is True


def test_excludable_marker_false_for_argful() -> None:
    """Argful /coding marker (post-Fix-2: is_command_marker=False): NOT excludable."""
    m = _msg(
        uuid="u1", sender="human",
        text="Double-check your plan with the LLM council.",
        is_command_marker=False, slash_command="/coding",
    )
    assert _is_excludable_marker(m) is False


def test_excludable_marker_false_for_real_user_message() -> None:
    """Regular user message: NOT excludable."""
    m = _msg(uuid="u1", sender="human", text="Hello, Claude.")
    assert _is_excludable_marker(m) is False


def test_excludable_marker_false_for_assistant_reply() -> None:
    """Assistant reply: NOT excludable."""
    m = _msg(uuid="a1", sender="assistant", text="Hi there.")
    assert _is_excludable_marker(m) is False


# ----- conversation_to_markdown integration -------------------------------


def test_markdown_export_excludes_argless_command_markers() -> None:
    """The argless /exit marker (mid-conversation OR leading) must NOT
    appear in the Markdown export.

    Bidirectional inverse: the real user message AND the assistant reply
    DO appear — we're filtering chrome, not data.
    """
    conv = _conv([
        _msg(uuid="u0", sender="human", text="Hello, Claude.",
             is_command_marker=False),
        _msg(uuid="a0", sender="assistant", text="Hi there.",
             is_command_marker=False),
        _msg(uuid="m1", sender="human", text="Session: /exit",
             is_command_marker=True, slash_command="/exit"),
        _msg(uuid="u1", sender="human", text="Follow-up question.",
             is_command_marker=False),
    ])

    out = conversation_to_markdown(conv)
    # Negative direction: the marker text does NOT appear.
    assert "Session: /exit" not in out, (
        f"argless /exit marker must be excluded from Markdown export; "
        f"got export containing it:\n{out}"
    )
    # Positive direction: real content survives.
    assert "Hello, Claude." in out
    assert "Hi there." in out
    assert "Follow-up question." in out


def test_markdown_export_excludes_prelude_messages() -> None:
    """Leading-prelude markers must NOT appear in the Markdown export.

    Mirrors the viewer's SessionPreludeAffordance behavior (hidden by
    default). Spec invariant X8.
    """
    conv = _conv([
        _msg(uuid="m1", sender="human", text="Session: /exit",
             is_command_marker=True, is_prelude=True, slash_command="/exit"),
        _msg(uuid="m2", sender="human", text="Session: /clear",
             is_command_marker=True, is_prelude=True, slash_command="/clear"),
        _msg(uuid="u1", sender="human", text="Real first prompt.",
             is_command_marker=False),
    ])

    out = conversation_to_markdown(conv)
    assert "Session: /exit" not in out
    assert "Session: /clear" not in out
    assert "Real first prompt." in out


def test_markdown_export_includes_argful_slash_commands() -> None:
    """Bidirectional positive: argful slash commands (`/coding <prose>`)
    carry real user content and MUST be included in the export.

    Without this guardrail, Fix 3 would risk hiding the user's prompts
    along with the chrome.
    """
    conv = _conv([
        _msg(uuid="u1", sender="human",
             text="Double-check your plan with the LLM council.",
             is_command_marker=False, slash_command="/coding"),
        _msg(uuid="a1", sender="assistant", text="On it.",
             is_command_marker=False),
    ])

    out = conversation_to_markdown(conv)
    # The user's prose body MUST appear.
    assert "Double-check your plan with the LLM council." in out
    # And the assistant's reply.
    assert "On it." in out


# ----- conversation_to_html (PDF source) integration -----------------------


def test_pdf_export_excludes_argless_command_markers() -> None:
    """The PDF path (HTML + WeasyPrint) must also exclude argless markers.

    `conversation_to_html` is the source of `conversation_to_pdf`; this
    pins the HTML-level invariant which is cheaper to test than rendering
    a full PDF.
    """
    conv = _conv([
        _msg(uuid="u0", sender="human", text="Hello, Claude.",
             is_command_marker=False),
        _msg(uuid="m1", sender="human", text="Session: /exit",
             is_command_marker=True, slash_command="/exit"),
        _msg(uuid="u1", sender="human", text="Real follow-up.",
             is_command_marker=False),
    ])

    out = conversation_to_html(conv)
    assert "Session: /exit" not in out, (
        "argless /exit marker must be excluded from HTML/PDF export; "
        "got HTML containing it"
    )
    assert "Hello, Claude." in out
    assert "Real follow-up." in out


def test_pdf_export_excludes_prelude_messages() -> None:
    """Bidirectional PDF version of the markdown-prelude-exclusion test."""
    conv = _conv([
        _msg(uuid="m1", sender="human", text="Session: /exit",
             is_command_marker=True, is_prelude=True, slash_command="/exit"),
        _msg(uuid="u1", sender="human", text="Real first prompt.",
             is_command_marker=False),
    ])

    out = conversation_to_html(conv)
    assert "Session: /exit" not in out
    assert "Real first prompt." in out


def test_pdf_export_includes_argful_slash_commands() -> None:
    """Bidirectional positive: argful /coding survives the PDF path too."""
    conv = _conv([
        _msg(uuid="u1", sender="human",
             text="Outline the migration steps.",
             is_command_marker=False, slash_command="/plan"),
        _msg(uuid="a1", sender="assistant", text="Here's a plan.",
             is_command_marker=False),
    ])

    out = conversation_to_html(conv)
    assert "Outline the migration steps." in out
    assert "Here's a plan." in out
