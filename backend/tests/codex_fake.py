"""Builder for a synthetic OpenAI Codex home (``~/.codex``).

Tests never read a real Codex home (plan §5, D10). This module writes a
small fake one instead. The record shapes follow ``openai/codex`` at commit
``f2d3086682a848e9121c5f688cf1d77ad5d2a114``:

* ``RolloutLine`` (``codex-rs/history/src/lib.rs``): ``{"timestamp", "type",
  "payload"}``, with ``type`` one of ``session_meta``, ``turn_context``,
  ``response_item``, ``event_msg``, ``world_state``, ``token_usage_record``...
* ``SessionMeta`` and ``TurnContextItem`` (``codex-rs/protocol/src/protocol.rs``).
* ``ResponseItem`` (``codex-rs/protocol/src/models.rs``): legacy history.
* ``event_msg`` / ``item_completed`` with a ``TurnItem``
  (``codex-rs/protocol/src/items.rs``): paginated history.

Each record keeps the fields that Explorer reads plus the required fields
of its Rust type; optional fields that nothing reads are left out.

``build_codex_home(home)`` writes the default corpus. The ``THREAD_*``
constants name its threads. The record helpers (``rollout_line``,
``session_meta``, ``item_completed``, ...) and ``write_rollout`` let a test
build extra rollouts in its own file.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CLI_VERSION = "0.164.0"

#: Paginated history, plain ``.jsonl`` in ``sessions/``. Two session-index
#: names (the last one wins), two turns with different models, snapshots,
#: images, a damaged line, ``Plan`` and an unknown item type.
THREAD_PAGINATED = "019a7c10-0000-7000-8000-00000000a001"
#: Legacy history, ``.jsonl.zst`` in ``sessions/``. Parallel tool calls whose
#: outputs come in the other order, encrypted reasoning, an input image,
#: injected context, and a damaged line. No name anywhere: the title comes
#: from ``state_5.title``.
THREAD_LEGACY = "019a0f20-0000-7000-8000-00000000b002"
#: Paginated, plain, in ``archived_sessions/``; ``state_5.archived = 1``.
#: Named only in ``state_5.name``.
THREAD_ARCHIVED = "0199e030-0000-7000-8000-00000000c003"
#: A subagent of ``THREAD_PAGINATED`` (``thread_source = 'subagent'``),
#: ``.jsonl.zst`` in ``sessions/``.
THREAD_SUBAGENT = "019a7c40-0000-7000-8000-00000000d004"
#: A guardian review thread (``thread_source = 'guardian_review'``).
THREAD_GUARDIAN = "019a7c50-0000-7000-8000-00000000e005"
#: Imported from Claude Code (listed in ``external_agent_session_imports.json``).
#: It has no ``state_5`` row and no name: the title comes from the first
#: human message.
THREAD_IMPORTED = "019a8060-0000-7000-8000-00000000f006"

ALL_THREADS = (
    THREAD_PAGINATED,
    THREAD_LEGACY,
    THREAD_ARCHIVED,
    THREAD_SUBAGENT,
    THREAD_GUARDIAN,
    THREAD_IMPORTED,
)

#: The Claude Code session that ``THREAD_IMPORTED`` came from.
IMPORTED_SOURCE_PATH = (
    "/home/fixture/.claude/projects/-home-fixture-proj-delta/"
    "5e1f0c3a-9b7d-4c2e-8f6a-1d2b3c4d5e6f.jsonl"
)

# A 1x1 PNG, so an inline image reference is a real data URL.
PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
    "QVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

DAMAGED_LINE = '{"timestamp":"2026-11-10T09:00:09.000Z","type":"event_msg","payload":{"type":"item_comp'


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------


def ts(when: datetime) -> str:
    """The ``RolloutLine.timestamp`` format: milliseconds and ``Z``."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def rollout_line(when: datetime, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": ts(when), "type": type_, "payload": payload}


def session_meta(
    when: datetime,
    thread_id: str,
    *,
    cwd: str,
    history_mode: str,
    source: Any = "cli",
    thread_source: str | None = "user",
    parent_thread_id: str | None = None,
    agent_nickname: str | None = None,
    git: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": parent_thread_id or thread_id,
        "id": thread_id,
        "timestamp": ts(when),
        "cwd": cwd,
        "originator": "codex_cli_rs",
        "cli_version": CLI_VERSION,
        "source": source,
        "model_provider": "openai",
        "base_instructions": None,
        "history_mode": history_mode,
    }
    if thread_source is not None:
        payload["thread_source"] = thread_source
    if parent_thread_id is not None:
        payload["parent_thread_id"] = parent_thread_id
    if agent_nickname is not None:
        payload["agent_nickname"] = agent_nickname
    if git is not None:
        payload["git"] = git
    return rollout_line(when, "session_meta", payload)


def turn_context(when: datetime, turn_id: str, *, cwd: str, model: str) -> dict[str, Any]:
    return rollout_line(
        when,
        "turn_context",
        {
            "turn_id": turn_id,
            "cwd": cwd,
            "approval_policy": "on-request",
            "sandbox_policy": {
                "type": "workspace-write",
                "writable_roots": [],
                "network_access": False,
                "exclude_tmpdir_env_var": False,
                "exclude_slash_tmp": False,
            },
            "model": model,
            "effort": "medium",
            "summary": "auto",
        },
    )


def item_completed(
    when: datetime, thread_id: str, turn_id: str, item: dict[str, Any]
) -> dict[str, Any]:
    """An ``event_msg`` / ``item_completed`` record (paginated history)."""
    return rollout_line(
        when,
        "event_msg",
        {
            "type": "item_completed",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item": item,
            "completed_at_ms": ms(when),
        },
    )


def user_message_item(item_id: str, text: str, *extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "UserMessage",
        "id": item_id,
        "content": [{"type": "text", "text": text, "text_elements": []}, *extra],
    }


def agent_message_item(item_id: str, text: str, *, phase: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "AgentMessage",
        "id": item_id,
        "content": [{"type": "Text", "text": text}],
    }
    if phase is not None:
        item["phase"] = phase
    return item


def reasoning_item(item_id: str, *summary: str) -> dict[str, Any]:
    """An empty ``summary`` is what Codex keeps when the reasoning is encrypted."""
    return {"type": "Reasoning", "id": item_id, "summary_text": list(summary), "raw_content": []}


def command_item(
    item_id: str, command: list[str], *, cwd: str, output: str, exit_code: int = 0
) -> dict[str, Any]:
    return {
        "type": "CommandExecution",
        "id": item_id,
        "command": command,
        "cwd": f"file://{cwd}",
        "parsed_cmd": [{"type": "unknown", "cmd": " ".join(command)}],
        "source": "agent",
        "status": "completed" if exit_code == 0 else "failed",
        "aggregated_output": output,
        "exit_code": exit_code,
        "duration": {"secs": 0, "nanos": 120_000_000},
    }


def response_item(when: datetime, payload: dict[str, Any]) -> dict[str, Any]:
    return rollout_line(when, "response_item", payload)


def message(role: str, *content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message", "role": role, "content": list(content)}


def input_text(text: str) -> dict[str, Any]:
    return {"type": "input_text", "text": text}


def output_text(text: str) -> dict[str, Any]:
    return {"type": "output_text", "text": text}


def function_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # The Responses API sends the arguments as JSON text inside a string.
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id}


def function_call_output(call_id: str, output: Any) -> dict[str, Any]:
    """``output`` is a string or a list of content items, as on the wire."""
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def custom_tool_call(call_id: str, name: str, tool_input: str) -> dict[str, Any]:
    """A freeform tool call (``ResponseItem::CustomToolCall``): ``input`` is plain text."""
    return {"type": "custom_tool_call", "status": "completed", "call_id": call_id, "name": name, "input": tool_input}


def custom_tool_call_output(call_id: str, text: str) -> dict[str, Any]:
    # The work-Mac shape probe (#84): the text is in ``payload.output[].text``.
    return {"type": "custom_tool_call_output", "call_id": call_id, "output": [{"type": "input_text", "text": text}]}


def local_shell_call(call_id: str, command: list[str]) -> dict[str, Any]:
    return {
        "type": "local_shell_call",
        "call_id": call_id,
        "status": "completed",
        "action": {"type": "exec", "command": command, "timeout_ms": None, "working_directory": None, "env": None, "user": None},
    }


def world_state(when: datetime) -> dict[str, Any]:
    return rollout_line(when, "world_state", {"full": True, "state": {"open_files": []}})


def token_usage_record(when: datetime, thread_id: str, turn_id: str) -> dict[str, Any]:
    usage = {
        "input_tokens": 1200,
        "cached_input_tokens": 0,
        "output_tokens": 80,
        "reasoning_output_tokens": 40,
        "total_tokens": 1280,
    }
    return rollout_line(
        when,
        "token_usage_record",
        {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "session_id": thread_id,
            "root_turn_id": turn_id,
            "response_id": f"resp_{turn_id[-4:]}",
            "usage": usage,
            "turn_token_usage": usage,
            "thread_token_usage": usage,
        },
    )


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def rollout_stem(started: datetime, thread_id: str) -> str:
    return f"rollout-{started.strftime('%Y-%m-%dT%H-%M-%S')}-{thread_id}"


def rollout_path(home: Path, started: datetime, thread_id: str, *, archived: bool = False, zst: bool = False) -> Path:
    """``sessions/YYYY/MM/DD/<stem>.jsonl[.zst]``, or flat in ``archived_sessions/``."""
    folder = (
        home / "archived_sessions"
        if archived
        else home / "sessions" / f"{started:%Y}" / f"{started:%m}" / f"{started:%d}"
    )
    return folder / (rollout_stem(started, thread_id) + (".jsonl.zst" if zst else ".jsonl"))


def encode_lines(lines: list[dict[str, Any] | str]) -> bytes:
    """One JSON object per line, each ending in a newline. A ``str`` is written as is."""
    return b"".join(
        (line if isinstance(line, str) else json.dumps(line, separators=(",", ":"))).encode() + b"\n"
        for line in lines
    )


def zstd_compress(data: bytes) -> bytes:
    """One zstd frame with no content size, as Codex's streaming encoder writes it."""
    if sys.version_info >= (3, 14):
        from compression import zstd

        compressor = zstd.ZstdCompressor()
        return compressor.compress(data) + compressor.flush()
    import zstandard  # a dependency only below 3.14

    return zstandard.ZstdCompressor(write_content_size=False).compress(data)


def write_rollout(path: Path, lines: list[dict[str, Any] | str]) -> Path:
    """Write a rollout; a ``.zst`` suffix compresses it."""
    data = encode_lines(lines)
    if path.suffix == ".zst":
        data = zstd_compress(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


STATE_COLUMNS = (
    "id", "rollout_path", "created_at", "updated_at", "source", "model_provider",
    "cwd", "title", "sandbox_policy", "approval_mode", "tokens_used",
    "has_user_event", "archived", "archived_at", "git_sha", "git_branch",
    "git_origin_url", "cli_version", "first_user_message", "model",
    "created_at_ms", "updated_at_ms", "thread_source", "history_mode", "name",
)

# The columns of codex-rs/state/migrations that the fake keeps (plan §3).
_STATE_SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    model_provider TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    has_user_event INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at INTEGER,
    git_sha TEXT,
    git_branch TEXT,
    git_origin_url TEXT,
    cli_version TEXT NOT NULL DEFAULT '',
    first_user_message TEXT NOT NULL DEFAULT '',
    model TEXT,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    thread_source TEXT,
    history_mode TEXT NOT NULL DEFAULT 'legacy',
    name TEXT
)
"""


def write_state_db(home: Path, rows: list[dict[str, Any]]) -> Path:
    """Write ``state_5.sqlite`` in WAL mode, as Codex keeps it.

    The connection closes cleanly, so no ``-wal`` or ``-shm`` file remains.
    """
    path = home / "state_5.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_STATE_SCHEMA)
        placeholders = ", ".join("?" for _ in STATE_COLUMNS)
        conn.executemany(
            f"INSERT INTO threads ({', '.join(STATE_COLUMNS)}) VALUES ({placeholders})",
            [tuple(row.get(c) for c in STATE_COLUMNS) for row in rows],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def state_row(
    thread_id: str,
    path: Path,
    *,
    started: datetime,
    updated: datetime,
    cwd: str,
    title: str,
    model: str,
    history_mode: str,
    source: Any = "cli",
    thread_source: str | None = "user",
    archived: bool = False,
    name: str | None = None,
    git: dict[str, str] | None = None,
) -> dict[str, Any]:
    git = git or {}
    return {
        "id": thread_id,
        "rollout_path": str(path),
        "created_at": int(started.timestamp()),
        "updated_at": int(updated.timestamp()),
        "source": source if isinstance(source, str) else json.dumps(source),
        "model_provider": "openai",
        "cwd": cwd,
        "title": title,
        "sandbox_policy": '{"type":"workspace-write"}',
        "approval_mode": "on-request",
        "tokens_used": 1280,
        # 0 for every local thread (plan D2); never a reason to hide one.
        "has_user_event": 0,
        "archived": int(archived),
        "archived_at": int(updated.timestamp()) if archived else None,
        "git_sha": git.get("commit_hash"),
        "git_branch": git.get("branch"),
        "git_origin_url": git.get("repository_url"),
        "cli_version": CLI_VERSION,
        "first_user_message": title,
        "model": model,
        "created_at_ms": ms(started),
        "updated_at_ms": ms(updated),
        "thread_source": thread_source,
        "history_mode": history_mode,
        "name": name,
    }


def write_session_index(home: Path, entries: list[tuple[str, str, datetime]]) -> Path:
    """Append-only ``(id, thread_name, updated_at)`` lines; the last one for an id wins."""
    path = home / "session_index.jsonl"
    path.write_bytes(
        encode_lines([{"id": i, "thread_name": n, "updated_at": ts(w)} for i, n, w in entries])
    )
    return path


def write_imports(home: Path, records: list[dict[str, Any]]) -> Path:
    path = home / "external_agent_session_imports.json"
    path.write_text(json.dumps({"records": records, "detected_connector_records": []}, indent=2))
    return path


# ---------------------------------------------------------------------------
# The default corpus
# ---------------------------------------------------------------------------


def _at(day: str, seconds: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(seconds=seconds)


def _paginated_lines(t0: datetime) -> list[dict[str, Any] | str]:
    tid, cwd = THREAD_PAGINATED, "/home/fixture/proj-alpha"
    git = {
        "commit_hash": "3f2a9c1d0b7e6f5a4c3b2a190807060504030201",
        "branch": "main",
        "repository_url": "https://example.com/fixture/proj-alpha.git",
    }
    turn1, turn2 = "turn-a1", "turn-a2"
    s = lambda n: t0 + timedelta(seconds=n)
    return [
        session_meta(t0, tid, cwd=cwd, history_mode="paginated", git=git),
        turn_context(s(1), turn1, cwd=cwd, model="gpt-5.5-codex"),
        world_state(s(1)),
        # Model context; in paginated history these are not the visible turns.
        response_item(s(2), message("developer", input_text("<permissions instructions>Fixture sandbox rules.</permissions instructions>"))),
        response_item(s(2), message("user", input_text("<environment_context><cwd>/home/fixture/proj-alpha</cwd></environment_context>"))),
        item_completed(
            s(3), tid, turn1,
            user_message_item(
                "item-a1-user",
                "Refactor the parser module and list the files first.",
                {"type": "image", "image_url": PNG_DATA_URL},
                {"type": "local_image", "path": "/home/fixture/proj-alpha/screenshot.png"},
            ),
        ),
        response_item(s(3), message("user", input_text("Refactor the parser module and list the files first."))),
        item_completed(s(4), tid, turn1, reasoning_item("item-a1-think", "I should list the files before I change anything.")),
        item_completed(
            s(5), tid, turn1,
            command_item("item-a1-cmd", ["bash", "-lc", "ls src"], cwd=cwd, output="parser.py\nlexer.py\n"),
        ),
        # Two snapshots of one item: the later one replaces the earlier one.
        item_completed(s(6), tid, turn1, agent_message_item("item-a1-reply", "The parser lives in", phase="commentary")),
        item_completed(s(7), tid, turn1, agent_message_item("item-a1-reply", "The parser lives in src/parser.py.", phase="final_answer")),
        response_item(s(7), message("assistant", output_text("The parser lives in src/parser.py."))),
        token_usage_record(s(8), tid, turn1),
        DAMAGED_LINE,
        "not json at all",
        turn_context(s(10), turn2, cwd=cwd, model="gpt-5.6-codex"),
        item_completed(s(11), tid, turn2, user_message_item("item-a2-user", "Now write the plan.")),
        # Reasoning with no readable summary (Codex kept only the encrypted form).
        item_completed(s(12), tid, turn2, reasoning_item("item-a2-think")),
        item_completed(s(13), tid, turn2, {"type": "Plan", "id": "item-a2-plan", "text": "1. Split the lexer.\n2. Add tests."}),
        # Not one of the 19 TurnItem types: a fallback block and a warning.
        item_completed(s(14), tid, turn2, {"type": "HologramCall", "id": "item-a2-holo", "beam": "blue"}),
        item_completed(s(15), tid, turn2, agent_message_item("item-a2-reply", "Plan: split the lexer, then add tests.")),
        token_usage_record(s(16), tid, turn2),
    ]


def _legacy_lines(t0: datetime) -> list[dict[str, Any] | str]:
    tid, cwd = THREAD_LEGACY, "/home/fixture/proj-beta"
    s = lambda n: t0 + timedelta(seconds=n)
    return [
        session_meta(t0, tid, cwd=cwd, history_mode="legacy"),
        response_item(s(1), {"type": "additional_tools", "role": "developer", "tools": [{"type": "function", "name": "shell"}]}),
        response_item(s(1), message("developer", input_text("<permissions instructions>Fixture sandbox rules.</permissions instructions>"))),
        response_item(s(1), message("user", input_text("<environment_context><cwd>/home/fixture/proj-beta</cwd></environment_context>"))),
        turn_context(s(2), "turn-b1", cwd=cwd, model="gpt-5.4-codex"),
        response_item(
            s(3),
            message("user", input_text("Why does the build fail? Here is the error."), {"type": "input_image", "image_url": PNG_DATA_URL}),
        ),
        response_item(
            s(4),
            {"type": "reasoning", "summary": [], "encrypted_content": "gAAAAABfixture-encrypted-reasoning=="},
        ),
        # Two parallel calls; their outputs come in the other order.
        response_item(s(5), function_call("call_b1_first", "shell", {"command": ["cat", "build.log"]})),
        response_item(s(5), function_call("call_b1_second", "shell", {"command": ["cat", "Makefile"]})),
        response_item(s(6), function_call_output("call_b1_second", "all:\n\tcc -o app main.c\n")),
        response_item(
            s(6),
            function_call_output("call_b1_first", [{"type": "input_text", "text": "main.c:3: error: missing ';'"}]),
        ),
        "{broken",
        response_item(s(7), {"type": "reasoning", "summary": [{"type": "summary_text", "text": "The log points to main.c line 3."}], "encrypted_content": None}),
        response_item(s(8), message("assistant", output_text("Line 3 of main.c needs a semicolon."))),
        token_usage_record(s(9), tid, "turn-b1"),
    ]


def _simple_paginated_lines(
    t0: datetime, tid: str, cwd: str, user: str, reply: str, **meta: Any
) -> list[dict[str, Any] | str]:
    turn = f"turn-{tid[-4:]}"
    s = lambda n: t0 + timedelta(seconds=n)
    return [
        session_meta(t0, tid, cwd=cwd, history_mode="paginated", **meta),
        turn_context(s(1), turn, cwd=cwd, model="gpt-5.5-codex"),
        item_completed(s(2), tid, turn, user_message_item(f"item-{tid[-4:]}-user", user)),
        item_completed(s(3), tid, turn, agent_message_item(f"item-{tid[-4:]}-reply", reply)),
    ]


#: Paginated history with tool calls that have no ``item_completed`` item
#: (issue #84). Not in ``ALL_THREADS`` or ``build_codex_home``: other suites
#: count the default sessions. ``write_orphan_tool_calls`` writes it.
THREAD_ORPHAN_CALLS = "019aa100-0000-7000-8000-0000000000c1"
ORPHAN_TURN = "turn-o1"
ORPHAN_MODEL = "gpt-5.5-codex"
ORPHAN_STARTED = _at("2026-11-19T11:00:00")

#: The call ids, in rollout order. An orphan call has no item with its id.
ORPHAN_PATCH = "call_o1_patch_failed"  # custom_tool_call apply_patch that failed before emitter.begin
ORPHAN_EXEC = "call_o1_exec"  # custom_tool_call: the outer code-mode exec tool
NESTED_EXEC_ITEM = "exec-6b1f3c2e-8d4a-4f5b-9c7e-2a1b3c4d5e6f"  # its nested CommandExecution item
MATCHED_SHELL = "call_o1_shell_ok"  # function_call with a CommandExecution item of the same id
MATCHED_PATCH = "call_o1_patch_ok"  # custom_tool_call with a FileChange item of the same id
ORPHAN_UNKNOWN = "call_o1_unknown"  # function_call to a tool that does not exist
ORPHAN_LONE_OUTPUT = "call_o1_lost"  # a custom_tool_call_output with no call
ORPHAN_NO_OUTPUT = "call_o1_ls"  # local_shell_call with no output

#: One word per place; no other text in the rollout holds it.
ORPHAN_WORDS = {
    "patch_input": "quokkainput",
    "patch_output": "wombatoutput",
    "exec_input": "ocelotscript",
    "exec_output": "ocelotresult",
    "nested_output": "lynxnested",
    "matched_shell_output": "lemurshell",
    "matched_patch_output": "lemurpatch",
    "unknown_input": "gnuargs",
    "unknown_output": "ibexunsupported",
    "lone_output": "yakorphan",
    "no_output_input": "tapirshell",
    "message": "heronreply",
}


def orphan_tool_calls_lines(t0: datetime = ORPHAN_STARTED) -> list[dict[str, Any] | str]:
    """One paginated turn. Codex writes ``response_item`` tool records for every
    call, but an ``item_completed`` item only for some (``rollout/src/policy.rs``,
    ``core/src/tools/handlers/apply_patch.rs``, ``core/src/tools/code_mode/``)."""
    tid, turn, cwd, w = THREAD_ORPHAN_CALLS, ORPHAN_TURN, "/home/fixture/proj-omega", ORPHAN_WORDS
    s = lambda n: t0 + timedelta(seconds=n)
    patch = f"*** Begin Patch\n*** Update File: src/lexer.py\n@@\n-{w['patch_input']} = 1\n+TOKENS = []\n*** End Patch\n"
    return [
        session_meta(t0, tid, cwd=cwd, history_mode="paginated"),
        turn_context(s(1), turn, cwd=cwd, model=ORPHAN_MODEL),
        item_completed(s(2), tid, turn, user_message_item("item-o1-user", "Fix the lexer tokens.")),
        response_item(s(2), message("user", input_text("Fix the lexer tokens."))),
        # A freeform apply_patch that fails verification: no FileChange item.
        response_item(s(3), custom_tool_call(ORPHAN_PATCH, "apply_patch", patch)),
        response_item(s(4), custom_tool_call_output(
            ORPHAN_PATCH, f"apply_patch verification failed: Failed to find expected lines in src/lexer.py:\n{w['patch_output']}")),
        # The outer code-mode exec has no item; the shell call inside it has its own.
        response_item(s(5), custom_tool_call(ORPHAN_EXEC, "exec", f"// {w['exec_input']}\nawait tools.shell({{command: ['cat', 'notes.txt']}});")),
        item_completed(s(6), tid, turn, command_item(NESTED_EXEC_ITEM, ["cat", "notes.txt"], cwd=cwd, output=f"{w['nested_output']}\n")),
        response_item(s(7), custom_tool_call_output(ORPHAN_EXEC, f"Script done: {w['exec_output']}")),
        # Matched calls: the item has the call's id, so the item alone shows.
        response_item(s(8), function_call(MATCHED_SHELL, "exec_command", {"cmd": "ls src"})),
        item_completed(s(9), tid, turn, command_item(MATCHED_SHELL, ["bash", "-lc", "ls src"], cwd=cwd, output=f"{w['matched_shell_output']}.py\n")),
        response_item(s(9), function_call_output(MATCHED_SHELL, f"{w['matched_shell_output']}.py\n")),
        response_item(s(10), custom_tool_call(MATCHED_PATCH, "apply_patch", "*** Begin Patch\n*** Add File: src/tokens.py\n+TOKENS = []\n*** End Patch\n")),
        item_completed(s(11), tid, turn, {
            "type": "FileChange", "id": MATCHED_PATCH, "status": "completed",
            "changes": {f"{cwd}/src/tokens.py": {"type": "add", "content": "TOKENS = []\n"}},
            "stdout": f"Success. Updated the following files:\nA src/tokens.py {w['matched_patch_output']}\n", "stderr": "",
        }),
        response_item(s(11), custom_tool_call_output(MATCHED_PATCH, f"Success. Updated the following files:\nA src/tokens.py {w['matched_patch_output']}\n")),
        # An unknown tool: Codex answers "unsupported call" and makes no item.
        response_item(s(12), function_call(ORPHAN_UNKNOWN, "frobnicate", {"level": w["unknown_input"]})),
        response_item(s(13), function_call_output(ORPHAN_UNKNOWN, f"unsupported call: frobnicate ({w['unknown_output']})")),
        # An output whose call is not in the rollout, and a call with no output.
        response_item(s(14), custom_tool_call_output(ORPHAN_LONE_OUTPUT, f"Lost output: {w['lone_output']}")),
        response_item(s(15), local_shell_call(ORPHAN_NO_OUTPUT, ["echo", w["no_output_input"]])),
        item_completed(s(16), tid, turn, agent_message_item("item-o1-reply", f"The lexer is fixed ({w['message']}).")),
        response_item(s(16), message("assistant", output_text(f"The lexer is fixed ({w['message']})."))),
    ]


def write_orphan_tool_calls(home: Path) -> Path:
    """Write the ``THREAD_ORPHAN_CALLS`` rollout into ``home`` and return its path."""
    return write_rollout(rollout_path(home, ORPHAN_STARTED, THREAD_ORPHAN_CALLS), orphan_tool_calls_lines())


#: Code mode runs (issue #90). Paginated, not in ``ALL_THREADS``. The output
#: headers follow ``core/src/tools/code_mode/`` at ``f2d3086``.
THREAD_CODE_MODE = "019aa200-0000-7000-8000-0000000000c9"
CM_TURN = "turn-cm1"
CM_STARTED = _at("2026-11-24T12:00:00")
CM_DONE = "call_cm_done"  # completed in one output, with one nested call
CM_YIELD = "call_cm_yield"  # yields (cell 7), one nested call, then `wait` finishes it
CM_WAIT = "call_cm_wait"  # the `wait` call for cell 7
CM_NOTIFY = "call_cm_notify"  # a notify() output, then the final output
CM_TWO_A = "call_cm_two_a"  # cells 8 and 9 are alive at once
CM_TWO_B = "call_cm_two_b"
CM_WAIT_A = "call_cm_wait_a"
CM_WAIT_B = "call_cm_wait_b"
CM_SILENT = "call_cm_silent"  # no output at all
CM_RUNS = (CM_DONE, CM_YIELD, CM_NOTIFY, CM_TWO_A, CM_TWO_B, CM_SILENT)
CM_NESTED_DONE = "exec-00000000-0000-4000-8000-0000000000d1"
CM_NESTED_YIELD = "exec-00000000-0000-4000-8000-0000000000d2"
CM_NESTED_AMBIGUOUS = "exec-00000000-0000-4000-8000-0000000000d3"
CM_WORDS = {
    "code": "cxscriptword",
    "nested_output": "cxnestedword",
    "returned": "cxreturnedword",
    "notify": "cxnotifyword",
    "reply": "cxreplyword",
}


def exec_output(header: str, output: str, wall: float = 0.4) -> str:
    return f"{header}\nWall time {wall} seconds\nOutput:\n{output}"


def code_mode_lines(t0: datetime = CM_STARTED) -> list[dict[str, Any] | str]:
    tid, turn, cwd, w = THREAD_CODE_MODE, CM_TURN, "/home/fixture/proj-cx", CM_WORDS
    s = lambda n: t0 + timedelta(seconds=n)
    ls = lambda item_id, out: item_completed(s(0), tid, turn, command_item(item_id, ["ls", "src"], cwd=cwd, output=out))
    return [
        session_meta(t0, tid, cwd=cwd, history_mode="paginated"),
        turn_context(s(1), turn, cwd=cwd, model="gpt-5.5-codex"),
        item_completed(s(2), tid, turn, user_message_item("item-cm-user", "List the files.")),
        # Completed.
        response_item(s(3), custom_tool_call(CM_DONE, "exec", f"// {w['code']}\nconst r = await tools.exec_command({{cmd: 'ls src'}});\ntext(r.output);")),
        ls(CM_NESTED_DONE, f"app.py {w['nested_output']}\n"),
        response_item(s(5), custom_tool_call_output(CM_DONE, exec_output("Script completed", f"app.py {w['returned']}\n"))),
        # Yielded, then finished by `wait`.
        response_item(s(6), custom_tool_call(CM_YIELD, "exec", "const r = await tools.exec_command({cmd: 'sleep 5; ls src'});\ntext(r.output);")),
        response_item(s(7), custom_tool_call_output(CM_YIELD, exec_output("Script running with cell ID 7", "", 10.0))),
        ls(CM_NESTED_YIELD, "app.py\n"),
        response_item(s(9), function_call(CM_WAIT, "wait", {"cell_id": "7"})),
        response_item(s(10), function_call_output(CM_WAIT, exec_output("Script completed", "yielded-done\n"))),
        # notify() adds an output to the same call.
        response_item(s(11), custom_tool_call(CM_NOTIFY, "exec", "notify('step one');\ntext('end');")),
        response_item(s(12), custom_tool_call_output(CM_NOTIFY, f"{w['notify']} step one")),
        response_item(s(13), custom_tool_call_output(CM_NOTIFY, exec_output("Script completed", "end\n"))),
        # Two cells alive at once: the nested call links to neither.
        response_item(s(14), custom_tool_call(CM_TWO_A, "exec", "await tools.exec_command({cmd: 'sleep 9'});")),
        response_item(s(15), custom_tool_call_output(CM_TWO_A, exec_output("Script running with cell ID 8", "", 10.0))),
        response_item(s(16), custom_tool_call(CM_TWO_B, "exec", "await tools.exec_command({cmd: 'ls src'});")),
        response_item(s(17), custom_tool_call_output(CM_TWO_B, exec_output("Script running with cell ID 9", "", 10.0))),
        ls(CM_NESTED_AMBIGUOUS, "app.py\n"),
        response_item(s(19), function_call(CM_WAIT_A, "wait", {"cell_id": "8"})),
        response_item(s(20), function_call_output(CM_WAIT_A, exec_output("Script failed", "Error: timeout\n"))),
        response_item(s(21), function_call(CM_WAIT_B, "wait", {"cell_id": "9"})),
        response_item(s(22), function_call_output(CM_WAIT_B, exec_output("Script terminated", ""))),
        # No output.
        response_item(s(23), custom_tool_call(CM_SILENT, "exec", "text('never');")),
        item_completed(s(24), tid, turn, agent_message_item("item-cm-reply", f"Done ({w['reply']}).")),
    ]


def write_code_mode(home: Path) -> Path:
    """Write the ``THREAD_CODE_MODE`` rollout into ``home`` and return its path."""
    return write_rollout(rollout_path(home, CM_STARTED, THREAD_CODE_MODE), code_mode_lines())


def build_codex_home(home: Path) -> Path:
    """Write the default synthetic Codex home at ``home`` and return ``home``."""
    home.mkdir(parents=True, exist_ok=True)

    t_pag, t_leg, t_arc = _at("2026-11-10T09:00:00"), _at("2026-10-20T14:30:00"), _at("2026-10-05T08:15:00")
    t_sub, t_grd, t_imp = _at("2026-11-10T09:05:00"), _at("2026-11-10T09:20:00"), _at("2026-11-12T16:00:00")
    sub_source = {
        "subagent": {
            "thread_spawn": {
                "parent_thread_id": THREAD_PAGINATED,
                "depth": 1,
                "agent_path": None,
                "agent_nickname": "Scout",
                "agent_role": "explorer",
            }
        }
    }
    grd_source = {"subagent": "review"}

    p_pag = write_rollout(rollout_path(home, t_pag, THREAD_PAGINATED), _paginated_lines(t_pag))
    p_leg = write_rollout(rollout_path(home, t_leg, THREAD_LEGACY, zst=True), _legacy_lines(t_leg))
    p_arc = write_rollout(
        rollout_path(home, t_arc, THREAD_ARCHIVED, archived=True),
        _simple_paginated_lines(t_arc, THREAD_ARCHIVED, "/home/fixture/proj-gamma", "Summarize the release notes.", "The release adds two fixes."),
    )
    p_sub = write_rollout(
        rollout_path(home, t_sub, THREAD_SUBAGENT, zst=True),
        _simple_paginated_lines(
            t_sub, THREAD_SUBAGENT, "/home/fixture/proj-alpha", "Find every caller of parse().", "parse() has 3 callers.",
            source=sub_source, thread_source="subagent", parent_thread_id=THREAD_PAGINATED, agent_nickname="Scout",
        ),
    )
    p_grd = write_rollout(
        rollout_path(home, t_grd, THREAD_GUARDIAN),
        _simple_paginated_lines(
            t_grd, THREAD_GUARDIAN, "/home/fixture/proj-alpha", "Review the proposed command: rm -rf build/", "Approved: the path is inside the workspace.",
            source=grd_source, thread_source="guardian_review",
        ),
    )
    write_rollout(
        rollout_path(home, t_imp, THREAD_IMPORTED),
        _simple_paginated_lines(t_imp, THREAD_IMPORTED, "/home/fixture/proj-delta", "Continue the migration from the Claude Code session.", "I picked up at step 4."),
    )

    write_session_index(
        home,
        [
            (THREAD_PAGINATED, "Alpha draft", t_pag + timedelta(minutes=1)),
            (THREAD_SUBAGENT, "Scout: callers of parse", t_sub + timedelta(minutes=1)),
            (THREAD_PAGINATED, "Alpha parser refactor", t_pag + timedelta(minutes=30)),
        ],
    )

    write_state_db(
        home,
        [
            state_row(THREAD_PAGINATED, p_pag, started=t_pag, updated=t_pag + timedelta(minutes=30), cwd="/home/fixture/proj-alpha",
                      title="Refactor the parser module and list the files first.", model="gpt-5.6-codex", history_mode="paginated",
                      name="Alpha parser refactor", git={"commit_hash": "3f2a9c1d0b7e6f5a4c3b2a190807060504030201", "branch": "main",
                                                         "repository_url": "https://example.com/fixture/proj-alpha.git"}),
            state_row(THREAD_LEGACY, p_leg, started=t_leg, updated=t_leg + timedelta(minutes=5), cwd="/home/fixture/proj-beta",
                      title="Why does the build fail? Here is the error.", model="gpt-5.4-codex", history_mode="legacy"),
            state_row(THREAD_ARCHIVED, p_arc, started=t_arc, updated=t_arc + timedelta(minutes=2), cwd="/home/fixture/proj-gamma",
                      title="Summarize the release notes.", model="gpt-5.5-codex", history_mode="paginated", archived=True,
                      name="Gamma release notes"),
            state_row(THREAD_SUBAGENT, p_sub, started=t_sub, updated=t_sub + timedelta(minutes=1), cwd="/home/fixture/proj-alpha",
                      title="Find every caller of parse().", model="gpt-5.5-codex", history_mode="paginated",
                      source=sub_source, thread_source="subagent"),
            state_row(THREAD_GUARDIAN, p_grd, started=t_grd, updated=t_grd + timedelta(seconds=10), cwd="/home/fixture/proj-alpha",
                      title="Review the proposed command: rm -rf build/", model="gpt-5.5-codex", history_mode="paginated",
                      source=grd_source, thread_source="guardian_review"),
        ],
    )

    write_imports(
        home,
        [
            {
                "source_path": IMPORTED_SOURCE_PATH,
                "content_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "imported_thread_id": THREAD_IMPORTED,
                "imported_at": int(t_imp.timestamp()),
                "source_modified_at": int(t_imp.timestamp()) - 3600,
                "connector_names": [],
                "title": "Delta migration",
            }
        ],
    )
    return home
