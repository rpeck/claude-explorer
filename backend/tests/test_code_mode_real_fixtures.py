"""Issue #90: Code Mode runs from real recordings.

The fixtures in ``fixtures/cc_code_mode/`` (Claude Code 2.1.331) and
``fixtures/codex_code_mode/`` (Codex 0.164.0) are real sessions with
synthetic prompts on synthetic files, made with ``scripts/scrub_fixture.py``
(see the README in each folder). These tests hold the reader and the
projection to what the real tools write.

Data flow (the common use of Code Mode: one tool's output goes into
another tool, and only a small result goes back to the model):

* ``inputs_from`` on a call: the earlier calls whose output text is in this
  call's input.
* ``returned_from`` on a run: the calls whose output text is in what the
  run returned to the model.

Both are exact text matches, on outputs of 8 or more characters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CC = FIXTURES / "cc_code_mode"
CX = FIXTURES / "codex_code_mode"


def _cc(name: str) -> dict[str, Any]:
    from backend.claude_code_reader import read_claude_code_conversation

    conv = read_claude_code_conversation(CC / name)
    assert conv is not None
    return conv


def _cx(name: str) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    read = read_rollout(CX / name)
    return project_rollout(read.records, damaged_lines=read.damaged_lines)


def _blocks(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for m in conv["chat_messages"] for b in m["content"]]


def _runs(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [b["code_run"] for b in _blocks(conv) if b["type"] == "code_run"]


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def test_cc_background_run_settles_with_its_two_calls() -> None:
    (run,) = _runs(_cc("background-read.jsonl"))
    assert run["background"] is True
    assert run["status"] == "completed"
    assert "util.py" in run["result"]
    assert [c["name"] for c in run["inner_calls"]] == ["Bash", "Read"]
    assert all(c["link_source"] == "transcript_field" for c in run["inner_calls"])
    assert run["inner_calls"][0]["result"] == "app.py\nutil.py"


def test_cc_failed_refused_logged_and_agent_runs() -> None:
    failed, refused, logged, agent = _runs(_cc("failed-refused-subagent.jsonl"))
    assert failed["status"] == "failed" and "synthetic failure" in failed["error"]
    assert [c["name"] for c in failed["inner_calls"]] == ["Bash"]
    (write,) = refused["inner_calls"]
    assert write["refused"] is True and "Write" in refused["refused"]
    assert logged["stdout"] and "y = 2" in logged["stdout"]
    assert [c["name"] for c in agent["inner_calls"]] == ["Agent"]


def test_cc_subagent_run_reads_the_repl_output_text() -> None:
    (run,) = _runs(_cc("subagent-run.jsonl"))
    assert run["result"] == "app.py\nutil.py"
    assert run["status"] == "completed"
    assert [c["name"] for c in run["inner_calls"]] == ["Bash"]


def test_cc_mcp_not_connected_run_fails_from_its_poll_event() -> None:
    conv = _cc("mcp-not-connected-failed.jsonl")
    (run,) = _runs(conv)
    assert run["background"] is True
    assert run["status"] == "failed"
    assert "fetch tool not found" in run["error"]
    # searchTools calls carry no parent field: tool calls, marked, never linked.
    searches = [b for b in _blocks(conv) if b.get("name") == "searchTools"]
    assert len(searches) == 8 and all(b["from_code_mode"] for b in searches)
    # The error record that Claude Code adds for the script is marked too.
    (error_call,) = [b for b in _blocks(conv) if str(b.get("id", "")).startswith("repl-error-")]
    assert error_call["from_code_mode"] is True


def test_cc_mcp_to_cli_pipe_shows_the_data_flow() -> None:
    conv = _cc("mcp-to-cli-pipe.jsonl")
    (run,) = _runs(conv)
    assert run["status"] == "completed" and run["result"] == "31"
    fetch, wc = run["inner_calls"]
    assert fetch["name"] == "mcp__fetch__fetch" and "Example Domain" in fetch["result"]
    assert wc["name"] == "Bash" and wc["inputs_from"] == [0]
    assert fetch["inputs_from"] == []
    # The page text never went back to the model.
    assert run["returned_from"] == []


def test_cc_an_unlinked_search_call_shows_its_result_from_tool_use_result() -> None:
    conv = _cc("mcp-to-cli-pipe.jsonl")
    (search,) = [b for b in _blocks(conv) if b.get("name") == "searchTools"]
    (result,) = [b for b in _blocks(conv) if b.get("tool_use_id") == search["id"]]
    assert "mcp__fetch__fetch" in _result_text(result)


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_completed_run_links_both_commands_and_returns_both_outputs() -> None:
    (run,) = _runs(_cx("completed.jsonl"))
    assert run["status"] == "completed"
    assert [c["result"] for c in run["inner_calls"]] == ["app.py\nutil.py\n", "x = 1  # TODO a\n"]
    assert all(c["link_source"] == "exclusive_window" for c in run["inner_calls"])
    assert run["returned_from"] == [0, 1]


@pytest.fixture(scope="module")
def cases() -> dict[str, Any]:
    return _cx("failed-wait-notify-two-cells-terminate.jsonl")


def test_codex_a_failed_script_has_its_error_not_a_result(cases: dict[str, Any]) -> None:
    failed = _runs(cases)[0]
    assert failed["status"] == "failed"
    assert failed["error"].startswith("Error: synthetic failure")
    assert failed["result"] is None
    assert [c["name"] for c in failed["inner_calls"]] == ["shell"]


def test_codex_a_yielded_script_completes_through_wait(cases: dict[str, Any]) -> None:
    yielded = _runs(cases)[1]
    assert yielded["status"] == "completed"
    assert yielded["result"] == "app.py\nutil.py"
    assert len(yielded["inner_calls"]) == 1


def test_codex_notify_output_joins_its_run(cases: dict[str, Any]) -> None:
    notified = _runs(cases)[2]
    assert "y = 2" in notified["result"] and "step one" in notified["result"]


def test_codex_two_alive_cells_give_one_ambiguous_call_and_one_exact_link(cases: dict[str, Any]) -> None:
    runs = [b for b in _blocks(cases) if b["type"] == "code_run"]
    first, second = runs[3], runs[4]
    (ambiguous,) = [b for b in _blocks(cases) if b.get("type") == "tool_use" and "echo first" in str(b.get("input"))]
    assert ambiguous["from_code_mode"] is True
    assert ambiguous["ambiguous_with"] == [first["id"], second["id"]]
    assert first["code_run"]["inner_calls"] == []
    # "second" completed after the first cell closed: one cell alive, an exact link.
    assert ["sleep 4; echo second" in str(c["input"]) for c in second["code_run"]["inner_calls"]] == [True]


def test_codex_a_terminated_script_and_its_late_command(cases: dict[str, Any]) -> None:
    terminated = _runs(cases)[5]
    assert terminated["status"] == "terminated"
    (late,) = [b for b in _blocks(cases) if b.get("type") == "tool_use" and "sleep 30" in str(b.get("input"))]
    assert late["from_code_mode"] is True and not late.get("ambiguous_with")


def test_codex_cli_to_cli_pipe_shows_the_data_flow() -> None:
    (run,) = _runs(_cx("cli-to-cli-pipe.jsonl"))
    cat, wc = run["inner_calls"]
    assert cat["result"] == "x = 1  # TODO a\n"
    assert wc["inputs_from"] == [0]
    assert run["result"] == "16" and run["returned_from"] == []
