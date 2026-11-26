"""Synthetic Claude Code sessions with Code Mode runs (issue #90).

The record shapes follow two ``claude -p`` sessions that Claude Code 2.1.331
wrote on 2026-11-24 (``CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`` in the
``--settings`` env block). Only the shape was kept; every value here is
synthetic.

* The outer call: an assistant ``tool_use`` named ``REPL`` with
  ``input.code``. The record also has ``hostHiddenToolUseIds`` and a
  placeholder ``hostContent`` that Explorer ignores.
* The outer result: a user ``tool_result`` whose text is ``<repl-output>``,
  and ``toolUseResult`` = {code, result, stdout, stderr, toolsUsed, error?,
  refusedCalls?}. A subagent file can lack ``toolUseResult``.
* An inner call: an ``isVirtual`` assistant record with
  ``ranByToolUseId`` = the outer id. Its result: an ``isVirtual`` user
  record whose message content is empty; the content is in ``hostContent``.
  Claude Code writes the inner records AFTER the outer result.
* A background script: the outer result is ``(dispatched #N)`` with
  ``evalId`` and ``asyncDispatched``; a ``poll_events`` attachment with a
  ``repl-eval`` event ends it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSION = "c0de0000-0000-4000-8000-000000000090"
PROJECT = "-home-fixture-proj-cm"
CWD = "/home/fixture/proj-cm"
AGENT_ID = "a0c0de0000000090a"

#: One word per place; no other text in the session holds it.
WORDS = {
    "prompt": "cmpromptword",
    "reply": "cmreplyword",
    "code": "cmscriptword",
    "inner_input": "cminnerinputword",
    "inner_output": "cminneroutputword",
    "returned": "cmreturnedword",
    "stdout": "cmstdoutword",
    "error": "cmerrorword",
    "subagent_code": "cmsubscriptword",
    "background_result": "cmbackgroundword",
    "legacy_output": "cmlegacyword",
}

#: The outer REPL call ids, in file order.
RUN_DONE = "toolu_01CMDONE0000000000000001"
RUN_FAILED = "toolu_01CMFAIL0000000000000002"
RUN_REFUSED = "toolu_01CMREFU0000000000000003"
RUN_TWIN_A = "toolu_01CMTWNA0000000000000004"
RUN_TWIN_B = "toolu_01CMTWNB0000000000000005"
RUN_BACKGROUND = "toolu_01CMBGND0000000000000006"
RUN_UNSETTLED = "toolu_01CMUNST0000000000000007"
RUN_SUBAGENT = "toolu_01CMSUBA0000000000000008"
ALL_RUNS = (RUN_DONE, RUN_FAILED, RUN_REFUSED, RUN_TWIN_A, RUN_TWIN_B, RUN_BACKGROUND, RUN_UNSETTLED)

#: A virtual call with a Code Mode id but no ``ranByToolUseId`` (an older build).
LEGACY_CALL = "toolu_cc_legacy00000000000000000000001"

_n = 0


def _ts() -> str:
    global _n
    _n += 1
    return f"2026-11-24T10:{_n // 60:02d}:{_n % 60:02d}.000Z"


def _base(kind: str, uuid: str, parent: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "type": kind, "uuid": uuid, "parentUuid": parent, "isSidechain": False,
        "sessionId": SESSION, "cwd": CWD, "version": "2.1.331", "timestamp": _ts(), **extra,
    }


def user(uuid: str, parent: str | None, text: str) -> dict[str, Any]:
    return _base("user", uuid, parent, message={"role": "user", "content": text})


def text_reply(uuid: str, parent: str, text: str) -> dict[str, Any]:
    return _base("assistant", uuid, parent, message={
        "id": f"msg_{uuid}", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}],
    })


def outer(uuid: str, parent: str, run_id: str, code: str, *, msg_id: str | None = None,
          description: str | None = None) -> dict[str, Any]:
    tool_input: dict[str, Any] = {"code": code}
    if description:
        tool_input["description"] = description
    return _base(
        "assistant", uuid, parent,
        message={
            "id": msg_id or f"msg_{uuid}", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "tool_use", "id": run_id, "name": "REPL", "input": tool_input,
                         "caller": {"type": "direct"}}],
        },
        madeBy="model",
        hostHiddenToolUseIds=[run_id],
        hostContent=[{"type": "tool_use", "id": run_id, "name": "Bash", "input": {
            "command": "", "description": "A step that was refused as a whole; nothing in it ran: "}}],
    )


def repl_output(*, result: str | None = None, log: str = "", error: str | None = None,
                refused: str | None = None, tools: list[str] | None = None) -> str:
    parts = ["<repl-output>"]
    if tools:
        parts.append(f"<tools-used-before-summary>{', '.join(tools)}</tools-used-before-summary>")
    if log:
        parts.append(f"<log>{log}</log>")
    if refused:
        parts.append(f"<refused>{refused}</refused>")
    if error is not None:
        parts.append(f"<error>{error.replace('<', '&lt;').replace('>', '&gt;')}</error>")
    if result is not None:
        parts.append(f"<result>{result}</result>")
    parts.append("</repl-output>")
    return "\n".join(parts)


def outer_result(uuid: str, parent: str, run_id: str, code: str, *, result: str | None = None,
                 stdout: str = "", stderr: str = "", tools: list[str] | None = None,
                 error: str | None = None, refused: str | None = None,
                 with_tool_use_result: bool = True, eval_id: int | None = None) -> dict[str, Any]:
    dispatched = eval_id is not None
    text = f"(dispatched #{eval_id})" if dispatched else repl_output(
        result=result, log=stdout, error=error, refused=refused, tools=tools)
    entry = _base("user", uuid, parent, message={"role": "user", "content": [{
        "tool_use_id": run_id, "type": "tool_result", "content": text, "is_error": error is not None}]})
    if with_tool_use_result:
        tur: dict[str, Any] = {"code": code, "result": text if dispatched else result,
                               "stdout": stdout, "stderr": stderr}
        if dispatched:
            tur.update(evalId=eval_id, asyncDispatched=True)
        else:
            tur["toolsUsed"] = tools or []
        if error is not None:
            tur["error"] = error
        if refused is not None:
            tur["refusedCalls"] = refused
        entry["toolUseResult"] = tur
    return entry


def inner_call(uuid: str, parent: str, run_id: str | None, call_id: str, name: str,
               tool_input: dict[str, Any]) -> dict[str, Any]:
    entry = _base("assistant", uuid, parent, message={
        "id": f"vmsg-{uuid}", "role": "assistant", "model": "<synthetic>", "type": "message",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]},
        isVirtual=True, madeBy="other")
    if run_id is not None:
        entry["ranByToolUseId"] = run_id
    return entry


def inner_result(uuid: str, parent: str, call_id: str, output: str, *, refused: bool = False) -> dict[str, Any]:
    extra: dict[str, Any] = {"turnCompanion": True, "isVirtual": True}
    if refused:
        # A refused call keeps its text in the message, as 2.1.331 wrote it.
        content, extra["toolDenialKind"] = output, "user-rejected"
    else:
        content = ""
        extra["toolUseResult"] = {"stdout": output, "stderr": ""}
    extra["hostContent"] = [{"type": "tool_result", "tool_use_id": call_id, "content": output, "is_error": refused}]
    return _base("user", uuid, parent, message={"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": call_id, "content": content, "is_error": refused}]}, **extra)


def poll_settled(uuid: str, parent: str, eval_id: int, result: str) -> dict[str, Any]:
    envelope = (
        f'<event nonce="aaaaBBBB" kind="repl-eval" at="2026-11-24T10:59:00.000Z" eval="{eval_id}" '
        f'ms="1200" status="settled">\n<result>{result}</result>\n</event>'
    )
    return _base("attachment", uuid, parent, attachment={
        "type": "poll_events", "envelopes": [envelope], "kinds": ["repl-eval"],
        "toolsUsed": ["Bash"], "remainingWakeCount": 0, "endsRequest": True})


def data_flow(run_id: str, tool: str, index: int) -> dict[str, Any]:
    return {"type": "data-flow-records", "v": 1,
            "records": [{"tool_name": tool, "tool_use_id": run_id, "inner_call_index": index}]}


def main_session() -> list[dict[str, Any]]:
    """Every case in one session, in the order Claude Code writes them."""
    w = WORDS
    done_code = (
        f"// {w['code']}\nconst ls = await $`ls src`.text();\n"
        "const app = await tools.Read({file_path: 'src/app.py'});\n({ files: ls.trim().split('\\n') })"
    )
    long_code = "\n".join(f"const step{i} = {i};" for i in range(20)) + "\nawait $`echo done`.text();"
    return [
        user("u1", None, f"List the files ({w['prompt']})."),
        # 1. Completed, two inner calls; the inner records come after the outer result.
        outer("a1", "u1", RUN_DONE, done_code, description="List src and read app.py"),
        data_flow(RUN_DONE, "Bash", 0),
        outer_result("r1", "a1", RUN_DONE, done_code, result=f"{{ files: ['app.py', '{w['returned']}.py'] }}",
                     stdout=f"{w['stdout']}", tools=["Bash", "Read"]),
        inner_call("i1", "r1", RUN_DONE, "toolu_cc_done0000000000000000000000001", "Bash",
                   {"command": f"ls src  # {w['inner_input']}"}),
        inner_result("j1", "i1", "toolu_cc_done0000000000000000000000001", f"app.py\n{w['inner_output']}.py"),
        inner_call("i2", "j1", RUN_DONE, "toolu_cc_done0000000000000000000000002", "Read",
                   {"file_path": "src/app.py"}),
        inner_result("j2", "i2", "toolu_cc_done0000000000000000000000002", "1\tx = 1"),
        text_reply("t1", "j2", f"There are two files ({w['reply']})."),
        # 2. Failed.
        outer("a2", "t1", RUN_FAILED, "await $`ls src`.text();\nthrow new Error('boom');"),
        outer_result("r2", "a2", RUN_FAILED, "await $`ls src`.text();\nthrow new Error('boom');",
                     error=f"Error: boom {w['error']}\nThrown at <anonymous>", tools=["Bash"]),
        inner_call("i3", "r2", RUN_FAILED, "toolu_cc_fail0000000000000000000000001", "Bash", {"command": "ls src"}),
        inner_result("j3", "i3", "toolu_cc_fail0000000000000000000000001", "app.py"),
        # 3. A refused call.
        outer("a3", "j3", RUN_REFUSED, "const r = await tools.Write({file_path: 'src/new.txt', content: 'hi'});\nr"),
        outer_result("r3", "a3", RUN_REFUSED, "const r = await tools.Write({file_path: 'src/new.txt', content: 'hi'});\nr",
                     result="{ error: 'Permission denied for Write' }", tools=["Write"],
                     refused='Tool calls that were refused while this script ran:\n- "Write", refused: "Permission denied for Write"'),
        inner_call("i4", "r3", RUN_REFUSED, "toolu_cc_refu0000000000000000000000001", "Write",
                   {"file_path": "src/new.txt", "content": "hi"}),
        inner_result("j4", "i4", "toolu_cc_refu0000000000000000000000001", "Permission denied for Write", refused=True),
        # 4. Two runs in one turn (one assistant message, two REPL calls); the second has a long script.
        outer("a4", "j4", RUN_TWIN_A, "await $`ls`.text()", msg_id="msg_twins"),
        outer("a5", "a4", RUN_TWIN_B, long_code, msg_id="msg_twins"),
        outer_result("r4", "a5", RUN_TWIN_A, "await $`ls`.text()", result="src", tools=["Bash"]),
        outer_result("r5", "r4", RUN_TWIN_B, long_code, result="done", tools=["Bash"]),
        inner_call("i5", "r5", RUN_TWIN_B, "toolu_cc_twnb0000000000000000000000001", "Bash", {"command": "echo done"}),
        inner_result("j5", "i5", "toolu_cc_twnb0000000000000000000000001", "done"),
        inner_call("i6", "j5", RUN_TWIN_A, "toolu_cc_twna0000000000000000000000001", "Bash", {"command": "ls"}),
        inner_result("j6", "i6", "toolu_cc_twna0000000000000000000000001", "src"),
        # 5. A background script that settles later, and one that never settles.
        outer("a6", "j6", RUN_BACKGROUND, "await $`sleep 1 && ls`.text()"),
        outer_result("r6", "a6", RUN_BACKGROUND, "await $`sleep 1 && ls`.text()", eval_id=1),
        outer("a7", "r6", RUN_UNSETTLED, "await $`sleep 999`.text()"),
        outer_result("r7", "a7", RUN_UNSETTLED, "await $`sleep 999`.text()", eval_id=2),
        text_reply("t2", "r7", "Both scripts run in the background."),
        inner_call("i7", "t2", RUN_BACKGROUND, "toolu_cc_bgnd0000000000000000000000001", "Bash",
                   {"command": "sleep 1 && ls"}),
        inner_result("j7", "i7", "toolu_cc_bgnd0000000000000000000000001", "src"),
        poll_settled("p1", "j7", 1, f"'{w['background_result']}'"),
        # 6. An older build: a Code Mode id, no parent field.
        inner_call("i8", "p1", None, LEGACY_CALL, "Bash", {"command": "pwd"}),
        inner_result("j8", "i8", LEGACY_CALL, f"/home/fixture/{w['legacy_output']}"),
        text_reply("t3", "j8", "Done."),
    ]


def subagent_session() -> list[dict[str, Any]]:
    """A subagent's own run; its outer result has no ``toolUseResult``."""
    code = f"// {WORDS['subagent_code']}\nawait $`ls src`.nothrow().text()"
    lines = [
        user("s1", None, "Run ls src with your REPL tool."),
        outer("s2", "s1", RUN_SUBAGENT, code),
        outer_result("s3", "s2", RUN_SUBAGENT, code, result="app.py\nutil.py", tools=["Bash"],
                     with_tool_use_result=False),
        inner_call("s4", "s3", RUN_SUBAGENT, "toolu_cc_suba0000000000000000000000001", "Bash", {"command": "ls src"}),
        inner_result("s5", "s4", "toolu_cc_suba0000000000000000000000001", "app.py\nutil.py"),
        text_reply("s6", "s5", "app.py and util.py"),
    ]
    for line in lines:
        line.update(isSidechain=True, agentId=AGENT_ID)
    return lines


def write_lines(path: Path, lines: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    return path


def write_session(claude_dir: Path) -> Path:
    """Write the main session and its subagent under ``<claude_dir>/projects``."""
    project = claude_dir / "projects" / PROJECT
    write_lines(project / SESSION / "subagents" / f"agent-{AGENT_ID}.jsonl", subagent_session())
    return write_lines(project / f"{SESSION}.jsonl", main_session())
