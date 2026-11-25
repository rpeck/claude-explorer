"""Issue #90: ``scripts/scrub_fixture.py`` makes committed fixtures from real recordings.

A recording of a synthetic Claude Code or Codex session still holds
machine data: the home path, the scratch folder, account ids, the system
prompt, skill and MCP lists, and request records. The scrubber keeps the
records that Explorer reads, with their field names and enum values, and
drops or rewrites the rest. It refuses to write a file that still holds a
forbidden pattern.

Every test plants a "secret" and checks that it is gone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scrub_fixture.py"
SCRATCH = "/private/tmp/claude-501/-Users-alice-proj/abc/scratchpad/cx-sample"


def _run(tmp_path: Path, kind: str, records: list[dict[str, Any]], *extra: str) -> subprocess.CompletedProcess[str]:
    src = tmp_path / "in.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in records))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--kind", kind, "--in", str(src), "--out", str(tmp_path / "out.jsonl"),
         "--home", "/Users/alice", "--scratch", SCRATCH, *extra],
        capture_output=True, text=True, check=False,
    )


def _out(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]


CODEX = [
    {"timestamp": "t0", "type": "session_meta", "payload": {
        "id": "th-1", "timestamp": "t0", "cwd": SCRATCH, "history_mode": "paginated", "cli_version": "0.164.0",
        "originator": "codex_exec", "base_instructions": {"text": "SECRETPROMPT"},
        "creator_account_id": "acct-SECRET", "creator_user_id": "user-SECRET"}},
    {"timestamp": "t1", "type": "turn_context", "payload": {
        "turn_id": "turn-1", "cwd": SCRATCH, "model": "gpt-x", "user_instructions": "SECRETINSTR"}},
    {"timestamp": "t1", "type": "world_state", "payload": {"state": {"skills": "/Users/alice/.codex/skills SECRETSKILL"}}},
    {"timestamp": "t1", "type": "response_item", "payload": {"type": "additional_tools", "tools": [{"name": "SECRETTOOL"}]}},
    {"timestamp": "t1", "type": "response_item", "payload": {
        "type": "message", "role": "developer", "content": [{"type": "input_text", "text": "SECRETDEV"}]}},
    {"timestamp": "t2", "type": "response_item", "payload": {
        "type": "custom_tool_call", "call_id": "call_1", "name": "exec",
        "input": f'await tools.exec_command({{"cmd":"ls src","workdir":"{SCRATCH}"}});',
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
    {"timestamp": "t3", "type": "event_msg", "payload": {"type": "item_completed", "thread_id": "th-1", "turn_id": "turn-1",
        "item": {"type": "CommandExecution", "id": "exec-1", "command": ["/bin/zsh", "-lc", "ls src"],
                 "cwd": f"file://{SCRATCH}", "aggregated_output": "app.py\n", "process_id": "4242"}}},
    {"timestamp": "t3", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {"plan_type": "SECRETPLAN"}}},
    {"timestamp": "t3", "type": "token_usage_record", "payload": {"response_id": "resp-SECRET"}},
]

CC = [
    {"type": "session-owner", "accountUuid": "acct-SECRET", "organizationUuid": "org-SECRET", "sessionId": "s-1"},
    {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": "s-1", "cwd": SCRATCH, "timestamp": "t0",
     "message": {"role": "user", "content": "List the files."}},
    {"type": "attachment", "uuid": "x1", "parentUuid": "u1", "attachment": {"type": "prompt_snapshot", "systemPrompt": ["SECRETSYS"]}},
    {"type": "attachment", "uuid": "x2", "parentUuid": "u1", "attachment": {"type": "skill_listing", "content": "SECRETSKILL"}},
    {"type": "api-request", "params": {"system": "SECRETSYS"}},
    {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "sessionId": "s-1", "cwd": SCRATCH, "timestamp": "t1",
     "serverClassifierContext": {"git_state": {"cwd": SCRATCH}}, "requestRef": "req-SECRET",
     "message": {"id": "msg_1", "role": "assistant", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1},
                 "content": [{"type": "thinking", "thinking": "SECRETTHINK", "signature": "SIG"},
                             {"type": "tool_use", "id": "toolu_1", "name": "REPL", "input": {"code": "await $`ls src`"}}]}},
    {"type": "user", "uuid": "v1", "parentUuid": "a1", "sessionId": "s-1", "isVirtual": True, "timestamp": "t2",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_cc_1", "content": ""}]},
     "hostContent": [{"type": "tool_result", "tool_use_id": "toolu_cc_1", "content": f"{SCRATCH}/src/app.py"}]},
    {"type": "attachment", "uuid": "p1", "parentUuid": "v1", "attachment": {"type": "poll_events", "envelopes": ["<event eval=\"1\"/>"]}},
]


def test_codex_keeps_what_the_reader_needs_and_drops_the_rest(tmp_path: Path) -> None:
    r = _run(tmp_path, "codex", CODEX)
    assert r.returncode == 0, r.stderr
    out = _out(tmp_path)
    text = json.dumps(out)
    for secret in ("SECRET", "/Users/alice", "/private/", "alice"):
        assert secret not in text, secret
    assert [x["type"] for x in out] == ["session_meta", "turn_context", "response_item", "event_msg"]
    meta = out[0]["payload"]
    assert meta["id"] == "th-1" and meta["history_mode"] == "paginated" and meta["cli_version"] == "0.164.0"
    assert meta["cwd"] == "/home/fixture/cx-sample"
    assert out[1]["payload"] == {"turn_id": "turn-1", "cwd": "/home/fixture/cx-sample", "model": "gpt-x"}
    call = out[2]["payload"]
    assert "internal_chat_message_metadata_passthrough" not in call
    assert "/home/fixture/cx-sample" in call["input"] and "ls src" in call["input"]
    item = out[3]["payload"]["item"]
    assert item["cwd"] == "file:///home/fixture/cx-sample" and item["aggregated_output"] == "app.py\n"


def test_cc_keeps_the_conversation_records_and_drops_the_context(tmp_path: Path) -> None:
    r = _run(tmp_path, "cc", CC)
    assert r.returncode == 0, r.stderr
    out = _out(tmp_path)
    text = json.dumps(out)
    for secret in ("SECRET", "SIG", "/Users/alice", "/private/", "alice"):
        assert secret not in text, secret
    assert [x["type"] for x in out] == ["user", "assistant", "user", "attachment"]
    a1 = out[1]
    assert "serverClassifierContext" not in a1 and "requestRef" not in a1 and "usage" not in a1["message"]
    assert a1["message"]["content"][0] == {"type": "thinking", "thinking": "..."}
    assert a1["message"]["content"][1]["input"] == {"code": "await $`ls src`"}
    assert out[2]["hostContent"][0]["content"] == "/home/fixture/cx-sample/src/app.py"
    assert out[3]["attachment"]["type"] == "poll_events"


def test_it_refuses_to_write_a_file_that_still_holds_a_home_path(tmp_path: Path) -> None:
    leak = [{"type": "user", "uuid": "u", "message": {"role": "user", "content": "see /Users/bob/notes.txt"}}]
    r = _run(tmp_path, "cc", leak)
    assert r.returncode != 0
    assert "/Users/" in r.stderr
    assert not (tmp_path / "out.jsonl").exists()


def test_it_refuses_an_email_address(tmp_path: Path) -> None:
    leak = [{"type": "user", "uuid": "u", "message": {"role": "user", "content": "mail carol@example.org"}}]
    r = _run(tmp_path, "cc", leak)
    assert r.returncode != 0
    assert not (tmp_path / "out.jsonl").exists()


def test_a_tool_search_result_keeps_only_the_tools_that_the_session_calls(tmp_path: Path) -> None:
    records = [
        {"type": "user", "uuid": "r1", "isVirtual": True, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "repl_1", "content": ""}]},
         "toolUseResult": [{"name": "mcp__fetch__fetch", "about": "Fetches a URL."},
                           {"name": "mcp__SECRETSERVICE__tool", "about": "SECRETABOUT"}]},
        {"type": "assistant", "uuid": "a2", "isVirtual": True, "message": {"id": "m2", "role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_cc_2", "name": "mcp__fetch__fetch", "input": {"url": "https://example.com"}}]}},
    ]
    r = _run(tmp_path, "cc", records)
    assert r.returncode == 0, r.stderr
    out = _out(tmp_path)
    assert "SECRET" not in json.dumps(out)
    assert out[0]["toolUseResult"] == [{"name": "mcp__fetch__fetch", "about": "Fetches a URL."}]


def test_the_parent_chain_skips_dropped_records(tmp_path: Path) -> None:
    records = [
        {"type": "user", "uuid": "u1", "parentUuid": None, "message": {"role": "user", "content": "hi"}},
        {"type": "attachment", "uuid": "x1", "parentUuid": "u1", "attachment": {"type": "environment"}},
        {"type": "attachment", "uuid": "x2", "parentUuid": "x1", "attachment": {"type": "skill_listing"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "x2", "message": {"id": "m", "role": "assistant", "content": []}},
    ]
    r = _run(tmp_path, "cc", records)
    assert r.returncode == 0, r.stderr
    assert [(x["uuid"], x["parentUuid"]) for x in _out(tmp_path)] == [("u1", None), ("a1", "u1")]


def test_the_time_zone_becomes_utc(tmp_path: Path) -> None:
    records = [{"timestamp": "t", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "<environment_context>\n  <timezone>Europe/SECRETZONE</timezone>\n</environment_context>"}]}}]
    r = _run(tmp_path, "codex", records)
    assert r.returncode == 0, r.stderr
    text = json.dumps(_out(tmp_path))
    assert "SECRETZONE" not in text and "<timezone>UTC</timezone>" in text
