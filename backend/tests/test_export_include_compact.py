"""Tests for the ``export.includeCompactContent`` preference gate.

V1 polish (2026-05-24). The ``include_compact`` flag controls whether
``/compact``-related artifacts (the trigger row + the
``isCompactSummary`` synthetic message) render verbatim in the
export, or are collapsed to a SINGLE-LINE indicator (the default).

Three artifacts per manual /compact:
  1. Trigger row — user message wrapping ``<command-name>/compact</command-name>``
     plus ``<command-args>{user_prompt}</command-args>``.
  2. ``isCompactSummary`` synthetic message — the LLM's compaction
     summary, whose UUID is what ``compact_marker.message_uuid``
     points to.
  3. The user's typed prompt — lives only inside the trigger row's
     ``<command-args>``.

An auto /compact produces ONLY artifact #2.

Bidirectional contracts pinned here:
  * OFF (default): indicator line appears; summary body + trigger row
    body do NOT appear.
  * ON: summary body + trigger row body DO appear; no indicator line.

Verified across the markdown, PDF (HTML source), and bundle exporters.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.export import (
    conversation_to_html,
    conversation_to_markdown,
    create_markdown_bundle,
)
from backend.exporters._shared import (
    _is_compact_summary_message,
    render_compact_indicator,
)
from backend.models import (
    CompactMarker,
    ContentBlock,
    ConversationDetail,
    Message,
)

SUMMARY_BODY_MANUAL = (
    "Manual compact summary covering the user's auth-module refactor and "
    "the build-phase context they wanted preserved."
)
SUMMARY_BODY_AUTO = (
    "Auto-compact summary: long-context truncation snapshot of the "
    "preceding conversation turns."
)
TRIGGER_TEXT = (
    "<command-message>compact</command-message>\n"
    "<command-name>/compact</command-name>\n"
    "<command-args>preserve A and refactor auth</command-args>"
)


def _msg(
    *,
    uuid: str,
    sender: str,
    text: str,
    is_command_marker: bool = False,
    slash_command: str | None = None,
) -> Message:
    return Message(
        uuid=uuid,
        sender=sender,  # type: ignore[arg-type]
        text=text,
        content=[ContentBlock(type="text", text=text)] if text else [],
        created_at=datetime(2026, 4, 1, 11, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, 11, 0, 0, tzinfo=UTC),
        is_command_marker=is_command_marker,
        slash_command=slash_command,
    )


def _conv_with_compacts() -> ConversationDetail:
    """A realistic mixed conversation with BOTH an auto /compact and a
    manual /compact (trigger + summary).

    Layout:
        u0  user      "Hello, Claude."
        a0  assistant "Hi there."
        u-auto  user (isCompactSummary)  -> auto marker
        u1  user      "More work."
        a1  assistant "On it."
        u-summary user (isCompactSummary) -> manual marker
        u-trigger user "<command-name>/compact</command-name>..."
        u2  user      "Continue."
        a2  assistant "OK."
    """
    messages = [
        _msg(uuid="u0", sender="human", text="Hello, Claude."),
        _msg(uuid="a0", sender="assistant", text="Hi there."),
        _msg(uuid="u-auto", sender="human", text=SUMMARY_BODY_AUTO),
        _msg(uuid="u1", sender="human", text="More work please."),
        _msg(uuid="a1", sender="assistant", text="On it."),
        _msg(uuid="u-summary", sender="human", text=SUMMARY_BODY_MANUAL),
        _msg(uuid="u-trigger", sender="human", text=TRIGGER_TEXT),
        _msg(uuid="u2", sender="human", text="Continue."),
        _msg(uuid="a2", sender="assistant", text="OK."),
    ]
    compact_markers = [
        CompactMarker(
            message_uuid="u-auto",
            summary_text=SUMMARY_BODY_AUTO,
            timestamp="2026-04-01T11:00:00Z",
            kind="auto",
            user_prompt=None,
        ),
        CompactMarker(
            message_uuid="u-summary",
            summary_text=SUMMARY_BODY_MANUAL,
            timestamp="2026-04-01T12:00:00Z",
            kind="manual",
            user_prompt="preserve A and refactor auth",
        ),
    ]
    return ConversationDetail(
        uuid="conv-1",
        name="Test conversation with /compact",
        summary="",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, 12, 1, 0, tzinfo=UTC),
        message_count=len(messages),
        human_message_count=sum(1 for m in messages if m.sender == "human"),
        source="CLAUDE_CODE",
        messages=messages,
        compact_markers=compact_markers,
    )


# ---------------------------------------------------------------------------
# Shared-helper unit tests
# ---------------------------------------------------------------------------


def test_is_compact_summary_message_true_for_marker_uuid() -> None:
    m = _msg(uuid="u-summary", sender="human", text=SUMMARY_BODY_MANUAL)
    assert _is_compact_summary_message(m, {"u-summary", "u-auto"}) is True


def test_is_compact_summary_message_false_for_non_marker() -> None:
    m = _msg(uuid="u0", sender="human", text="Hello.")
    assert _is_compact_summary_message(m, {"u-summary"}) is False


def test_render_compact_indicator_manual_includes_user_prompt() -> None:
    m = _msg(uuid="u-summary", sender="human", text=SUMMARY_BODY_MANUAL)
    by_uuid = {
        "u-summary": CompactMarker(
            message_uuid="u-summary",
            summary_text=SUMMARY_BODY_MANUAL,
            timestamp="2026-04-01T12:00:00Z",
            kind="manual",
            user_prompt="preserve A and refactor auth",
        )
    }
    out = render_compact_indicator(m, by_uuid)
    assert out is not None
    assert "Compacted" in out
    assert "manual" in out
    assert "preserve A and refactor auth" in out


def test_render_compact_indicator_auto_no_prompt() -> None:
    m = _msg(uuid="u-auto", sender="human", text=SUMMARY_BODY_AUTO)
    by_uuid = {
        "u-auto": CompactMarker(
            message_uuid="u-auto",
            summary_text=SUMMARY_BODY_AUTO,
            timestamp="2026-04-01T11:00:00Z",
            kind="auto",
            user_prompt=None,
        )
    }
    out = render_compact_indicator(m, by_uuid)
    assert out is not None
    assert "Compacted" in out
    assert "auto" in out
    # Auto markers carry no user prompt -> no "preserve" / "refactor" leak.
    assert "preserve" not in out
    assert "refactor" not in out


def test_render_compact_indicator_returns_none_for_non_marker() -> None:
    m = _msg(uuid="u0", sender="human", text="Hello.")
    assert render_compact_indicator(m, {}) is None


# ---------------------------------------------------------------------------
# Markdown export — bidirectional OFF/ON
# ---------------------------------------------------------------------------


def test_markdown_off_fully_hides_compactions_no_indicator() -> None:
    """V1 polish 2026-05-24 refinement (user-reported): when
    include_compact=False, fully HIDE the compaction. No indicator
    line, no `Compacted` text, no user_prompt leak. Matches the
    viewer's "Show Compactions" checkbox semantics — unchecked means
    invisible, not "summarized to a one-liner."

    Bidirectional pair with test_markdown_on_renders_summary_richly_
    and_drops_trigger_envelope below."""
    conv = _conv_with_compacts()
    out = conversation_to_markdown(conv, include_compact=False)

    # Nothing compact-related leaks.
    assert "Compacted" not in out
    assert "preserve A and refactor auth" not in out
    assert SUMMARY_BODY_MANUAL not in out
    assert SUMMARY_BODY_AUTO not in out
    assert "<command-name>/compact</command-name>" not in out
    assert "<command-args>" not in out

    # Surrounding real content is preserved (negative-space assertion:
    # the OFF state hides ONLY the compaction artifacts, not the
    # adjacent conversation).
    assert "Hello, Claude." in out
    assert "More work please." in out
    assert "Continue." in out


def test_markdown_on_renders_summary_richly_and_drops_trigger_envelope() -> None:
    """V1 polish 2026-05-24 (user-reported): when include_compact=True
    the LLM summary message must render with rich visual treatment
    (mirror of CompactMarker.tsx — `You asked:` + `Summary:` labels)
    rather than as a plain user message. The trigger row's
    `<command-name>/compact</command-name>` envelope is chrome the
    user doesn't want to see in EITHER state (the rich summary block
    already surfaces `user_prompt` as `You asked:`), so we drop the
    trigger row entirely in the ON state too."""
    conv = _conv_with_compacts()
    out = conversation_to_markdown(conv, include_compact=True)

    # Verbose summary bodies still present.
    assert SUMMARY_BODY_MANUAL in out
    assert SUMMARY_BODY_AUTO in out

    # Rich-block markers for the manual summary: `You asked:` label
    # carries the user_prompt; `Summary:` label introduces the LLM body.
    assert "**You asked:** preserve A and refactor auth" in out
    assert "**Summary:**" in out
    # The "Compacted (manual)" header is still present in the ON state
    # — it leads the rich block (mirror of CompactMarker.tsx's pill).
    assert "Compacted (manual)" in out

    # Trigger row's `<command-name>` envelope MUST be dropped in the
    # ON state (chrome the user doesn't want to see).
    assert "<command-name>/compact</command-name>" not in out
    assert "<command-args>" not in out


def test_markdown_default_is_include_compact_false() -> None:
    """Pref default is OFF — calling with no kwarg behaves like OFF
    (fully hidden, per 2026-05-24 refinement)."""
    conv = _conv_with_compacts()
    out = conversation_to_markdown(conv)
    assert "Compacted" not in out
    assert SUMMARY_BODY_MANUAL not in out
    assert "preserve A and refactor auth" not in out


# ---------------------------------------------------------------------------
# PDF export (HTML source) — bidirectional OFF/ON
# ---------------------------------------------------------------------------


def test_pdf_off_fully_hides_compactions_no_indicator() -> None:
    """PDF mirror of the markdown OFF-state contract (2026-05-24
    refinement)."""
    conv = _conv_with_compacts()
    out = conversation_to_html(conv, include_compact=False)

    assert "Compacted" not in out
    assert "preserve A and refactor auth" not in out
    assert SUMMARY_BODY_MANUAL not in out
    assert SUMMARY_BODY_AUTO not in out

    # Trigger envelope (raw + HTML-escaped) absent.
    assert "<command-name>/compact</command-name>" not in out
    assert "&lt;command-name&gt;/compact&lt;/command-name&gt;" not in out

    # Real surrounding content remains.
    assert "Hello, Claude." in out
    assert "Continue." in out


def test_pdf_compact_summary_css_uses_unified_purple_no_blue() -> None:
    """V1 polish 2026-05-24 user report: the PDF "You asked" sub-block
    used to be styled blue (color: #1d4ed8 / background: #eff6ff) which
    visually separated it from the purple "Summary" sub-block. User
    wants the prompt "to fit in with the formatting of the Summary"
    — both subsections should share the purple color family so they
    read as one unified compaction block.

    USER-OBSERVABLE CONTRACT pinned here:
      * The PDF inline CSS for `.compact-summary-asked` /
        `.compact-summary-asked-label` / `.compact-summary-asked-body`
        MUST NOT carry the blue hex values that previously caused the
        visual disjunction.
      * The asked-label MUST use the same purple-800 the Summary label
        uses (parallel structure).

    Implementation-coupled: we assert on hex strings because the
    "looks purple, not blue" claim is fundamentally about pixel color
    in the PDF; there's no DOM-structure proxy that captures it.
    """
    conv = _conv_with_compacts()
    out = conversation_to_html(conv, include_compact=True)

    # Negative-space: blue hexes from the old design MUST NOT appear.
    assert "#1d4ed8" not in out, (
        "blue-700 (asked label) must not appear — unify to purple"
    )
    assert "#eff6ff" not in out, (
        "blue-50 (asked block bg) must not appear — unify to purple"
    )
    assert "#1e3a8a" not in out, (
        "blue-900 (asked body) must not appear — unify to purple"
    )

    # Positive-space: the asked-label class is present (structure
    # preserved) AND it uses the same purple-800 as the body label.
    assert ".compact-summary-asked-label" in out
    assert ".compact-summary-body-label" in out
    # purple-800 (#6b21a8) appears at least twice — once for body
    # label, once for the unified asked label.
    assert out.count("#6b21a8") >= 2


def test_pdf_on_renders_summary_richly_and_drops_trigger_envelope() -> None:
    """V1 polish 2026-05-24 (user-reported): PDF mirror of the markdown
    rich-summary contract. The compact summary renders inside a
    distinguished `<div class="compact-summary">` block with
    `You asked` + `Summary` subsections (mirror of CompactMarker.tsx
    in the viewer), NOT as a plain `<div class="message human">`. The
    trigger row's `<command-name>` envelope is dropped in both
    states."""
    conv = _conv_with_compacts()
    out = conversation_to_html(conv, include_compact=True)

    # Summary bodies present.
    assert SUMMARY_BODY_MANUAL in out
    assert SUMMARY_BODY_AUTO in out

    # Rich-block CSS class anchors the visual treatment.
    assert 'class="compact-summary' in out

    # Rich labels match the viewer's CompactMarker.tsx structure:
    # "You asked" subsection (manual marker's user_prompt) +
    # "Summary" subsection (LLM body).
    assert "You asked" in out
    assert "Summary" in out
    # The manual marker's user_prompt is visible under "You asked".
    assert "preserve A and refactor auth" in out

    # Trigger row's `<command-name>` envelope MUST be dropped in BOTH
    # states (HTML-escaped form also absent).
    assert "<command-name>/compact</command-name>" not in out
    assert "&lt;command-name&gt;/compact&lt;/command-name&gt;" not in out

    # The "Compacted (manual)" header pill is present.
    assert "Compacted (manual)" in out


# ---------------------------------------------------------------------------
# Markdown bundle — bidirectional OFF/ON
# ---------------------------------------------------------------------------


def test_bundle_off_fully_hides_compactions_no_indicator(tmp_path) -> None:
    """Bundle mirror of the markdown OFF-state contract (2026-05-24
    refinement)."""
    conv = _conv_with_compacts()
    blob = create_markdown_bundle(
        conv,
        include_compact=False,
        image_cache_root=tmp_path,
    )
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        md = zf.read("conversation.md").decode("utf-8")

    assert "Compacted" not in md
    assert "preserve A and refactor auth" not in md
    assert SUMMARY_BODY_MANUAL not in md
    assert SUMMARY_BODY_AUTO not in md
    assert "<command-name>/compact</command-name>" not in md
    assert "Hello, Claude." in md
    assert "Continue." in md


def test_bundle_on_renders_summary_richly_and_drops_trigger_envelope(tmp_path) -> None:
    """Bundle mirror of the markdown rich-summary contract."""
    conv = _conv_with_compacts()
    blob = create_markdown_bundle(
        conv,
        include_compact=True,
        image_cache_root=tmp_path,
    )
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        md = zf.read("conversation.md").decode("utf-8")

    assert SUMMARY_BODY_MANUAL in md
    assert SUMMARY_BODY_AUTO in md
    assert "**You asked:** preserve A and refactor auth" in md
    assert "**Summary:**" in md
    assert "Compacted (manual)" in md

    # Trigger row envelope dropped in the ON state too.
    assert "<command-name>/compact</command-name>" not in md
    assert "<command-args>" not in md


# ---------------------------------------------------------------------------
# Route-level wiring — confirm the include_compact query param flows through
# ---------------------------------------------------------------------------


def test_route_export_markdown_threads_include_compact() -> None:
    """The /export/markdown route accepts include_compact and threads it
    to conversation_to_markdown."""
    from fastapi.testclient import TestClient

    from backend.main import app

    conv = _conv_with_compacts()

    class _StubStore:
        def get_conversation(self, uuid: str) -> ConversationDetail | None:
            return conv if uuid == conv.uuid else None

        def list_conversations(self) -> list:  # pragma: no cover - unused here
            return []

    from backend.deps import get_store

    app.dependency_overrides[get_store] = lambda: _StubStore()
    try:
        with TestClient(app) as client:
            # OFF (default + explicit) — fully hidden, no indicator
            # line (2026-05-24 user-reported refinement).
            r_off = client.get(
                f"/api/conversations/{conv.uuid}/export/markdown"
                "?include_compact=false"
            )
            assert r_off.status_code == 200
            body_off = r_off.text
            assert "Compacted" not in body_off
            assert SUMMARY_BODY_MANUAL not in body_off

            # ON: rich summary block, trigger envelope dropped.
            r_on = client.get(
                f"/api/conversations/{conv.uuid}/export/markdown"
                "?include_compact=true"
            )
            assert r_on.status_code == 200
            body_on = r_on.text
            assert SUMMARY_BODY_MANUAL in body_on
            # Rich block presence: `You asked:` + `Summary:` labels.
            assert "**You asked:** preserve A and refactor auth" in body_on
            assert "**Summary:**" in body_on
            # Trigger envelope MUST NOT leak (V1 polish 2026-05-24).
            assert "<command-name>/compact</command-name>" not in body_on
    finally:
        app.dependency_overrides.pop(get_store, None)


def test_route_export_markdown_bundle_threads_include_compact(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from backend.deps import get_store
    from backend.main import app

    conv = _conv_with_compacts()

    class _StubStore:
        def get_conversation(self, uuid: str) -> ConversationDetail | None:
            return conv if uuid == conv.uuid else None

    app.dependency_overrides[get_store] = lambda: _StubStore()
    try:
        with TestClient(app) as client:
            r_off = client.get(
                f"/api/conversations/{conv.uuid}/export/markdown-bundle"
                "?include_compact=false"
            )
            assert r_off.status_code == 200
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(r_off.content)) as zf:
                md_off = zf.read("conversation.md").decode("utf-8")
            # OFF state fully hides — no indicator line.
            assert "Compacted" not in md_off
            assert SUMMARY_BODY_MANUAL not in md_off

            r_on = client.get(
                f"/api/conversations/{conv.uuid}/export/markdown-bundle"
                "?include_compact=true"
            )
            assert r_on.status_code == 200
            with zipfile.ZipFile(io.BytesIO(r_on.content)) as zf:
                md_on = zf.read("conversation.md").decode("utf-8")
            assert SUMMARY_BODY_MANUAL in md_on
    finally:
        app.dependency_overrides.pop(get_store, None)


def test_route_export_all_markdown_threads_include_compact() -> None:
    from fastapi.testclient import TestClient

    from backend.deps import get_store
    from backend.main import app

    conv = _conv_with_compacts()

    class _StubStore:
        def get_conversation(self, uuid: str) -> ConversationDetail | None:
            return conv if uuid == conv.uuid else None

        def list_conversations(self) -> list:
            # Return a minimal summary-shaped object with `uuid`.
            class _Summary:
                pass

            s = _Summary()
            s.uuid = conv.uuid
            return [s]

    app.dependency_overrides[get_store] = lambda: _StubStore()
    try:
        with TestClient(app) as client:
            import io
            import zipfile

            r_off = client.get("/api/export/all/markdown?include_compact=false")
            assert r_off.status_code == 200
            with zipfile.ZipFile(io.BytesIO(r_off.content)) as zf:
                names = zf.namelist()
                # Pick the only conversation md.
                md_name = next(n for n in names if n.endswith(".md"))
                md_off = zf.read(md_name).decode("utf-8")
            # OFF state fully hides — no indicator.
            assert "Compacted" not in md_off
            assert SUMMARY_BODY_MANUAL not in md_off

            r_on = client.get("/api/export/all/markdown?include_compact=true")
            assert r_on.status_code == 200
            with zipfile.ZipFile(io.BytesIO(r_on.content)) as zf:
                md_name = next(n for n in zf.namelist() if n.endswith(".md"))
                md_on = zf.read(md_name).decode("utf-8")
            assert SUMMARY_BODY_MANUAL in md_on
    finally:
        app.dependency_overrides.pop(get_store, None)


@pytest.mark.parametrize("include_compact", [False, True])
def test_route_export_pdf_threads_include_compact(include_compact: bool) -> None:
    """PDF route accepts include_compact. We don't render WeasyPrint
    end-to-end here (slow + DYLD-sensitive); instead monkeypatch
    create_pdf to capture the kwarg flow."""
    from fastapi.testclient import TestClient

    from backend.deps import get_store
    from backend.main import app
    from backend.routers import export as export_router

    conv = _conv_with_compacts()

    captured: dict = {}

    def fake_create_pdf(c, include_tools=True, include_compact=True):
        captured["include_tools"] = include_tools
        captured["include_compact"] = include_compact
        return b"%PDF-fake"

    class _StubStore:
        def get_conversation(self, uuid: str) -> ConversationDetail | None:
            return conv if uuid == conv.uuid else None

    app.dependency_overrides[get_store] = lambda: _StubStore()
    orig = export_router.create_pdf
    export_router.create_pdf = fake_create_pdf
    try:
        with TestClient(app) as client:
            r = client.get(
                f"/api/conversations/{conv.uuid}/export/pdf"
                f"?include_compact={'true' if include_compact else 'false'}"
            )
            assert r.status_code == 200
            assert captured["include_compact"] is include_compact
    finally:
        export_router.create_pdf = orig
        app.dependency_overrides.pop(get_store, None)
