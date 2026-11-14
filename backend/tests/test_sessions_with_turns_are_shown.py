"""Every session that has a turn shows in the list, the viewer, and search.

Guiding principle 1 (AGENTS.md): the user may want to see and search any
session that had an effect on their work. A turn is a user message or an
assistant message, so a session with only an assistant turn still shows.
Only a session with no turns is hidden.

Before 2026-11-14 two readers hid a session that had no user turn:

* ``cowork_reader.read_cowork_conversation`` (rule D6) returned ``None``.
* ``cc_jsonl_io.read_conversation_summary_fast`` returned ``None``, so the
  list hid the session. The full reader still loaded it, so search found a
  session that the list did not show. A real archive had one such file.

The fixtures are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import search_index as si
from backend.store import ConversationStore

CC_UUID = "a0000001-0000-0000-0000-000000000001"
COWORK_UUID = "b0000001-0000-0000-0000-000000000001"


def _write_lines(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    cowork_root = tmp_path / "cowork"
    cowork_root.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    config.get_settings.cache_clear()
    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    index = si.SearchIndex(tmp_path / "index.sqlite")
    yield {"store": store, "index": index, "claude_dir": claude_dir,
           "cowork_root": cowork_root}
    index.close()
    config.get_settings.cache_clear()


def _cc_assistant_only(claude_dir: Path) -> Path:
    """A Claude Code session whose only turn is an assistant message, with
    a title row and other metadata rows around it."""
    path = claude_dir / "projects" / "-tmp-proj" / f"{CC_UUID}.jsonl"
    _write_lines(path, [
        {"type": "custom-title", "customTitle": "Assistant only", "sessionId": CC_UUID},
        {"type": "system", "subtype": "init", "uuid": "s1", "sessionId": CC_UUID,
         "timestamp": "2026-05-01T10:00:00Z", "cwd": "/tmp/proj", "gitBranch": "main"},
        {"type": "assistant", "uuid": "a1", "sessionId": CC_UUID,
         "timestamp": "2026-05-01T10:00:01Z", "cwd": "/tmp/proj", "gitBranch": "main",
         "message": {"role": "assistant", "id": "msg_a1", "model": "claude-sonnet-4-6",
                     "content": [{"type": "text", "text": "the platypus report"}]}},
    ])
    return path


def _cowork_assistant_only(cowork_root: Path) -> Path:
    session_dir = cowork_root / "d_dep" / "o_org" / f"local_{COWORK_UUID}"
    _write_lines(session_dir / "audit.jsonl", [
        {"type": "system", "subtype": "init", "session_id": COWORK_UUID, "uuid": "s1",
         "_audit_timestamp": "2026-05-25T10:00:00.000Z"},
        {"type": "assistant", "session_id": COWORK_UUID, "uuid": "a1",
         "parent_tool_use_id": None, "_audit_timestamp": "2026-05-25T10:00:05.000Z",
         "message": {"model": "claude-opus-4-7", "id": "msg_a1", "type": "message",
                     "role": "assistant",
                     "content": [{"type": "text", "text": "the axolotl report"}]}},
    ])
    session_dir.with_suffix(".json").write_text(json.dumps(
        {"sessionId": f"local_{COWORK_UUID}", "title": "Scheduled report"}
    ))
    return session_dir


# ----- Claude Code ----------------------------------------------------


def test_cc_session_with_only_an_assistant_turn_is_listed(env):
    _cc_assistant_only(env["claude_dir"])

    listed = env["store"].list_conversations(source="CLAUDE_CODE")

    assert [(c.uuid, c.name) for c in listed] == [(CC_UUID, "Assistant only")]
    assert listed[0].project_path == "/tmp/proj"


def test_cc_session_with_only_an_assistant_turn_opens_and_is_searchable(env):
    _cc_assistant_only(env["claude_dir"])
    si.build_full_index(env["store"], index=env["index"])

    detail = env["store"].get_conversation(CC_UUID)

    assert detail is not None
    assert "the platypus report" in json.dumps(detail.model_dump(mode="json"))
    assert {h["conv_uuid"] for h in env["index"].query("platypus")} == {CC_UUID}


def test_cc_session_with_no_turns_stays_hidden(env):
    path = env["claude_dir"] / "projects" / "-tmp-proj" / f"{CC_UUID}.jsonl"
    _write_lines(path, [
        {"type": "custom-title", "customTitle": "Only metadata", "sessionId": CC_UUID},
        {"type": "file-history-snapshot", "messageId": "m1", "snapshot": {}},
    ])

    assert env["store"].list_conversations(source="CLAUDE_CODE") == []


# ----- Cowork -----------------------------------------------------------


def test_cowork_session_with_only_an_assistant_turn_is_listed_and_opens(env):
    _cowork_assistant_only(env["cowork_root"])

    listed = env["store"].list_conversations(source="CLAUDE_COWORK")
    detail = env["store"].get_conversation(COWORK_UUID)

    assert [(c.uuid, c.name) for c in listed] == [(COWORK_UUID, "Scheduled report")]
    assert detail is not None
    assert "the axolotl report" in json.dumps(detail.model_dump(mode="json"))


def test_cowork_session_with_only_an_assistant_turn_is_searchable(env):
    _cowork_assistant_only(env["cowork_root"])

    si.build_full_index(env["store"], index=env["index"])

    assert {h["conv_uuid"] for h in env["index"].query("axolotl")} == {COWORK_UUID}
