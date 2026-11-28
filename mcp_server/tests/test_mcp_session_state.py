"""L2 (issue #91, part 2): session state in the MCP ``list_sessions`` tool.

The contract (orchestrator, 2026-11-27):

* Each session in ``list_sessions`` gets ``"state"``: "running", "open",
  "ended", "ended_no_exit", or null (Desktop).
* A new ``state`` argument: one of those four, or "active" (running or open).
  Any other value raises ``ToolError`` with a text that lists the valid values.
* Still five tools (pinned by ``test_server_metadata.py``).

The corpus is ``backend/tests/peer_tree_fake`` (synthetic).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests import peer_tree_fake as f


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[f.Tree]:
    from mcp_server import server

    with f.use_tree(tmp_path, monkeypatch) as t:
        server.reset_archive_sync_for_tests()
        try:
            yield t
        finally:
            server.reset_archive_sync_for_tests()


def _list(**kw: Any) -> dict[str, Any]:
    from mcp_server import server

    fn = server.list_sessions
    return getattr(fn, "fn", fn)(limit=100, **kw)


def _states(**kw: Any) -> dict[str, Any]:
    return {s["uuid"]: s.get("state", "<missing>") for s in _list(**kw)["sessions"]}


def test_each_session_has_its_state(tree: f.Tree) -> None:
    states = _states()
    assert states[f.X_ENDED] == "ended"
    assert states[f.X_RESUMED] == "open"
    assert states[f.X_OLD] == "ended_no_exit"
    assert states[f.X_RUN] == "running"
    assert states[f.CO] == "open"


def test_state_running_lists_only_the_running_session(tree: f.Tree) -> None:
    assert _states(state="running") == {f.X_RUN: "running"}


def test_state_ended_lists_the_sessions_with_an_end_record(tree: f.Tree) -> None:
    assert set(_states(state="ended")) == {f.X_ENDED, f.X_CONT, f.X_DEAD}


def test_state_ended_no_exit_lists_the_old_session(tree: f.Tree) -> None:
    assert set(_states(state="ended_no_exit")) == {f.X_OLD}


def test_state_active_lists_the_running_and_open_sessions(tree: f.Tree) -> None:
    got = _list(state="active")
    assert got["total"] == len(f.TREE) + 2
    assert {s["state"] for s in got["sessions"]} == {"open", "running"}
    assert f.X_RUN in {s["uuid"] for s in got["sessions"]}


def test_state_active_also_works_with_a_query(tree: f.Tree) -> None:
    # The query also matches the coordinator, whose message names fixture-extra-1.
    got = _states(state="active", query="fixture-extra")
    assert {f.X_RESUMED, f.X_RUN} <= set(got)
    assert set(got.values()) <= {"open", "running"}
    assert f.X_ENDED not in got


@pytest.mark.parametrize("value", ["closed", "Running!", "all"])
def test_an_unknown_state_is_a_tool_error_that_lists_the_valid_values(tree: f.Tree, value: str) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        _list(state=value)
    text = str(info.value)
    for valid in ("running", "open", "ended", "ended_no_exit", "active"):
        assert valid in text
