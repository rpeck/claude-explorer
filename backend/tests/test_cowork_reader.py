"""Spec for the Cowork local-session reader.

Pins the user-observable contract of ``backend.cowork_reader``:
turn a real-shape ``audit.jsonl`` + sidecar ``local_<uuid>.json`` pair
into a conversation dict the rest of the stack can render and search.

Verified against the live shape recorded in
``PLANS/2026.05.24-SUPPORT-COWORK-SESSIONS.md`` Phase 0a (run on the
maintainer's Mac) — these fixtures match field-for-field. NEVER commit
real user data here; synthetic only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path



FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cowork"
HAPPY_DEPLOYMENT = FIXTURE_ROOT / "d_deployment1"
HAPPY_ORG = HAPPY_DEPLOYMENT / "o_org1"
HAPPY_SESSION_DIR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777"
HAPPY_SIDECAR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777.json"


def test_read_cowork_conversation_returns_canonical_dict():
    """Happy path: parse the synthetic fixture and assert the canonical
    conversation-dict shape the rest of the stack consumes.
    """
    from backend.cowork_reader import read_cowork_conversation

    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    # Cowork uuid strips the "local_" prefix so it matches the directory
    # stem AND the user-visible URL slug.
    assert conv["uuid"] == "aaaa1111-2222-3333-4444-555566667777"
    assert conv["source"] == "CLAUDE_COWORK"
    assert conv["name"] == "Synthetic Cowork Fixture One"
    assert conv["model"] == "claude-opus-4-7"
    # Two user + two assistant messages survive grouping.
    assert len(conv["chat_messages"]) == 4
    senders = [m["sender"] for m in conv["chat_messages"]]
    assert senders == ["human", "assistant", "human", "assistant"]


def test_audit_hmac_stripped_from_all_messages():
    """D4: ``_audit_hmac`` must never leak into the rendered message
    payload (we don't verify it and shipping it would expose Desktop's
    audit secret to anyone running the web UI).
    """
    from backend.cowork_reader import read_cowork_conversation

    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    for msg in conv["chat_messages"]:
        # Walk the full content tree.
        assert "_audit_hmac" not in json.dumps(msg)


def test_audit_timestamp_mapped_to_timestamp():
    """``_audit_timestamp`` must become ``created_at`` on the merged
    message (otherwise every message falls back to ``datetime.now``,
    which would jumble chronological order).
    """
    from backend.cowork_reader import read_cowork_conversation

    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    first = conv["chat_messages"][0]
    # The first user line carries _audit_timestamp 2026-05-25T10:00:00.000Z.
    assert first["created_at"].startswith("2026-05-25T10:00:00")


def test_thinking_block_round_trips():
    """``thinking`` blocks survive the merge pipeline (frontend drops
    them at render — see ContentBlockRenderer default branch — but
    they must NOT be silently stripped here)."""
    from backend.cowork_reader import read_cowork_conversation

    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    # Second assistant has a thinking block.
    second_assistant = [m for m in conv["chat_messages"] if m["sender"] == "assistant"][1]
    block_types = [b.get("type") for b in second_assistant["content"]]
    assert "thinking" in block_types


def test_missing_sidecar_falls_back_to_untitled(tmp_path: Path):
    """Reader must tolerate a missing sidecar (manual restore, ongoing
    write, etc.) — title falls back to "Untitled" and the audit.jsonl
    content still parses.
    """
    from backend.cowork_reader import read_cowork_conversation

    # Copy ONLY the audit.jsonl into a tmp session dir with no sidecar.
    session_dir = tmp_path / "local_bbbb2222-2222-3333-4444-555566667777"
    session_dir.mkdir()
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", session_dir / "audit.jsonl")
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert conv["name"] == "Untitled"
    assert conv["model"] == ""
    assert len(conv["chat_messages"]) == 4


def test_empty_audit_returns_none(tmp_path: Path):
    """D6: a session with no user record (e.g. interrupted before the
    first user turn) must NOT surface in the sidebar."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_empty"
    session_dir.mkdir()
    (session_dir / "audit.jsonl").write_text("")
    conv = read_cowork_conversation(session_dir)
    assert conv is None


def test_no_user_record_returns_none(tmp_path: Path):
    """A session with only system/init lines and no user turn must be
    dropped (D6) — there's nothing to render."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_sysonly"
    session_dir.mkdir()
    (session_dir / "audit.jsonl").write_text(
        '{"type":"system","subtype":"init","cwd":"/sessions/x","session_id":"sysonly","tools":[],"model":"m","uuid":"s1","_audit_timestamp":"2026-05-25T10:00:00.000Z","_audit_hmac":"a"}\n'
    )
    conv = read_cowork_conversation(session_dir)
    assert conv is None


def test_partial_last_line_tolerated(tmp_path: Path):
    """A torn append (writer killed mid-flush, no trailing newline,
    invalid JSON on the last line) must not lose the prior valid
    lines."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_partial"
    session_dir.mkdir()
    base = (HAPPY_SESSION_DIR / "audit.jsonl").read_text()
    # Append a torn line with no newline.
    (session_dir / "audit.jsonl").write_text(base + '{"type":"user","uuid":"torn","sessio')
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    # Same 4 well-formed messages as the happy path; torn line dropped.
    assert len(conv["chat_messages"]) == 4


def test_unknown_type_does_not_crash():
    """A line with a future/unknown ``type`` (e.g. ``future_unknown_type``)
    must be silently skipped at message-grouping, not crash."""
    from backend.cowork_reader import read_cowork_conversation

    # The happy fixture INCLUDES a ``future_unknown_type`` line.
    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    senders = [m["sender"] for m in conv["chat_messages"]]
    assert "future_unknown_type" not in senders


def test_truncation_of_oversized_string_payload(tmp_path: Path):
    """D11: any single content payload over 1 MB is truncated with a
    ``[truncated; N bytes]`` marker so the SSE stream + frontend don't
    OOM on a giant tool_result paste."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_big"
    session_dir.mkdir()
    big = "X" * (2 * 1024 * 1024)  # 2 MB
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u-big",
            "session_id": "bigsess",
            "parent_tool_use_id": None,
            "message": {"role": "user", "content": big},
            "_audit_timestamp": "2026-05-25T10:00:00.000Z",
            "_audit_hmac": "x",
        }
    )
    (session_dir / "audit.jsonl").write_text(line + "\n")
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    text = conv["chat_messages"][0]["text"]
    assert "[truncated;" in text
    # Truncated payload itself fits inside the 1 MB limit + the small
    # marker tail — well under the original 2 MB.
    assert len(text.encode("utf-8")) < 1_200_000


def test_list_cowork_conversations_walks_deployment_and_org():
    """``list_cowork_conversations`` flattens the deployment+org layer
    (D2) — caller receives one flat list of conversation dicts, with
    no deployment/org keys leaking through."""
    from backend.cowork_reader import list_cowork_conversations

    convs = list_cowork_conversations(FIXTURE_ROOT)
    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "CLAUDE_COWORK"
    assert "deployment" not in conv
    assert "organization_id" not in conv or conv["organization_id"] is None


def test_list_cowork_conversations_missing_root_is_empty(tmp_path: Path):
    """A user without Cowork data installed must get an empty list,
    not a crash, when the cowork root doesn't exist."""
    from backend.cowork_reader import list_cowork_conversations

    missing = tmp_path / "no_such_root"
    assert list_cowork_conversations(missing) == []


def test_archived_flag_surfaces_on_conv(tmp_path: Path):
    """D8: ``is_archived`` from the sidecar must surface on the conv
    dict so the store layer can filter without re-reading the sidecar."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_archived"
    session_dir.mkdir()
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", session_dir / "audit.jsonl")
    sidecar = json.loads(HAPPY_SIDECAR.read_text())
    sidecar["isArchived"] = True
    (tmp_path / "local_archived.json").write_text(json.dumps(sidecar))
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert conv["is_archived"] is True


def test_error_field_surfaces_on_conv_when_present(tmp_path: Path):
    """D9: ``error`` from the sidecar surfaces on the conv dict so the
    detail view can render a banner."""
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_errored"
    session_dir.mkdir()
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", session_dir / "audit.jsonl")
    sidecar = json.loads(HAPPY_SIDECAR.read_text())
    sidecar["error"] = "The session ended unexpectedly."
    (tmp_path / "local_errored.json").write_text(json.dumps(sidecar))
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    assert conv["error"] == "The session ended unexpectedly."


def test_sandbox_path_surfaces(tmp_path: Path):
    """D10: ``cwd`` (the sandbox path like ``/sessions/<vm>``) is
    surfaced as ``sandbox_path`` so the detail view can render it as
    plain text labeled "Sandbox path"."""
    from backend.cowork_reader import read_cowork_conversation

    conv = read_cowork_conversation(HAPPY_SESSION_DIR)
    assert conv is not None
    assert conv["sandbox_path"] == "/sessions/synthetic-sandbox"
