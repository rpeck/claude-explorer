"""Issue #103: Code Mode media in the two cases that #90 left open.

Real recordings with synthetic prompts on synthetic 16x16 PNGs (see the
READMEs in ``fixtures/cc_code_mode/`` and ``fixtures/codex_code_mode/``).

* **Claude Code, one Poll result that ends two scripts.** The result content
  is [text with two delivered repl-eval events, image of event 1, image of
  event 2]; ``toolUseResult.media[i]`` holds the media of event i. Each run
  keeps only its own media. When the media cannot be split per event, no run
  of that result gets media: a wrong image is worse than none.
* **Codex, an exec output with an image.** The output is a list:
  [input_text "Script completed ...", input_image data:image/png;base64,...].
  The image goes to the run's ``media`` in the Claude Code shape. Only a
  ``data:image/<subtype>;base64,`` URL becomes media; any other URL stays
  the "[Image]" label and is never fetched.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as cf

TESTS = Path(__file__).resolve().parent
CC_FIXTURE = TESTS / "fixtures" / "cc_code_mode" / "poll-two-media-scripts.jsonl"
CODEX_FIXTURE = TESTS / "fixtures" / "codex_code_mode" / "exec-view-image.jsonl"

RUN_A = "toolu_01P1LcsiVXrokssYsDgwz5b6"  # sleeps past the 30 s limit: an error
RUN_B = "toolu_01MWvj7wuoEJFk5LKbSKRyat"  # reads media/red.png
RUN_C = "toolu_01T2eSztTZNkVBecYqzXLKNr"  # reads media/blue.png
POLL = "toolu_01XUfXcdszDmRA8hsg4PYye7"
RED = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAF0lEQVR4nGP4z8BAEiJN9aiGUQ1DSgMAkPn/Afnh+ngAAAAASUVORK5CYII="
BLUE = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAFUlEQVR4nGNgYPhPIhrVMKph2GoAAJLb/wFh5Z4RAAAAAElFTkSuQmCC"
EXEC = "call_85600ebeded647518b38a135410b5844"


def _png(data: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def _cc_lines() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CC_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cc_runs(path: Path) -> dict[str, dict[str, Any]]:
    from backend.claude_code_reader import read_claude_code_conversation

    conv = read_claude_code_conversation(path)
    assert conv is not None
    return {b["id"]: b["code_run"] for m in conv["chat_messages"] for b in m["content"] if b["type"] == "code_run"}


def _poll_entry(lines: list[dict[str, Any]]) -> dict[str, Any]:
    (entry,) = [
        e for e in lines
        if e.get("type") == "user" and isinstance(e["message"]["content"], list)
        and any(isinstance(b, dict) and b.get("tool_use_id") == POLL for b in e["message"]["content"])
    ]
    return entry


def _write_cc(tmp_path: Path, lines: list[dict[str, Any]]) -> Path:
    path = tmp_path / CC_FIXTURE.name
    path.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    return path


def test_the_recording_has_one_poll_result_that_ends_both_media_scripts() -> None:
    # A guard on the fixture itself: the case that the other tests pin.
    block = _poll_entry(_cc_lines())["message"]["content"][0]
    text = block["content"][0]["text"]
    assert text.count('kind="repl-eval"') == 2
    assert [b["type"] for b in block["content"]] == ["text", "image", "image"]


def test_each_script_ended_by_one_poll_result_keeps_only_its_own_image() -> None:
    runs = _cc_runs(CC_FIXTURE)
    assert runs[RUN_B]["media"] == [_png(RED)]
    assert runs[RUN_C]["media"] == [_png(BLUE)]
    assert runs[RUN_B]["status"] == "completed"
    assert runs[RUN_C]["status"] == "completed"


def test_the_timed_out_script_has_no_media() -> None:
    run = _cc_runs(CC_FIXTURE)[RUN_A]
    assert run["media"] == []
    assert run["status"] == "failed"


def test_media_that_cannot_be_split_per_event_goes_to_no_run(tmp_path: Path) -> None:
    lines = _cc_lines()
    entry = _poll_entry(lines)
    entry["toolUseResult"].pop("media")
    block = entry["message"]["content"][0]
    block["content"] = block["content"][:2]  # two events, but only one image block
    runs = _cc_runs(_write_cc(tmp_path, lines))
    assert runs[RUN_B]["media"] == []
    assert runs[RUN_C]["media"] == []


def test_a_poll_result_that_ends_one_script_keeps_all_its_media(tmp_path: Path) -> None:
    lines = _cc_lines()
    entry = _poll_entry(lines)
    entry["toolUseResult"].pop("media")
    block = entry["message"]["content"][0]
    text = block["content"][0]["text"]
    # Keep only the delivery of script B (eval 2).
    head = text.partition('<event nonce="V06d4fCr"')[0]
    block["content"] = [{"type": "text", "text": head.rstrip("\n").replace(" V06d4fCr", "")}, block["content"][1]]
    assert 'eval="3"' not in block["content"][0]["text"]
    runs = _cc_runs(_write_cc(tmp_path, lines))
    assert runs[RUN_B]["media"] == [_png(RED)]
    assert runs[RUN_C]["media"] == []


def test_the_shortcut_comment_on_poll_media_is_gone() -> None:
    source = (TESTS.parent / "cc_code_mode.py").read_text(encoding="utf-8")
    assert "shortcut:" not in source


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def _codex_lines() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CODEX_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _codex_runs(path: Path) -> dict[str, dict[str, Any]]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    read = read_rollout(path)
    conv = project_rollout(read.records, damaged_lines=read.damaged_lines)
    return {b["id"]: b["code_run"] for m in conv["chat_messages"] for b in m["content"] if b["type"] == "code_run"}


def _exec_output(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    (out,) = [
        line["payload"]["output"] for line in lines
        if line["type"] == "response_item" and line["payload"].get("type") == "custom_tool_call_output"
    ]
    return out


def _write_codex(tmp_path: Path, lines: list[Any], thread: str = "01a21aad-cdde-7223-8ea5-4c8121a1f03f") -> Path:
    return cf.write_rollout(tmp_path / f"rollout-2026-11-26T17-45-28-{thread}.jsonl", lines)


def _with_image_url(tmp_path: Path, url: str) -> dict[str, Any]:
    lines = _codex_lines()
    _exec_output(lines)[1]["image_url"] = url
    return _codex_runs(_write_codex(tmp_path, lines))[EXEC]


def test_a_codex_exec_image_goes_to_the_run_media() -> None:
    run = _codex_runs(CODEX_FIXTURE)[EXEC]
    assert run["media"] == [_png(RED)]
    assert run["status"] == "completed"


def test_a_codex_run_whose_only_output_is_an_image_has_no_result_text() -> None:
    assert _codex_runs(CODEX_FIXTURE)[EXEC]["result"] is None


def test_the_view_image_call_stays_linked_to_its_run() -> None:
    (call,) = _codex_runs(CODEX_FIXTURE)[EXEC]["inner_calls"]
    assert call["name"] == "view_image"
    assert call["link_source"] == "exclusive_window"


def test_a_codex_image_with_a_web_url_stays_a_label(tmp_path: Path) -> None:
    run = _with_image_url(tmp_path, "https://example.invalid/x.png")
    assert run["media"] == []
    assert "[Image]" in run["result"]


@pytest.mark.parametrize(
    "url",
    [
        "data:text/html,<b>synthetic</b>",
        "data:text/html;base64,PGI+c3ludGhldGljPC9iPg==",
        "data:image/png,not-base64",
    ],
)
def test_only_a_base64_image_data_url_becomes_media(tmp_path: Path, url: str) -> None:
    run = _with_image_url(tmp_path, url)
    assert run["media"] == []
    assert "[Image]" in run["result"]


def test_text_and_image_in_one_codex_output_both_stay(tmp_path: Path) -> None:
    lines = _codex_lines()
    _exec_output(lines)[0]["text"] += "synthetic-hello\n"
    run = _codex_runs(_write_codex(tmp_path, lines))[EXEC]
    assert run["result"] == "synthetic-hello"
    assert run["media"] == [_png(RED)]


def test_an_image_in_a_wait_output_goes_to_the_run_of_its_cell(tmp_path: Path) -> None:
    lines = copy.deepcopy(cf.code_mode_lines())
    (wait_out,) = [
        line for line in lines
        if isinstance(line, dict) and line.get("payload", {}).get("type") == "function_call_output"
        and line["payload"].get("call_id") == cf.CM_WAIT
    ]
    wait_out["payload"]["output"] = [
        {"type": "input_text", "text": cf.exec_output("Script completed", "")},
        {"type": "input_image", "image_url": f"data:image/png;base64,{BLUE}", "detail": "high"},
    ]
    runs = _codex_runs(_write_codex(tmp_path, lines, cf.THREAD_CODE_MODE))
    assert runs[cf.CM_YIELD]["media"] == [_png(BLUE)]
    assert runs[cf.CM_YIELD]["status"] == "completed"
    assert all(runs[r]["media"] == [] for r in cf.CM_RUNS if r != cf.CM_YIELD)


def test_every_codex_run_without_an_image_has_empty_media(tmp_path: Path) -> None:
    runs = _codex_runs(_write_codex(tmp_path, cf.code_mode_lines(), cf.THREAD_CODE_MODE))
    assert set(runs) == set(cf.CM_RUNS)
    assert all(run["media"] == [] for run in runs.values())


def test_the_codex_projection_version_is_6() -> None:
    from backend.sources import codex

    assert codex.CODEX_PROJECTION_VERSION == 6
