"""Issue #90: exports follow the Code Mode toggle.

* ``include_code_mode=True``: each run is a ``javascript`` block, its
  numbered calls, and what it returned. The fence is longer than any
  backtick run in the script.
* ``include_code_mode=False`` (the default): no run text at all.
* Markdown, the Markdown bundle, HTML (PDF), the export routes, and MCP
  ``export_session`` all agree.

All data is synthetic (``cc_code_mode_fake``).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests import cc_code_mode_fake as fake

W = fake.WORDS
RUN_WORDS = ("code", "inner_input", "inner_output", "returned", "stdout")


@pytest.fixture
def conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from backend import config
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache

    fake.write_session(tmp_path / "claude")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(tmp_path / "desktop-missing"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-missing"))
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    conv = ConversationStore().get_conversation(fake.SESSION)
    assert conv is not None
    yield conv
    clear_cache()
    invalidate_detail_dict_cache()
    config.get_settings.cache_clear()


def _markdown(conv: Any, code: bool, tools: bool = False) -> str:
    from backend.export import conversation_to_markdown, without_code_runs

    return conversation_to_markdown(conv if code else without_code_runs(conv), include_tools=tools)


@pytest.mark.parametrize("tools", [False, True], ids=["tools-off", "tools-on"])
def test_markdown_with_code_mode_has_the_script_calls_and_returned_value(conversation: Any, tools: bool) -> None:
    md = _markdown(conversation, True, tools)
    assert "```javascript" in md or "````javascript" in md
    for kind in RUN_WORDS:
        assert W[kind] in md, kind
    assert "1. `Bash`" in md and "2. `Read`" in md
    assert "Returned to the model" in md


@pytest.mark.parametrize("tools", [False, True], ids=["tools-off", "tools-on"])
def test_markdown_without_code_mode_has_no_run_text(conversation: Any, tools: bool) -> None:
    md = _markdown(conversation, False, tools)
    for kind in RUN_WORDS:
        assert W[kind] not in md, kind
    assert W["reply"] in md


def test_a_script_with_backticks_gets_a_longer_fence(conversation: Any) -> None:
    md = _markdown(conversation, True)
    # The script holds $`ls src`; a ``` fence would still be safe, but a run of
    # three backticks in a script must never close the block.
    from backend.exporters.markdown import code_run_markdown
    from backend.models import CodeRun, ContentBlock

    block = ContentBlock(type="code_run", id="r", name="REPL", code_run=CodeRun(code="const s = '```';"))
    out = code_run_markdown(block)
    assert "````javascript\nconst s = '```';\n````" in out
    assert W["code"] in md


def test_the_failed_and_refused_parts_are_in_the_markdown(conversation: Any) -> None:
    md = _markdown(conversation, True)
    assert W["error"] in md
    assert "Permission denied for Write" in md
    assert "refused" in md.lower()


def test_html_export_follows_the_toggle(conversation: Any) -> None:
    from backend.export import conversation_to_html, without_code_runs

    on = conversation_to_html(conversation, include_tools=False)
    off = conversation_to_html(without_code_runs(conversation), include_tools=False)
    assert W["code"] in on and "language-javascript" in on
    assert W["code"] not in off
    assert W["reply"] in on and W["reply"] in off


def test_the_bundle_follows_the_toggle(conversation: Any) -> None:
    from backend.export import create_markdown_bundle, without_code_runs

    def md_of(conv: Any) -> str:
        with zipfile.ZipFile(io.BytesIO(create_markdown_bundle(conv, include_tools=False))) as z:
            return next(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))

    assert W["returned"] in md_of(conversation)
    assert W["returned"] not in md_of(without_code_runs(conversation))


def test_without_code_runs_drops_messages_left_empty(conversation: Any) -> None:
    from backend.export import without_code_runs

    stripped = without_code_runs(conversation)
    assert not any(b.type == "code_run" for m in stripped.messages for b in m.content)
    assert all(m.content or m.text for m in stripped.messages)
    assert len(stripped.messages) < len(conversation.messages)


def test_the_markdown_route_takes_include_code_mode(conversation: Any) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    url = f"/api/conversations/{fake.SESSION}/export/markdown"
    on = client.get(url, params={"include_code_mode": "true"})
    off = client.get(url)
    assert on.status_code == off.status_code == 200
    assert W["code"] in on.text and W["code"] not in off.text


def test_the_bundle_route_takes_include_code_mode(conversation: Any) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    url = f"/api/conversations/{fake.SESSION}/export/markdown-bundle"
    for flag, want in (("true", True), ("false", False)):
        r = client.get(url, params={"include_code_mode": flag})
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))
        assert (W["code"] in text) is want


def test_mcp_export_session_takes_include_code_mode(conversation: Any) -> None:
    from mcp_server import server

    fn = getattr(server.export_session, "fn", server.export_session)
    assert W["code"] in fn(conversation_id=fake.SESSION, include_code_mode=True)
    assert W["code"] not in fn(conversation_id=fake.SESSION)


def test_an_export_marks_a_call_whose_input_holds_an_earlier_output() -> None:
    from backend.export import code_run_html, code_run_markdown
    from backend.models import CodeRun, CodeRunCall, ContentBlock

    calls = [
        CodeRunCall(id="c1", name="cat", result="x = 1  # TODO a\n", code_run_parent="r", link_source="exclusive_window"),
        CodeRunCall(id="c2", name="wc", result="16", code_run_parent="r", link_source="exclusive_window", inputs_from=[0]),
    ]
    block = ContentBlock(type="code_run", id="r", name="exec", code_run=CodeRun(code="x", inner_calls=calls, result="16"))
    md = code_run_markdown(block)
    assert "2. `wc` (input from call 1)" in md
    assert "1. `cat`\n" in md
    assert "input from call 1" in code_run_html(block)
