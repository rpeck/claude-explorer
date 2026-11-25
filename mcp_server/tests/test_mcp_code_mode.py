"""Issue #90: the MCP tools and Code Mode runs.

* ``list_sessions(include_code_mode=...)``, default false: run content is
  searched only when true, as on ``/api/search``.
* ``get_messages(include_code_mode=...)``, default false: each message with
  runs has ``code_runs``. Off: a stub per run (id, name, status, line and
  call counts), with no script, call, or output text. On: the full run.
* ``get_session_outline``: ``code_run_count`` on each entry.
* ``export_session(include_code_mode=...)``: ``test_export_code_mode.py``.

All data is synthetic (``cc_code_mode_fake``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests import cc_code_mode_fake as fake

W = fake.WORDS


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from backend.tests.test_search_all_sources import close_e2e_corpus
    from backend.tests.test_search_code_mode import build_code_mode_corpus
    from mcp_server import server

    idx = build_code_mode_corpus(tmp_path, monkeypatch)
    server.reset_archive_sync_for_tests()
    try:
        yield
    finally:
        server.reset_archive_sync_for_tests()
        close_e2e_corpus(idx)


def _call(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def _uuids(result: dict[str, Any]) -> list[str]:
    return [s["uuid"] for s in result["sessions"]]


def test_list_sessions_searches_runs_only_with_include_code_mode(corpus: None) -> None:
    assert _uuids(_call("list_sessions", query=W["code"], include_code_mode=True)) == [fake.SESSION]
    assert _uuids(_call("list_sessions", query=W["code"])) == []
    assert _uuids(_call("list_sessions", query=W["code"], include_tool_calls=True)) == []


def test_get_messages_gives_a_stub_per_run_by_default(corpus: None) -> None:
    msgs = _call("get_messages", conversation_id=fake.SESSION, include_tool_results=True)
    runs = [r for m in msgs for r in m.get("code_runs", [])]
    assert [r["id"] for r in runs] == list(fake.ALL_RUNS)
    done = runs[0]
    assert done == {"id": fake.RUN_DONE, "name": "REPL", "status": "completed", "line_count": 4, "call_count": 2}
    text = json.dumps(msgs)
    for kind in ("code", "inner_output", "returned", "stdout"):
        assert W[kind] not in text, kind


def test_get_messages_gives_the_full_run_with_include_code_mode(corpus: None) -> None:
    msgs = _call("get_messages", conversation_id=fake.SESSION, include_code_mode=True)
    runs = {r["id"]: r for m in msgs for r in m.get("code_runs", [])}
    done = runs[fake.RUN_DONE]
    assert W["code"] in done["code"] and W["returned"] in done["result"]
    assert [c["name"] for c in done["inner_calls"]] == ["Bash", "Read"]
    assert done["inner_calls"][0]["link_source"] == "transcript_field"


def test_the_outline_counts_the_runs_of_each_entry(corpus: None) -> None:
    outline = _call("get_session_outline", conversation_id=fake.SESSION)
    counts = [e["code_run_count"] for e in outline["messages"]]
    assert sum(counts) == len(fake.ALL_RUNS)
    assert max(counts) == 2  # the two runs of one turn


def test_the_parameter_descriptions_name_code_mode() -> None:
    import inspect

    from mcp_server import server

    for name in ("list_sessions", "get_messages", "export_session"):
        fn = getattr(server, name)
        fn = getattr(fn, "fn", fn)
        assert "include_code_mode" in inspect.signature(fn).parameters, name
        assert inspect.signature(fn).parameters["include_code_mode"].default is False
        assert "include_code_mode: " in (fn.__doc__ or ""), name
