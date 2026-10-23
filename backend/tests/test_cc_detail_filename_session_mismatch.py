"""Regression: ``GET /api/conversations/{uuid}`` must find a CC session
whose internal ``sessionId`` differs from its filename stem.

Reproduces the bug surfaced by `/code-audit` on the live corpus:

    * File on disk: ``124418da-….jsonl`` (filename stem ``124418da-…``)
    * First user entry's ``sessionId``: ``816c6dbf-…``
    * Sidebar LIST endpoint reported ``uuid=816c6dbf-…`` (correctly,
      since ``read_conversation_summary_fast`` prefers ``sessionId``
      over ``jsonl_path.stem``).
    * User clicked the sidebar row → frontend fetched
      ``/api/conversations/816c6dbf-…`` → backend's
      ``_find_conversation_data`` only matched by filename stem AND
      internal uuid (both must match) → File 124418da-…'s
      filename stem 124418da didn't match the requested uuid 816c6dbf
      → fell through → 404 "Conversation not found" in the UI.

Bidirectional pair:

  * `test_detail_uses_the_file_name_not_the_internal_sessionid` — since
    2026-10-21 the file name is the uuid (see that test's docstring).

  * `test_detail_404s_when_no_file_has_matching_sessionid` — the
    fallback must NOT silently return some other conversation. A
    bogus UUID still 404s.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.cache import _conversation_cache
from backend import config as cfg
from backend.main import app


def _cc_jsonl_lines(session_id: str, name: str) -> list[dict]:
    """Build minimal CC JSONL entries with the given internal sessionId."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "type": "user",
            "uuid": "msg-1",
            "parentUuid": None,
            "sessionId": session_id,
            "cwd": "/tmp",
            "gitBranch": "",
            "version": "test",
            "timestamp": now,
            "message": {"role": "user", "content": name},
        },
        {
            "type": "assistant",
            "uuid": "msg-2",
            "parentUuid": "msg-1",
            "sessionId": session_id,
            "timestamp": now,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "test response"}],
            },
        },
    ]


@pytest.fixture
def cc_mismatch_data_dir(tmp_path, monkeypatch):
    """Seed a CC project with a session whose filename stem ≠ internal
    sessionId, plus a sibling whose stem DOES match.
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    project_dir = claude_dir / "projects" / "-test-project"
    project_dir.mkdir(parents=True)

    # Mismatch file — the bug case from the live corpus.
    # Filename stem: filename-stem-uuid. Internal sessionId: real-sessionid-uuid.
    mismatch_session_id = "real-sessionid-uuid-aaaa-aaaaaaaaaaaa"
    mismatch_path = project_dir / "filename-stem-uuid-bbbb-bbbbbbbbbbbb.jsonl"
    mismatch_path.write_text(
        "\n".join(
            json.dumps(e) for e in _cc_jsonl_lines(mismatch_session_id, "Mismatched session")
        )
    )

    # Control file: filename and sessionId match (the simple case).
    matched_session_id = "matched-session-uuid-cccc-cccccccccccc"
    matched_path = project_dir / "matched-session-uuid-cccc-cccccccccccc.jsonl"
    matched_path.write_text(
        "\n".join(
            json.dumps(e) for e in _cc_jsonl_lines(matched_session_id, "Matched session")
        )
    )

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()
    yield {
        "mismatch_session_id": mismatch_session_id,
        "mismatch_path": mismatch_path,
        "matched_session_id": matched_session_id,
        "matched_path": matched_path,
    }
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_detail_uses_the_file_name_not_the_internal_sessionid(cc_mismatch_data_dir):
    """Since 2026-10-21 a Claude Code conversation's uuid is its file name.

    The live case behind this file (``124418da-….jsonl`` whose first user
    row carries ``816c6dbf-…``) is a continued session: 8,778 of its rows
    carry ``124418da``, and only 6 rows were copied from ``816c6dbf``,
    which has its own file. Naming the file by the copied row hid one of
    the two sessions. Now the list and the detail view both use the file
    name, and the copied row's id opens nothing.
    """
    client = TestClient(app)
    stem = cc_mismatch_data_dir["mismatch_path"].stem
    r = client.get(f"/api/conversations/{stem}")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["uuid"] == stem
    assert body["name"] == "Mismatched session"

    internal = cc_mismatch_data_dir["mismatch_session_id"]
    assert client.get(f"/api/conversations/{internal}").status_code == 404


def test_detail_404s_when_no_file_has_matching_sessionid(cc_mismatch_data_dir):
    """Bidirectional pair: the new fallback scan must NOT silently
    return some other conversation when no file matches. A genuine
    miss still 404s.
    """
    client = TestClient(app)
    r = client.get("/api/conversations/this-uuid-exists-nowhere-on-disk-zzzz")
    assert r.status_code == 404


def test_detail_finds_session_by_filename_when_filename_matches(cc_mismatch_data_dir):
    """The fast path still works for the simple case: filename stem
    matches internal sessionId. Regression guard so the fix didn't
    accidentally change the happy path.
    """
    client = TestClient(app)
    req_uuid = cc_mismatch_data_dir["matched_session_id"]
    r = client.get(f"/api/conversations/{req_uuid}")
    assert r.status_code == 200
    body = r.json()
    assert body["uuid"] == req_uuid
    assert body["name"] == "Matched session"


def test_list_and_detail_uuids_agree(cc_mismatch_data_dir):
    """The fundamental contract that was being violated: every UUID
    that appears in ``/api/conversations`` (sidebar list) MUST be
    fetchable via ``/api/conversations/{uuid}``. If LIST advertises
    a UUID, DETAIL must honor it.
    """
    client = TestClient(app)
    list_resp = client.get("/api/conversations")
    assert list_resp.status_code == 200
    uuids = [c["uuid"] for c in list_resp.json()]
    assert len(uuids) >= 2, f"fixture should seed 2 sessions; got {len(uuids)}"

    for u in uuids:
        detail = client.get(f"/api/conversations/{u}")
        assert detail.status_code == 200, (
            f"LIST advertised uuid={u} but DETAIL returned {detail.status_code}. "
            f"Contract violation: sidebar shows a row that doesn't open."
        )
