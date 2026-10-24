"""Tests for the ConversationListItem / ConversationSummary split.

These tests pin the public contract of the
``/api/conversations`` list endpoint after PLANS/SPLIT_CONVERSATION_SCHEMA.md:

* The list endpoint serializes the SKINNY ``ConversationListItem`` shape:
  ``summary``, ``human_message_count``, and ``git_branch`` are stripped
  from each row to shrink the wire payload.
* The per-conversation endpoint (``/api/conversations/{uuid}``) still
  serializes the FULL ``ConversationDetail`` shape and continues to
  include ``git_branch`` (read by the detail-page Details disclosure).
* Server-side ``?search=`` still matches against ``summary`` on the
  in-memory ``ConversationSummary`` BEFORE the list-item projection runs,
  so search queries that target the summary body keep working.
* ``ConversationListItem`` is a strict subset of ``ConversationSummary``
  (property test: every field on the skinny model also lives on the
  fuller one).
* The serialized payload is meaningfully smaller than the full shape
  (regression guard against accidentally restoring the dropped fields).

See PLANS/SPLIT_CONVERSATION_SCHEMA.md "TDD test plan" for the full
specification this file is implementing.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


# Realistic per-conversation `summary` size (~400 chars). The Desktop
# auto-summary is typically a few sentences; using a long-ish string
# makes the payload-size guard (test 6) meaningful even on small
# fixture corpora.
_LONG_SUMMARY = (
    "This is a longer Desktop auto-summary intended to make the "
    "payload-size delta meaningful when this field is removed from the "
    "sidebar wire format. It contains a unique NEEDLE_TOKEN_FOR_SEARCH "
    "marker that the server-side search filter test relies on to prove "
    "the matcher still operates on the full ConversationSummary shape "
    "before the projection to ConversationListItem strips this field "
    "off. Padding to make the regression more visible: " + ("x " * 80)
)


def _write_desktop_conversation(
    data_dir: Path,
    *,
    uuid: str,
    name: str,
    summary: str = "",
    extra: dict | None = None,
) -> None:
    """Write a minimal Claude Desktop conversation JSON.

    Uses the on-disk shape consumed by ``backend/store.py``. UUIDs must
    match the ``^[0-9a-f-]{36}$`` filter or the file is skipped.
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
                "text": "Hi.",
                "content": [{"type": "text", "text": "Hi."}],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
            {
                "uuid": "msg-2",
                "sender": "assistant",
                "text": "Hello.",
                "content": [{"type": "text", "text": "Hello."}],
                "created_at": "2026-04-01T10:00:30Z",
                "updated_at": "2026-04-01T10:00:30Z",
                "parent_message_uuid": "msg-1",
            },
        ],
    }
    if extra:
        blob.update(extra)
    (data_dir / f"{uuid}.json").write_text(json.dumps(blob))


def _write_cc_session_with_git_branch(
    claude_dir: Path,
    *,
    session_uuid: str,
    git_branch: str = "feature/test-branch",
) -> None:
    """Write a minimal Claude Code JSONL session that carries a ``gitBranch``
    field on each line. The backend's ``claude_code_reader`` propagates
    this to ``ConversationSummary.git_branch``.
    """
    encoded = "-fixture-project"
    proj_dir = claude_dir / "projects" / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        {
            "cwd": "/fixture/project",
            "entrypoint": "cli",
            "gitBranch": git_branch,
            "isSidechain": False,
            "message": {"content": "Hi from CC fixture.", "role": "user"},
            "parentUuid": None,
            "sessionId": session_uuid,
            "timestamp": "2026-04-01T12:00:00Z",
            "type": "user",
            "userType": "external",
            "uuid": "11111111-2222-3333-4444-aaaaaaaaaaaa",
            "version": "2.0.0",
        },
        {
            "cwd": "/fixture/project",
            "entrypoint": "cli",
            "gitBranch": git_branch,
            "isSidechain": False,
            "message": {
                "content": [{"text": "Hello back.", "type": "text"}],
                "id": "asst-msg-id",
                "model": "claude-sonnet-4-6",
                "role": "assistant",
                "stop_reason": "end_turn",
                "type": "message",
                "usage": {
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "input_tokens": 10,
                    "output_tokens": 10,
                },
            },
            "parentUuid": "11111111-2222-3333-4444-aaaaaaaaaaaa",
            "sessionId": session_uuid,
            "timestamp": "2026-04-01T12:00:30Z",
            "type": "assistant",
            "userType": "external",
            "uuid": "22222222-3333-4444-5555-bbbbbbbbbbbb",
            "version": "2.0.0",
        },
    ]

    with (proj_dir / f"{session_uuid}.jsonl").open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_response_excludes_dropped_fields(isolated_data_dir):
    """1. ``/api/conversations`` rows MUST NOT contain ``summary``,
    ``human_message_count``, or ``git_branch``. These are the three
    fields the split moves off the list-item wire shape.
    """
    _write_desktop_conversation(
        isolated_data_dir,
        uuid="00000000-1111-2222-3333-000000000001",
        name="Convo A",
        summary=_LONG_SUMMARY,
    )

    with TestClient(app) as client:
        resp = client.get("/api/conversations")
    assert resp.status_code == 200, resp.text

    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    for row in rows:
        assert "summary" not in row, (
            f"summary leaked into list payload: {row!r}"
        )
        assert "human_message_count" not in row, (
            f"human_message_count leaked into list payload: {row!r}"
        )
        assert "git_branch" not in row, (
            f"git_branch leaked into list payload: {row!r}"
        )


def test_list_response_keeps_sidebar_required_fields(isolated_data_dir):
    """2. ``/api/conversations`` rows MUST keep every field the sidebar
    (and its client-side filter / sort) consumes. Mirrors the Phase 1
    frontend audit findings.
    """
    _write_desktop_conversation(
        isolated_data_dir,
        uuid="00000000-1111-2222-3333-000000000002",
        name="Convo B",
        summary=_LONG_SUMMARY,
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/conversations",
            params={"include_subagents": "true"},
        )
    assert resp.status_code == 200, resp.text

    rows = resp.json()
    assert len(rows) >= 1
    row = rows[0]

    required = {
        "uuid",
        "name",
        "model",
        "created_at",
        "updated_at",
        "is_starred",
        "message_count",
        "has_branches",
        "source",
        "project_path",
        "project_name",
        "organization_id",
        "organization_name",
        "subagents",
    }
    missing = required - set(row.keys())
    assert not missing, f"sidebar-required fields missing from list row: {missing}"


def test_per_conversation_response_still_includes_git_branch(
    isolated_data_dir,
):
    """3. ``GET /api/conversations/{uuid}`` MUST still serialize
    ``git_branch`` on Claude Code conversations that have one. The
    detail-page Details disclosure renders this field
    (``frontend/src/routes/ConversationPage.tsx``).

    This is a regression guard on existing behavior; should already pass.
    """
    session_uuid = "33333333-4444-5555-6666-cccccccccccc"
    claude_dir = isolated_data_dir.parent / "claude"
    _write_cc_session_with_git_branch(
        claude_dir,
        session_uuid=session_uuid,
        git_branch="feature/regression-guard",
    )

    with TestClient(app) as client:
        resp = client.get(f"/api/conversations/{session_uuid}")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "git_branch" in body, (
        f"git_branch must round-trip on the per-conversation endpoint; got keys {sorted(body.keys())}"
    )
    assert body["git_branch"] == "feature/regression-guard"


def test_server_side_search_still_matches_summary_field(isolated_data_dir):
    """4. ``?search=`` MUST still match against the in-memory ``summary``
    field even though the wire format drops it. The filter operates on
    the full ``ConversationSummary`` shape BEFORE the
    ``ConversationListItem`` projection runs in the router.

    This is a regression guard; should already pass.
    """
    # One conversation whose `summary` (but NOT `name`) carries the needle.
    _write_desktop_conversation(
        isolated_data_dir,
        uuid="00000000-1111-2222-3333-000000000003",
        name="Unrelated title",
        summary="contains NEEDLE_TOKEN_FOR_SEARCH inside the summary body",
    )
    # A second conversation that should NOT match — proves the filter
    # is selective, not a no-op.
    _write_desktop_conversation(
        isolated_data_dir,
        uuid="00000000-1111-2222-3333-000000000004",
        name="Another unrelated title",
        summary="completely different body",
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/conversations",
            params={"search": "NEEDLE_TOKEN_FOR_SEARCH"},
        )
    assert resp.status_code == 200, resp.text

    rows = resp.json()
    uuids = {r["uuid"] for r in rows}
    assert "00000000-1111-2222-3333-000000000003" in uuids, (
        f"summary-only search match must surface; rows={rows!r}"
    )
    assert "00000000-1111-2222-3333-000000000004" not in uuids, (
        f"non-matching row leaked; rows={rows!r}"
    )


def test_list_item_is_strict_subset_of_summary():
    """5. Property test: every field on ``ConversationListItem`` MUST
    also live on ``ConversationSummary``. This guards against a future
    field added to the skinny model that silently defaults because the
    source object doesn't expose it (PLANS/SPLIT_CONVERSATION_SCHEMA.md
    Risk 3).
    """
    from backend.models import ConversationListItem, ConversationSummary

    item_fields = set(ConversationListItem.model_fields)
    summary_fields = set(ConversationSummary.model_fields)
    extra = item_fields - summary_fields
    assert not extra, (
        "ConversationListItem must be a STRICT SUBSET of "
        f"ConversationSummary. Fields present on the skinny model but "
        f"missing from the source: {extra}"
    )


def test_pydantic_projection_is_loss_tolerant():
    """5b. Construct a fully-populated ``ConversationSummary`` and
    project it to ``ConversationListItem`` via
    ``model_validate(..., from_attributes=True)``. The projection must
    succeed, drop the three excluded fields, and preserve the rest.
    """
    from datetime import datetime

    from backend.models import ConversationListItem, ConversationSummary

    src = ConversationSummary(
        uuid="11111111-2222-3333-4444-555555555555",
        name="Projection test",
        summary="this should be dropped",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, 11, 0, 0, tzinfo=UTC),
        is_starred=True,
        message_count=42,
        human_message_count=21,
        has_branches=True,
        source="CLAUDE_CODE",
        project_path="/fixture/project",
        project_name=None,
        git_branch="main",
        organization_id="org-1",
        organization_name="Test Org",
    )

    item = ConversationListItem.model_validate(src, from_attributes=True)
    dumped = item.model_dump()

    # Dropped fields are absent.
    assert "summary" not in dumped
    assert "human_message_count" not in dumped
    assert "git_branch" not in dumped

    # Kept fields are preserved.
    assert dumped["uuid"] == src.uuid
    assert dumped["name"] == src.name
    assert dumped["model"] == src.model
    assert dumped["is_starred"] is True
    assert dumped["message_count"] == 42
    assert dumped["has_branches"] is True
    assert dumped["source"] == "CLAUDE_CODE"
    assert dumped["project_path"] == "/fixture/project"
    # `model_post_init` MUST have computed project_name from project_path
    # for the skinny model too.
    assert dumped["project_name"] == "project"
    assert dumped["organization_id"] == "org-1"
    assert dumped["organization_name"] == "Test Org"


def test_payload_size_smaller_than_full_summary_shape(isolated_data_dir):
    """6. Regression guard: serializing N rows as ``ConversationListItem``
    must be meaningfully smaller than serializing the same rows as the
    full ``ConversationSummary`` shape. Computes both sizes from the
    same store snapshot — no dependency on a hardcoded "before" number.
    """
    from backend.models import ConversationSummary
    from backend.store import ConversationStore

    # Plant 5 conversations with realistic-size summaries so the delta
    # is unambiguous.
    for i in range(5):
        _write_desktop_conversation(
            isolated_data_dir,
            uuid=f"00000000-1111-2222-3333-{i:012d}",
            name=f"Payload test {i}",
            summary=_LONG_SUMMARY,
        )

    # Compute the "before" size by re-serializing the same store data
    # with the full ConversationSummary shape — no router involvement,
    # so the delta is purely about the dropped fields.
    store = ConversationStore()
    full_rows = store.list_conversations()
    assert len(full_rows) >= 5
    before_bytes = sum(
        len(ConversationSummary.model_validate(r).model_dump_json())
        for r in full_rows
    )

    # Hit the live router to get the post-projection wire size.
    with TestClient(app) as client:
        resp = client.get("/api/conversations")
    assert resp.status_code == 200
    after_bytes = len(resp.content)

    # The skinny shape must be smaller; require at least a 10% delta as
    # a soft floor (the summary field alone clears this with realistic
    # data, but we don't want to false-alarm on tiny fixture corpora).
    delta = before_bytes - after_bytes
    assert after_bytes < before_bytes, (
        f"projection did not shrink payload: before={before_bytes}, after={after_bytes}"
    )
    ratio = delta / before_bytes
    assert ratio >= 0.10, (
        "expected at least a 10% payload-size reduction from dropping "
        f"summary/human_message_count/git_branch, got {ratio:.1%} "
        f"(before={before_bytes}, after={after_bytes})"
    )
