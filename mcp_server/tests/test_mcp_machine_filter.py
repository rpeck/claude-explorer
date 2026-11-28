"""A2 (issue #92): the MCP ``list_sessions`` machine filter and fields.

- ``list_sessions(machine=...)`` keeps the sessions whose ``machine_id`` or
  shown ``machine_label`` equals the value, case-insensitive.
- Each session in the output has ``machine_id`` and ``machine_label``.
- Still five tools (``test_server_metadata.py``).

Machine B's sessions reach machine A through a bundle. Synthetic data only.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import World, make_world

S_A = "a2a9a9a9-0000-4000-8000-00000000000a"
S_B = "a2a9a9a9-0000-4000-8000-00000000000b"
WORD = "mcpmachineword"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    from backend import machines
    from mcp_server import server

    with make_world(tmp_path, monkeypatch) as w:
        w.a.cc_session(S_A, f"a {WORD}")
        w.a.sync()
        w.a.activate()
        machines.set_machine_label("Home Mac")
        w.b.cc_session(S_B, f"b {WORD}")
        w.b.sync()
        w.b.activate()
        machines.set_machine_label("Work laptop")
        w.b.export_all()
        dest = tmp_path / "b.cebundle"
        w.b.export(dest)
        w.a.import_(dest)
        w.a.activate()
        monkeypatch.setattr(server, "_store", None)
        monkeypatch.setattr(server, "_settings", None)
        server.reset_archive_sync_for_tests()
        try:
            yield w
        finally:
            server.reset_archive_sync_for_tests()


def _list(**kw: Any) -> dict[str, Any]:
    from mcp_server import server

    fn = server.list_sessions
    return getattr(fn, "fn", fn)(**kw)


def test_every_session_carries_its_machine(world: World) -> None:
    sessions = {s["uuid"]: s for s in _list(source="CLAUDE_CODE")["sessions"]}

    assert sessions[S_A]["machine_id"] == world.a.machine_id
    assert sessions[S_A]["machine_label"] == "Home Mac"
    assert sessions[S_B]["machine_id"] == world.b.machine_id
    assert sessions[S_B]["machine_label"] == "Work laptop"


@pytest.mark.parametrize("value", ["Work laptop", "work LAPTOP"])
def test_the_machine_filter_matches_the_label_in_any_case(
    world: World, value: str
) -> None:
    out = _list(machine=value)

    assert [s["uuid"] for s in out["sessions"]] == [S_B]
    assert out["total"] == 1


def test_the_machine_filter_matches_the_machine_id(world: World) -> None:
    out = _list(machine=world.a.machine_id)

    assert [s["uuid"] for s in out["sessions"]] == [S_A]


def test_the_machine_filter_applies_to_a_search(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import search_index as si

    idx = si.SearchIndex(world.tmp / "mcp-index.sqlite")
    si.build_full_index(world.a.store(), index=idx)
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        both = {s["uuid"] for s in _list(query=WORD)["sessions"]}
        only_b = [
            s["uuid"] for s in _list(query=WORD, machine="Work laptop")["sessions"]
        ]
    finally:
        idx.close()

    assert both == {S_A, S_B}
    assert only_b == [S_B]


def test_an_unknown_machine_matches_nothing(world: World) -> None:
    out = _list(machine="No such machine")

    assert out["sessions"] == []
    assert out["total"] == 0
