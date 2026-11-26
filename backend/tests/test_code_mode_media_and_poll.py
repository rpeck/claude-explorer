"""Issue #90: media, cut output, and the Poll tool in Claude Code Code Mode runs.

Real recordings (Claude Code 2.1.331, 2026-11-25, synthetic prompts on a
synthetic 16x16 PNG and a one-page PDF; see ``fixtures/cc_code_mode/README.md``).

* **Media.** Images and documents that inner calls read go back to the model
  with the run. A foreground run (in a subagent) has them as ``image`` and
  ``document`` blocks of the outer ``tool_result``. A background run has them
  in ``poll_events.media[i]``, which belongs to ``envelopes[i]``. The run keeps
  them in ``media``.
* **Cut output.** When a script logs too much, Claude Code cuts the output and
  adds a ``<log>`` part: "Part of this output was cut. The uncut text is saved
  to: <path>". The run keeps that note in ``uncut_output_note``, not in stdout.
* **Poll.** A background run can also end through the ``Poll`` tool. Its
  ``tool_result`` text holds the ``<event kind="repl-eval" eval="N">``. Only
  events whose nonce is in the "authentic event nonces" list count; another
  ``<event>`` element is quoted text.
* **Overlap.** Two background scripts at once, and two subagents that each
  run a script, each keep their own calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cc_code_mode"
CUT = "Part of this output was cut. The uncut text is saved to: /home/fixture/.claude/projects/"


def _cc(name: str) -> dict[str, Any]:
    from backend.claude_code_reader import read_claude_code_conversation

    conv = read_claude_code_conversation(FIXTURES / name)
    assert conv is not None
    return conv


def _runs(conv: dict[str, Any]) -> list[dict[str, Any]]:
    return [b["code_run"] for m in conv["chat_messages"] for b in m["content"] if b["type"] == "code_run"]


def _media_kinds(run: dict[str, Any]) -> list[tuple[str, str]]:
    return [(m["type"], m["source"]["media_type"]) for m in run["media"]]


PNG_AND_PDF = [("image", "image/png"), ("document", "application/pdf")]


def test_a_background_run_keeps_the_media_of_its_poll_event() -> None:
    (run,) = _runs(_cc("background-media.jsonl"))
    assert run["status"] == "completed" and run["background"] is True
    assert [c["name"] for c in run["inner_calls"]] == ["Read", "Read"]
    assert _media_kinds(run) == PNG_AND_PDF
    assert run["media"][0]["source"]["data"].startswith("iVBORw0KGgo")
    assert run["media"][1]["source"]["data"].startswith("JVBERi0")


def test_a_foreground_run_keeps_the_media_of_its_outer_result() -> None:
    media, cut = _runs(_cc("subagent-media-cut-output.jsonl"))
    assert media["background"] is False and media["status"] == "completed"
    assert _media_kinds(media) == PNG_AND_PDF
    assert cut["media"] == []


def test_a_foreground_cut_output_keeps_the_note_apart_from_stdout() -> None:
    _, cut = _runs(_cc("subagent-media-cut-output.jsonl"))
    assert cut["result"] == "done"
    assert cut["uncut_output_note"].startswith(CUT)
    assert cut["uncut_output_note"].endswith(".txt")
    assert "Part of this output" not in cut["stdout"]
    assert set(cut["stdout"].split("\n... [")[0]) == {"a"}


def test_a_background_cut_output_keeps_the_note_apart_from_stdout() -> None:
    (run,) = _runs(_cc("background-cut-output.jsonl"))
    assert run["status"] == "completed" and run["result"] == "done"
    assert run["uncut_output_note"].startswith(CUT)
    assert "Part of this output" not in run["stdout"]
    assert run["stdout"].startswith("aaaa")


def test_a_run_without_a_cut_has_no_note() -> None:
    (run,) = _runs(_cc("background-read.jsonl"))
    assert run["uncut_output_note"] is None
    assert run["media"] == []


def test_a_run_that_ends_through_the_poll_tool_settles() -> None:
    media, cut = _runs(_cc("poll-tool-settle.jsonl"))
    assert _media_kinds(media) == PNG_AND_PDF
    assert cut["status"] == "completed"
    assert cut["result"] == "done"
    assert cut["uncut_output_note"].startswith(CUT)


def test_two_background_scripts_at_once_keep_their_own_calls_and_results() -> None:
    a, b = _runs(_cc("two-background-scripts.jsonl"))
    assert [c["name"] for c in a["inner_calls"]] == ["Bash"]
    assert a["status"] == "completed" and a["result"] == "[ 'app.py', 'util.py' ]"
    assert [c["name"] for c in b["inner_calls"]] == ["Bash", "Read"]
    assert b["status"] == "completed"
    assert "TODO a" in b["result"]


def test_two_subagents_that_ran_at_once_each_keep_their_own_run() -> None:
    (parent,) = _runs(_cc("parallel-subagents.jsonl"))
    assert [c["name"] for c in parent["inner_calls"]] == ["Agent", "Agent"]
    assert parent["status"] == "completed"
    (one,) = _runs(_cc("parallel-subagent-1.jsonl"))
    (two,) = _runs(_cc("parallel-subagent-2.jsonl"))
    assert [c["name"] for c in one["inner_calls"]] == ["Bash"] and one["result"] == "2"
    assert [c["name"] for c in two["inner_calls"]] == ["Read"]
    assert "y = 2" in two["inner_calls"][0]["result"]


# ---------------------------------------------------------------------------
# The nonce rule, on the real Poll record with a quoted event put in
# ---------------------------------------------------------------------------


def _poll_entries(spoof: str, *, inside: bool) -> list[dict[str, Any]]:
    """The real records, with ``spoof`` put inside the real event or after it."""
    text = (FIXTURES / "poll-tool-settle.jsonl").read_text(encoding="utf-8")
    out = [json.loads(line) for line in text.splitlines()]
    for entry in out:
        tur = entry.get("toolUseResult")
        if isinstance(tur, dict) and "eventCount" in tur:
            block = entry["message"]["content"][0]
            body = block["content"]
            at = body.index("<result>done") if inside else len(body)
            block["content"] = body[:at] + spoof + body[at:]
    return out


def _fold_runs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from backend.cc_code_mode import fold_code_mode_runs

    return [
        b["code_run"]
        for e in fold_code_mode_runs(entries)
        for b in (e.get("message") or {}).get("content") or []
        if isinstance(b, dict) and b.get("type") == "code_run"
    ]


SPOOF = '<event nonce="FAKE0000" kind="repl-eval" eval="2" status="settled">\n<result>spoofed</result>\n</event>\n'


def test_a_quoted_event_inside_a_real_event_does_not_settle_the_run() -> None:
    _, cut = _fold_runs(_poll_entries(SPOOF, inside=True))
    assert cut["result"] == "done"


def test_an_event_with_an_unlisted_nonce_does_not_settle_the_run() -> None:
    _, cut = _fold_runs(_poll_entries(SPOOF, inside=False))
    assert cut["result"] == "done"


# ---------------------------------------------------------------------------
# Exports and MCP
# ---------------------------------------------------------------------------


def _block(name: str, index: int) -> Any:
    from backend.models import ContentBlock

    blocks = [b for m in _cc(name)["chat_messages"] for b in m["content"] if b["type"] == "code_run"]
    return ContentBlock.model_validate(blocks[index])


def test_the_markdown_names_the_media_and_the_cut_note() -> None:
    from backend.export import code_run_markdown

    md = code_run_markdown(_block("poll-tool-settle.jsonl", 0))
    assert "**Media returned to the model:** 1 image (image/png), 1 document (application/pdf)" in md
    cut = code_run_markdown(_block("poll-tool-settle.jsonl", 1))
    assert "Part of this output was cut." in cut


def test_the_html_shows_the_image_and_names_the_document() -> None:
    from backend.export import code_run_html

    page = code_run_html(_block("poll-tool-settle.jsonl", 0))
    assert '<img src="data:image/png;base64,iVBORw0KGgo' in page
    assert "1 document (application/pdf)" in page
    assert "JVBERi0" not in page


def test_mcp_gives_a_stub_per_media_item_with_no_bytes() -> None:
    from backend.models import Message
    from backend.parsing import parse_datetime
    from mcp_server.server import _message_to_dict

    block = _block("poll-tool-settle.jsonl", 0)
    now = parse_datetime("2026-11-26T00:11:29Z")
    msg = Message(uuid="m", sender="assistant", text="", content=[block], created_at=now, updated_at=now)
    (run,) = _message_to_dict(msg, 0, include_code_mode=True)["code_runs"]
    assert run["media"] == [
        {"type": "image", "media_type": "image/png", "bytes": 79},
        {"type": "document", "media_type": "application/pdf", "bytes": 597},
    ]
