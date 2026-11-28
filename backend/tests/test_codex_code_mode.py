"""Issue #90: Codex code mode runs (``exec``) in the projection.

* Each ``exec`` call is one ``code_run`` block: the script, every output of
  its call id (``notify()`` adds outputs), and the outputs of the ``wait``
  calls that continue its cell. The status comes from the output header.
* A nested tool item (``exec-<uuid>``) links to the one cell that is alive
  (``exclusive_window``). With two cells alive it links to neither: it stays
  a tool call, ``from_code_mode`` with ``ambiguous_with`` both runs.
* ``CODEX_PROJECTION_VERSION`` is 8 (6: exec images, #103; 7: agent message links, #91;
  8: the originator as entrypoint, #93).

All data is synthetic (``codex_fake.code_mode_lines``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as cf

W = cf.CM_WORDS


def _project(tmp_path: Path, lines: list[Any]) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    path = cf.write_rollout(tmp_path / f"rollout-2026-11-24T12-00-00-{cf.THREAD_CODE_MODE}.jsonl", lines)
    read = read_rollout(path)
    return project_rollout(read.records, damaged_lines=read.damaged_lines)


@pytest.fixture
def conv(tmp_path: Path) -> dict[str, Any]:
    return _project(tmp_path, cf.code_mode_lines())


def _blocks(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for m in conv["chat_messages"] for b in m["content"]]


def _runs(conv: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {b["id"]: b["code_run"] for b in _blocks(conv) if b["type"] == "code_run"}


def test_every_exec_call_is_one_code_run_in_order(conv: dict[str, Any]) -> None:
    assert list(_runs(conv)) == list(cf.CM_RUNS)
    assert {b["name"] for b in _blocks(conv) if b["type"] == "code_run"} == {"exec"}


def test_a_completed_run_has_its_script_output_and_its_nested_call(conv: dict[str, Any]) -> None:
    run = _runs(conv)[cf.CM_DONE]
    assert W["code"] in run["code"]
    assert run["status"] == "completed"
    assert W["returned"] in run["result"]
    assert "Wall time" not in run["result"]
    (call,) = run["inner_calls"]
    assert call["id"] == cf.CM_NESTED_DONE
    assert call["name"] == "shell"
    assert W["nested_output"] in call["result"]
    assert call["link_source"] == "exclusive_window"
    assert call["code_run_parent"] == cf.CM_DONE


def test_a_yielded_run_is_finished_by_its_wait_call(conv: dict[str, Any]) -> None:
    run = _runs(conv)[cf.CM_YIELD]
    assert run["status"] == "completed"
    assert "yielded-done" in run["result"]
    assert [c["id"] for c in run["inner_calls"]] == [cf.CM_NESTED_YIELD]
    ids = {b.get("id") for b in _blocks(conv)} | {b.get("tool_use_id") for b in _blocks(conv)}
    assert cf.CM_WAIT not in ids


def test_notify_outputs_join_the_run(conv: dict[str, Any]) -> None:
    run = _runs(conv)[cf.CM_NOTIFY]
    assert W["notify"] in run["result"]
    assert "end" in run["result"]
    assert run["status"] == "completed"


def test_with_two_cells_alive_a_nested_call_links_to_neither(conv: dict[str, Any]) -> None:
    runs = _runs(conv)
    assert runs[cf.CM_TWO_A]["inner_calls"] == runs[cf.CM_TWO_B]["inner_calls"] == []
    assert runs[cf.CM_TWO_A]["status"] == "failed"
    assert runs[cf.CM_TWO_B]["status"] == "terminated"
    (use,) = [b for b in _blocks(conv) if b.get("id") == cf.CM_NESTED_AMBIGUOUS]
    assert use["type"] == "tool_use"
    assert use["from_code_mode"] is True
    assert use["ambiguous_with"] == [cf.CM_TWO_A, cf.CM_TWO_B]


def test_a_run_with_no_output_has_no_result(conv: dict[str, Any]) -> None:
    run = _runs(conv)[cf.CM_SILENT]
    assert run["result"] is None
    assert run["status"] == "unknown"


def test_linked_nested_items_leave_the_message_list(conv: dict[str, Any]) -> None:
    ids = {b.get("id") for b in _blocks(conv)}
    assert cf.CM_NESTED_DONE not in ids and cf.CM_NESTED_YIELD not in ids
    texts = [b.get("text") for b in _blocks(conv) if b["type"] == "text"]
    assert any(W["reply"] in (t or "") for t in texts)


def test_legacy_history_folds_exec_outputs_too(tmp_path: Path) -> None:
    t0 = cf.CM_STARTED
    lines = [
        cf.session_meta(t0, cf.THREAD_CODE_MODE, cwd="/home/fixture/proj-cx", history_mode="legacy"),
        cf.response_item(t0, cf.message("user", cf.input_text("Go."))),
        cf.response_item(t0, cf.custom_tool_call("call_lx", "exec", "text('x');")),
        cf.response_item(t0, cf.custom_tool_call_output("call_lx", cf.exec_output("Script running with cell ID 3", "a\n"))),
        cf.response_item(t0, cf.function_call("call_lw", "wait", {"cell_id": "3"})),
        cf.response_item(t0, cf.function_call_output("call_lw", cf.exec_output("Script completed", "b\n"))),
    ]
    conv = _project(tmp_path, lines)
    (run,) = _runs(conv).values()
    assert run["status"] == "completed"
    assert "a" in run["result"] and "b" in run["result"]
    assert [b["type"] for b in _blocks(conv)] == ["text", "code_run"]


def test_the_projection_version_is_8() -> None:
    from backend.sources import codex

    assert codex.CODEX_PROJECTION_VERSION == 8
