"""L2 (issue #91, part 2): the peer graph routes of the Messages tab.

The contract (orchestrator, 2026-11-27):

* ``GET /api/conversations/{uuid}/peers?hops=<1..5|all>&subagents=<bool>``
  and ``GET /api/peers/active?subagents=<bool>`` return a ``PeerGraph``:
  ``scope`` ("hops" or "active"), ``center_key``, ``hops`` (None = "all"),
  ``subagents``, ``sessions``, ``messages``.
* A session: ``key`` (``index_key(uuid, agent_id)``; "name:<peer name>" for a
  peer that is not in the archive), ``uuid``, ``agent_id``, ``title``, ``name``,
  ``source``, ``project_name``, ``project_path``, ``state``, ``updated_at``,
  ``in_archive``, ``hop`` (0 = the center; None in the active scope),
  ``via_key`` (the BFS parent), ``derived_parent_key``.
* A message: ``msg_id``, ``kind`` ("message", "idle_notice", "subagent"),
  ``from_key``, ``to_key``, ``from_message_uuid``, ``to_message_uuid``,
  ``timestamp``, ``text`` (the body).
* Hops: BFS over message edges in both directions; default 2; "all" is the
  whole connected group; 0, 6, or "x" -> 422; an unknown center -> 404. Every
  message between two sessions in scope is in the response.
* subagents=false: an agent's messages count for its session. subagents=true:
  agent nodes, "subagent" edges from the session to each agent, and BFS
  crosses them.
* derived_parent_key: the other side of the session's first peer message, when
  the session received it.
* Active: every running or open session (also with no messages), and only the
  messages whose two ends are both active.
* Also pinned here: the messages come in time order.

The corpus is ``peer_tree_fake`` (the 60-session tree, synthetic).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from backend.tests import peer_tree_fake as f

GONE_KEY = f"name:{f.GONE_NAME}"
AGENT_KEY = f"{f.S1}::agent::{f.AGENT_ID}"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[f.Tree]:
    with f.use_tree(tmp_path, monkeypatch) as t:
        yield t


def _client() -> Any:
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


def graph(center: str | None = None, **params: Any) -> dict[str, Any]:
    url = f"/api/conversations/{center}/peers" if center else "/api/peers/active"
    r = _client().get(url, params={k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()})
    assert r.status_code == 200, r.text
    return r.json()


def keys(g: dict[str, Any]) -> set[str]:
    return {s["key"] for s in g["sessions"]}


def session(g: dict[str, Any], key: str) -> dict[str, Any]:
    found = [s for s in g["sessions"] if s["key"] == key]
    assert len(found) == 1, f"{key}: {len(found)} sessions"
    return found[0]


def pairs(g: dict[str, Any]) -> list[tuple[str, str]]:
    return [(m["from_key"], m["to_key"]) for m in g["messages"]]


def hops_of(g: dict[str, Any]) -> dict[str, int]:
    return {s["key"]: s["hop"] for s in g["sessions"]}


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def test_the_default_is_two_hops_around_the_center(tree: f.Tree) -> None:
    g = graph(f.W11)
    assert (g["scope"], g["center_key"], g["hops"], g["subagents"]) == ("hops", f.W11, 2, False)


@pytest.mark.parametrize("hops", ["0", "6", "x"])
def test_hops_outside_one_to_five_or_all_gives_422(tree: f.Tree, hops: str) -> None:
    r = _client().get(f"/api/conversations/{f.W11}/peers", params={"hops": hops})
    assert r.status_code == 422, r.text


def test_an_unknown_center_gives_404(tree: f.Tree) -> None:
    assert _client().get(f"/api/conversations/{f.W11}/peers").status_code == 200  # the route exists
    r = _client().get("/api/conversations/99999999-0000-4000-8000-000000000000/peers")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Hops
# ---------------------------------------------------------------------------


def test_the_center_has_hop_0_and_no_via(tree: f.Tree) -> None:
    s = session(graph(f.W11, hops=1), f.W11)
    assert (s["hop"], s["via_key"]) == (0, None)


def test_one_hop_from_a_worker_is_its_sub_and_its_peer_that_is_gone(tree: f.Tree) -> None:
    assert hops_of(graph(f.W11, hops=1)) == {f.W11: 0, f.S1: 1, GONE_KEY: 1}


def test_two_hops_from_a_worker_add_the_coordinator_and_the_sibling_workers(tree: f.Tree) -> None:
    g = graph(f.W11, hops=2)
    expected = {f.W11: 0, f.S1: 1, GONE_KEY: 1, f.CO: 2, **{w: 2 for w in f.WORKERS[0][1:]}}
    assert hops_of(g) == expected


def test_one_hop_from_the_coordinator_is_every_session_it_messaged(tree: f.Tree) -> None:
    g = graph(f.CO, hops=1)
    assert keys(g) == {f.CO, *f.SUBS, *f.DIRECT, f.X_ENDED}


def test_a_hop_2_session_names_the_hop_1_session_that_reaches_it(tree: f.Tree) -> None:
    g = graph(f.CO, hops=2)
    for i, sub in enumerate(f.SUBS):
        assert session(g, sub)["via_key"] == f.CO
        for w in f.WORKERS[i]:
            s = session(g, w)
            assert (s["hop"], s["via_key"]) == (2, sub), f.NAMES[w]


def test_three_hops_from_a_worker_do_not_reach_the_workers_of_other_subs(tree: f.Tree) -> None:
    g = graph(f.W11, hops=3)
    assert f.SUBS[1] in keys(g)
    assert not set(f.WORKERS[1]) & keys(g)


def test_four_hops_from_a_worker_reach_the_whole_tree(tree: f.Tree) -> None:
    g = graph(f.W11, hops=4)
    assert set(f.TREE) <= keys(g)
    assert hops_of(g)[f.WORKERS[1][0]] == 4


def test_all_is_the_whole_connected_group(tree: f.Tree) -> None:
    g = graph(f.W11, hops="all")
    assert g["hops"] is None
    assert keys(g) == {*f.TREE, f.X_ENDED, GONE_KEY}
    assert keys(g) == keys(graph(f.W11, hops=5))


def test_every_message_between_two_sessions_in_scope_is_included(tree: f.Tree) -> None:
    g = graph(f.W11, hops=2)
    got = pairs(g)
    # The sub and the coordinator are both in scope.
    assert (f.CO, f.S1) in got and (f.S1, f.CO) in got
    # Between two hop-2 sessions: the sibling worker and its sub (hop 1) both ways.
    assert (f.S1, f.W12) in got and (f.W12, f.S1) in got
    # A session out of scope brings no message.
    assert (f.CO, f.SUBS[1]) not in got
    in_scope = keys(g)
    assert all(a in in_scope and b in in_scope for a, b in got)


def test_one_hop_from_a_worker_has_its_three_messages(tree: f.Tree) -> None:
    assert sorted(pairs(graph(f.W11, hops=1))) == sorted([(f.S1, f.W11), (f.W11, f.S1), (f.W11, GONE_KEY)])


# ---------------------------------------------------------------------------
# Sessions and messages
# ---------------------------------------------------------------------------


def test_a_session_has_its_list_fields(tree: f.Tree) -> None:
    titles = {r["uuid"]: r["name"] for r in _client().get("/api/conversations").json()}
    s = session(graph(f.CO, hops=1), f.S1)
    assert s["uuid"] == f.S1
    assert s["agent_id"] is None
    assert s["title"] == titles[f.S1]
    assert s["name"] == f.NAMES[f.S1]
    assert s["source"] == "CLAUDE_CODE"
    assert s["project_path"] == f.CWD
    assert s["project_name"] == "orchestra"
    assert s["state"] == "open"
    assert s["in_archive"] is True
    assert s["updated_at"]


def test_a_peer_that_is_not_in_the_archive_is_one_session_with_in_archive_false(tree: f.Tree) -> None:
    s = session(graph(f.W11, hops=1), GONE_KEY)
    assert s["in_archive"] is False
    assert s["name"] == f.GONE_NAME
    assert s["uuid"] is None
    assert s["state"] is None


def test_a_message_has_its_two_ends_its_body_and_its_time(tree: f.Tree) -> None:
    m = tree.msg(f.CO, f.S1)
    got = [x for x in graph(f.CO, hops=1)["messages"] if x["msg_id"] == m.msg_id]
    assert len(got) == 1
    x = got[0]
    assert (x["kind"], x["from_key"], x["to_key"], x["text"]) == ("message", f.CO, f.S1, m.body)
    assert x["to_message_uuid"] == m.recv_uuid
    assert datetime.fromisoformat(x["timestamp"])


def test_the_from_message_uuid_is_the_message_that_holds_the_send(tree: f.Tree) -> None:
    from backend.store import invalidate_detail_dict_cache

    m = tree.msg(f.CO, f.S1)
    x = next(x for x in graph(f.CO, hops=1)["messages"] if x["msg_id"] == m.msg_id)
    invalidate_detail_dict_cache()
    detail = _client().get(f"/api/conversations/{f.CO}").json()
    holder = [
        d for d in detail["messages"]
        if any(b.get("type") == "tool_use" and b.get("id") == m.tool_id for b in d.get("content") or [])
    ]
    assert [d["uuid"] for d in holder] == [x["from_message_uuid"]]


def test_a_message_to_a_peer_that_is_gone_has_no_receiver_uuid(tree: f.Tree) -> None:
    x = next(x for x in graph(f.W11, hops=1)["messages"] if x["to_key"] == GONE_KEY)
    assert x["to_message_uuid"] is None
    assert x["text"] == "Synthetic message to a gone peer."


def test_the_messages_come_in_time_order(tree: f.Tree) -> None:
    stamps = [x["timestamp"] for x in graph(f.CO, hops="all")["messages"]]
    assert all(stamps)
    assert stamps == sorted(stamps)


def test_the_derived_parent_is_the_sender_of_the_first_peer_message(tree: f.Tree) -> None:
    g = graph(f.CO, hops="all")
    parent = {s["key"]: s["derived_parent_key"] for s in g["sessions"]}
    assert parent[f.CO] is None  # it sent its first peer message
    for i, sub in enumerate(f.SUBS):
        assert parent[sub] == f.CO
        for w in f.WORKERS[i]:
            assert parent[w] == sub, f.NAMES[w]
    for d in [*f.DIRECT, f.X_ENDED]:
        assert parent[d] == f.CO


# ---------------------------------------------------------------------------
# Subagent edges
# ---------------------------------------------------------------------------


def test_without_subagents_an_agent_message_counts_for_its_session(tree: f.Tree) -> None:
    g = graph(f.W15, hops=1)
    assert AGENT_KEY not in keys(g)
    agent_msg = next(m for m in tree.messages if m.agent)
    x = next(x for x in g["messages"] if x["msg_id"] == agent_msg.msg_id)
    assert (x["from_key"], x["to_key"]) == (f.S1, f.W15)
    assert not [x for x in g["messages"] if x["kind"] == "subagent"]


def test_with_subagents_the_agent_is_a_session_with_a_subagent_edge(tree: f.Tree) -> None:
    g = graph(f.S1, hops=1, subagents=True)
    assert g["subagents"] is True
    agent = session(g, AGENT_KEY)
    assert (agent["uuid"], agent["agent_id"], agent["hop"]) == (f.S1, f.AGENT_ID, 1)
    edges = [x for x in g["messages"] if x["kind"] == "subagent"]
    assert [(x["from_key"], x["to_key"]) for x in edges] == [(f.S1, AGENT_KEY)]


def test_with_subagents_the_agent_message_starts_at_the_agent(tree: f.Tree) -> None:
    g = graph(f.W15, hops=2, subagents=True)
    agent_msg = next(m for m in tree.messages if m.agent)
    x = next(x for x in g["messages"] if x["msg_id"] == agent_msg.msg_id)
    assert (x["from_key"], x["to_key"]) == (AGENT_KEY, f.W15)
    assert AGENT_KEY in keys(g)


# ---------------------------------------------------------------------------
# The active scope
# ---------------------------------------------------------------------------


def test_the_active_scope_lists_every_running_or_open_session(tree: f.Tree) -> None:
    g = graph()
    assert (g["scope"], g["center_key"], g["hops"]) == ("active", None, None)
    assert keys(g) == {*f.TREE, f.X_RESUMED, f.X_RUN}
    assert {s["hop"] for s in g["sessions"]} == {None}
    assert {s["state"] for s in g["sessions"]} == {"open", "running"}


def test_the_active_scope_holds_an_active_session_with_no_messages(tree: f.Tree) -> None:
    g = graph()
    assert f.X_RESUMED in keys(g)
    assert not [p for p in pairs(g) if f.X_RESUMED in p]


def test_the_active_scope_keeps_only_messages_between_active_sessions(tree: f.Tree) -> None:
    g = graph()
    active = keys(g)
    assert all(a in active and b in active for a, b in pairs(g))
    assert not [p for p in pairs(g) if f.X_ENDED in p or GONE_KEY in p]
    tree_msgs = [m for m in tree.messages if m.to is not None and m.to != f.X_ENDED and m.frm != f.X_ENDED]
    assert sorted(x["msg_id"] for x in g["messages"]) == sorted(m.msg_id for m in tree_msgs)
