"""Files that the search index cannot load leave the drift backlog.

On 2026-11-13 the drift pass on a real archive listed 6 files as changed on
every pass and read each one again, because each one loads as "nothing":

* Four Claude Code session files of 0 bytes.
* Two remote Cowork tasks whose ``audit.jsonl`` holds only setup events
  (``env_manager_log``, ``system`` hooks, ``active_goal``): the task was
  opened, but no prompt was ever sent. The reader drops a session without a
  user turn (D6, ``backend/cowork_reader.py``), so the list, the viewer, and
  the index all skip it.

The contract pinned here:

* A file that cannot load is read once. Later passes skip it until its
  mtime or its size changes.
* It produces one log line, not one per pass.
* When it changes (a 0-byte session gets its first line), the next pass
  reads and indexes it.
* A read that fails because the file cannot be opened (for example, no
  permission) is not recorded: the next pass tries again.

Both drift entry points share the rule: ``build_full_index`` (startup) and
``update_drifted_files`` (the watcher).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from backend import search_index as si
from backend.store import ConversationStore
from fetcher.remote_sessions import RemoteCoworkFetcher
from fetcher.tests.ccr_fake import FakeSession, FakeSessionsApi, iso

ORG = "0f0f0f0f-1111-2222-3333-444444444444"


def _build(store, index):
    si.build_full_index(store, index=index)


def _update(store, index):
    si.update_drifted_files(store, index=index)


PASSES = pytest.mark.parametrize(
    "run_pass", [_build, _update], ids=["build_full_index", "update_drifted_files"]
)


def _cc_line(session_uuid: str, n: int, role: str, text: str) -> dict:
    entry = {
        "type": role,
        "uuid": f"{session_uuid}-{role[0]}{n}",
        "sessionId": session_uuid,
        "timestamp": f"2026-05-01T10:00:{n:02d}Z",
        "cwd": "/tmp/proj",
        "message": {"role": role, "content": text},
    }
    if role == "assistant":
        entry["message"] = {
            "role": "assistant",
            "id": f"msg_{session_uuid}_{n}",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": text}],
        }
    return entry


def _write_cc(path: Path, session_uuid: str, word: str) -> None:
    lines = [
        _cc_line(session_uuid, 1, "user", f"please find {word}"),
        _cc_line(session_uuid, 2, "assistant", f"found {word}"),
    ]
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))


def _setup_only_events() -> list[dict]:
    """The event shape of a remote task that was opened but never got a
    prompt: environment setup, hooks, an empty goal, and command lists.
    Content is made up; only the shape matches the real API."""

    def ev(seq: int, payload: dict) -> dict:
        return {
            "sequence_num": str(seq),
            "created_at": iso(seq),
            "event_type": payload["type"],
            "source": "worker",
            "payload": {"uuid": f"s-{seq}", **payload},
        }

    events = [
        ev(seq, {
            "type": "env_manager_log",
            "data": {
                "category": "boot",
                "content": f"setup step {seq}",
                "extra": {"step_id": f"step-{seq}", "step_status": "started"},
                "level": "info",
                "timestamp": iso(seq),
            },
        })
        for seq in range(1, 4)
    ]
    events += [
        ev(4, {"type": "system", "subtype": "hook_started", "session_id": "w",
               "hook_event": "SessionStart", "hook_id": "h1", "hook_name": "fake"}),
        ev(5, {"type": "system", "subtype": "hook_response", "session_id": "w",
               "hook_event": "SessionStart", "hook_id": "h1", "hook_name": "fake",
               "exit_code": 0, "outcome": "success", "output": "", "stderr": "",
               "stdout": ""}),
        ev(6, {"type": "active_goal", "session_id": "w", "value": None}),
        ev(7, {"type": "system", "subtype": "commands_changed", "session_id": "w",
               "commands": [{"name": "fake-command"}]}),
    ]
    return events


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend import config

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_dir = tmp_path / "claude"
    project = claude_dir / "projects" / "-tmp-proj"
    project.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    config.get_settings.cache_clear()

    loaded = project / "c0000001-0000-0000-0000-000000000001.jsonl"
    _write_cc(loaded, loaded.stem, "zebra")
    empty = project / "e0000001-0000-0000-0000-000000000001.jsonl"
    empty.write_bytes(b"")

    store = ConversationStore(data_dir=data_dir, claude_dir=claude_dir)
    index = si.SearchIndex(tmp_path / "index.sqlite")
    yield {"store": store, "index": index, "data_dir": data_dir,
           "loaded": loaded, "empty": empty}
    index.close()
    config.get_settings.cache_clear()


def _count_reads(monkeypatch) -> dict[Path, int]:
    reads: dict[Path, int] = {}
    real = si._load_conversation_at

    def counting(path, store, source=None):
        reads[path] = reads.get(path, 0) + 1
        return real(path, store, source=source)

    monkeypatch.setattr(si, "_load_conversation_at", counting)
    return reads


def _drifted_paths(env) -> set[Path]:
    drifted, _missing = si._drift_first_scan(env["store"], env["index"])
    return {p for p, _src in drifted}


# ----- 0-byte Claude Code files -------------------------------------


@PASSES
def test_zero_byte_session_leaves_the_backlog(env, run_pass):
    run_pass(env["store"], env["index"])

    assert _drifted_paths(env) == set()


@PASSES
def test_unloadable_file_is_not_read_again_on_each_pass(env, monkeypatch, run_pass):
    reads = _count_reads(monkeypatch)

    for _ in range(3):
        run_pass(env["store"], env["index"])

    assert reads.get(env["empty"]) == 1
    # The loadable file is read once too, then the mtime ledger skips it.
    assert reads.get(env["loaded"]) == 1


@PASSES
def test_unloadable_file_logs_one_line(env, caplog, run_pass):
    caplog.set_level(logging.INFO, logger="backend.search_index")

    for _ in range(3):
        run_pass(env["store"], env["index"])

    lines = [r for r in caplog.records if str(env["empty"]) in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    assert "cannot load" in lines[0].getMessage()


@PASSES
def test_unloadable_file_is_indexed_when_it_grows(env, run_pass):
    run_pass(env["store"], env["index"])
    before = env["empty"].stat().st_mtime

    _write_cc(env["empty"], env["empty"].stem, "quokka")
    # Keep the old mtime: a change of size alone must be enough.
    os.utime(env["empty"], (before, before))
    run_pass(env["store"], env["index"])

    hits = env["index"].query("quokka")
    assert {h["conv_uuid"] for h in hits} == {env["empty"].stem}
    assert _drifted_paths(env) == set()


@PASSES
def test_unloadable_file_is_read_again_when_only_its_mtime_changes(
    env, monkeypatch, run_pass
):
    run_pass(env["store"], env["index"])
    reads = _count_reads(monkeypatch)
    later = env["empty"].stat().st_mtime + 10
    os.utime(env["empty"], (later, later))

    run_pass(env["store"], env["index"])

    assert reads.get(env["empty"]) == 1


@PASSES
def test_a_new_file_at_the_same_path_is_read(env, monkeypatch, run_pass):
    """A deleted file's record goes with it: a new file at the same path,
    with the same size and mtime, is read again."""
    run_pass(env["store"], env["index"])
    mtime = env["empty"].stat().st_mtime
    env["empty"].unlink()
    run_pass(env["store"], env["index"])

    env["empty"].write_bytes(b"")
    os.utime(env["empty"], (mtime, mtime))
    reads = _count_reads(monkeypatch)
    run_pass(env["store"], env["index"])

    assert reads.get(env["empty"]) == 1


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 000 does not block reads on Windows or for root",
)
@PASSES
def test_a_file_that_cannot_be_opened_is_tried_again(env, monkeypatch, caplog, run_pass):
    """The readers turn an OSError into "no entries", the same result as an
    empty file. A file that cannot be opened is a transient state (a
    permission, a lock, too many open files), so it is not recorded."""
    caplog.set_level(logging.INFO, logger="backend.search_index")
    _write_cc(env["empty"], env["empty"].stem, "walrus")
    env["empty"].chmod(0)
    try:
        reads = _count_reads(monkeypatch)
        run_pass(env["store"], env["index"])
        run_pass(env["store"], env["index"])
        assert reads.get(env["empty"]) == 2
        lines = [r for r in caplog.records if str(env["empty"]) in r.getMessage()]
        assert len(lines) == 1, [r.getMessage() for r in lines]
    finally:
        env["empty"].chmod(0o644)

    run_pass(env["store"], env["index"])

    assert {h["conv_uuid"] for h in env["index"].query("walrus")} == {env["empty"].stem}


def test_clear_all_forgets_unloadable_files(env, monkeypatch):
    si.build_full_index(env["store"], index=env["index"])
    env["index"].clear_all()
    reads = _count_reads(monkeypatch)

    si.build_full_index(env["store"], index=env["index"])

    assert reads.get(env["empty"]) == 1


# ----- remote Cowork task without a prompt ----------------------------


@PASSES
def test_remote_task_without_a_prompt_leaves_the_backlog(env, monkeypatch, run_pass):
    task = FakeSession("cse_01NoPrompt", "Opened task", _setup_only_events())
    FakeSessionsApi({ORG: [task]}).install(monkeypatch)
    RemoteCoworkFetcher(lambda *a, **k: None, env["data_dir"], delay=0).sync_org(ORG)
    audit = env["data_dir"] / "cowork-remote" / ORG / "remote_cse_01NoPrompt" / "audit.jsonl"
    assert audit.stat().st_size > 0
    reads = _count_reads(monkeypatch)

    for _ in range(3):
        run_pass(env["store"], env["index"])

    assert reads.get(audit) == 1
    assert audit not in _drifted_paths(env)


def test_remote_task_without_a_prompt_stays_hidden(env, monkeypatch):
    """D6 holds for remote tasks: a task with no user turn has nothing to
    show, so the list and the viewer skip it, like a local Cowork session."""
    task = FakeSession("cse_01NoPrompt", "Opened task", _setup_only_events())
    FakeSessionsApi({ORG: [task]}).install(monkeypatch)
    RemoteCoworkFetcher(lambda *a, **k: None, env["data_dir"], delay=0).sync_org(ORG)

    listed = env["store"].list_conversations(source="CLAUDE_COWORK")

    assert listed == []
    assert env["store"].get_conversation("cse_01NoPrompt") is None
