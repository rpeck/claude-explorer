"""Synthetic Claude Code corpus for L2 (issue #91, part 2): the Messages tab.

The 60-session orchestrator tree in three levels:

* level 0: one coordinator (``CO``);
* level 1: five sub-orchestrators (``SUBS``) and four direct workers (``DIRECT``);
* level 2: ten workers under each sub-orchestrator (``WORKERS[i]``).

Each parent sends one message to each child (the child's first peer message,
so the derived parent is the sender), and each child replies. Extras:

* ``W11`` (the first worker of the first sub) also sends to a peer that is not
  in the archive (``GONE_NAME``).
* An agent of the first sub (``AGENT_ID``) sends one message to ``W15``.
* Sessions for the state rules (``X_*``), and a live session registry with a
  live pid, a dead pid, and a file that does not parse.

The record shapes are the L1 shapes (Claude Code 2.1.331, see
``test_peer_messages.py``). Every value is synthetic. The tree sessions are less
than one day old, so their state is "open".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid as uuidlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

PROJECT = "-home-fixture-orchestra"
CWD = "/home/fixture/orchestra"
MODEL = "claude-sonnet-5-5"
_NS = uuidlib.UUID("5e55e55e-0000-4000-8000-00000000f00d")


def sid(prefix: str, n: int) -> str:
    return f"{prefix}-0000-4000-8000-{n:012d}"


CO = sid("c0000000", 1)
SUBS = [sid("5b000000", i) for i in range(1, 6)]
DIRECT = [sid("d0000000", j) for j in range(1, 5)]
WORKERS = [[sid("a0000000", i * 100 + j) for j in range(1, 11)] for i in range(1, 6)]
TREE = [CO, *SUBS, *DIRECT, *(w for ws in WORKERS for w in ws)]
W11, W12, W15 = WORKERS[0][0], WORKERS[0][1], WORKERS[0][4]
S1 = SUBS[0]

X_ENDED = sid("e0000000", 1)  # messaged by CO, replied, then a cost-state: Ended
X_RESUMED = sid("e0000000", 2)  # cost-state, then a resumed turn: Open
X_OLD = sid("e0000000", 3)  # last record 10 days old, no end record: Ended (no exit record)
X_CONT = sid("e0000000", 4)  # a continued-in record after the last turn: Ended
X_RUN = sid("e0000000", 5)  # a cost-state, but in the registry with a live pid: Running
X_DEAD = sid("e0000000", 6)  # a cost-state, in the registry with a dead pid: Ended
EXTRAS = [X_ENDED, X_RESUMED, X_OLD, X_CONT, X_RUN, X_DEAD]

AGENT_ID = "s1helper"
GONE_NAME = "fixture-gone"


def _name(s: str) -> str:
    if s == CO:
        return "fixture-coordinator"
    if s in SUBS:
        return f"fixture-sub-{SUBS.index(s) + 1}"
    if s in DIRECT:
        return f"fixture-direct-{DIRECT.index(s) + 1}"
    for i, ws in enumerate(WORKERS):
        if s in ws:
            return f"fixture-worker-{i + 1}-{ws.index(s) + 1}"
    return f"fixture-extra-{EXTRAS.index(s) + 1}"


NAMES = {s: _name(s) for s in [*TREE, *EXTRAS]}
TITLES = {s: f"Orchestra: {NAMES[s]}" for s in NAMES}
PIDS = {s: 30000 + k for k, s in enumerate(NAMES)}


def sock(s: str) -> str:
    return f"uds:/tmp/cc-socks/{PIDS[s]}.sock"


@dataclass
class Msg:
    """One message between two sessions. ``to`` is None for the peer that is gone."""

    frm: str
    to: str | None
    msg_id: str
    body: str
    tool_id: str
    agent: str | None = None
    recv_uuid: str | None = None


class Clock:
    """One clock for every session, so a send comes before its receive."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def tick(self) -> str:
        self.t += timedelta(seconds=1)
        return self.t.isoformat().replace("+00:00", "Z")


@dataclass
class Transcript:
    sid: str
    clock: Clock
    agent: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)
    n: int = 0
    last: str | None = None
    last_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.agent is None:
            self.lines.append({"type": "custom-title", "customTitle": TITLES[self.sid], "sessionId": self.sid})
            self.lines.append({"type": "agent-name", "agentName": NAMES[self.sid], "sessionId": self.sid})

    def _base(self, type_: str, **kw: Any) -> dict[str, Any]:
        self.n += 1
        uid = str(uuidlib.uuid5(_NS, f"{self.sid}/{self.agent}/{self.n}"))
        rec = {
            "parentUuid": self.last,
            "isSidechain": self.agent is not None,
            "type": type_,
            "uuid": uid,
            "timestamp": self.clock.tick(),
            "userType": "external",
            "entrypoint": "sdk-cli",
            "cwd": CWD,
            "sessionId": self.sid,
            "version": "2.1.331",
            **({"agentId": self.agent} if self.agent else {}),
            **kw,
        }
        self.lines.append(rec)
        self.last = uid
        self.last_time = self.clock.t
        return rec

    def prompt(self, text: str) -> str:
        return self._base("user", message={"role": "user", "content": text}, promptSource="sdk", turnOrigin="sdk")["uuid"]

    def text(self, text: str) -> str:
        return self._base(
            "assistant",
            message={"id": f"msg_{self.n + 1}", "role": "assistant", "model": MODEL, "type": "message",
                     "content": [{"type": "text", "text": text}]},
        )["uuid"]

    def send(self, m: Msg, to_name: str) -> str:
        """A SendMessage call and its result row. Return the call's record uuid."""
        tool_input = {"to": to_name, "summary": m.body[:20], "message": m.body, "type": "message",
                      "recipient": to_name, "recipient_kind": "name", "content": m.body}
        call = self._base(
            "assistant",
            message={"id": f"msg_{self.n + 1}", "role": "assistant", "model": MODEL, "type": "message",
                     "content": [{"type": "tool_use", "id": m.tool_id, "name": "SendMessage",
                                  "input": tool_input, "caller": {"type": "direct"}}]},
        )["uuid"]
        result = {"success": True, "message": f"“{m.body[:20]}” → {to_name}",
                  "display": f"“{m.body[:20]}” → sent to {to_name}", "msg_id": m.msg_id}
        self._base(
            "user",
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.tool_id,
                                                   "content": json.dumps(result), "is_error": False}]},
            toolUseResult=result,
            sourceToolAssistantUUID=call,
        )
        return call

    def receive(self, m: Msg, from_name: str, from_sock: str) -> str:
        tag = (f'<cross-session-message from="{from_sock}" from-name="{from_name}" from-mode="prompting">\n'
               f"{m.body}\n</cross-session-message>")
        origin = {"kind": "peer", "from": from_sock, "verifiedPeerPid": int(from_sock.rsplit("/", 1)[1].split(".")[0]),
                  "msg_id": m.msg_id, "name": from_name, "fromMode": "prompting", "body": m.body, "verifiedLocal": True}
        return self._base(
            "user",
            message={"role": "user", "content": f"Another Claude session sent a message:\n{tag}"},
            isMeta=True, origin=origin, promptSource="system", turnOrigin="peer", queueSkipSlashCommands=True,
        )["uuid"]

    def cost_state(self) -> None:
        self.lines.append({"type": "cost-state", "sessionId": self.sid, "totalCostUSD": 0.01, "totalAPIDuration": 1000,
                           "totalDuration": 60000, "startTime": 1764000000000, "modelUsage": {}})

    def continued_in(self, other: str) -> None:
        self.lines.append({"type": "continued-in", "timestamp": self.clock.tick(), "sessionId": self.sid,
                           "continuedInSessionId": other})

    def write(self, projects: Path) -> Path:
        if self.agent:
            path = projects / PROJECT / self.sid / "subagents" / f"agent-{self.agent}.jsonl"
        else:
            path = projects / PROJECT / f"{self.sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in self.lines), encoding="utf-8")
        if self.last_time:
            os.utime(path, (self.last_time.timestamp(), self.last_time.timestamp()))
        return path


@dataclass
class Tree:
    claude_dir: Path
    now: datetime
    messages: list[Msg]
    #: The SendMessage call ids, by msg_id.
    send_tool: dict[str, str]

    def msg(self, frm: str, to: str | None) -> Msg:
        found = [m for m in self.messages if m.frm == frm and m.to == to and m.agent is None]
        assert found, f"no message {frm} -> {to}"
        return found[0]


def dead_pid() -> int:
    """The pid of a process that has ended."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def build_tree(claude_dir: Path, now: datetime | None = None) -> Tree:
    """Write the tree, the extras, and the registry under ``claude_dir``."""
    now = now or datetime.now(UTC)
    clock = Clock(now - timedelta(hours=3))
    ts = {s: Transcript(s, clock) for s in [*TREE, *EXTRAS] if s != X_OLD}
    messages: list[Msg] = []
    send_tool: dict[str, str] = {}
    k = 0

    def exchange(parent: str, child: str) -> None:
        nonlocal k
        for frm, to, body in ((parent, child, f"Task for {NAMES[child]}."), (child, parent, f"Done: {NAMES[child]}.")):
            k += 1
            m = Msg(frm, to, str(uuidlib.uuid5(_NS, f"msg/{frm}/{to}/{k}")), body, f"toolu_l2_{k:04d}")
            ts[frm].send(m, NAMES[to])
            m.recv_uuid = ts[to].receive(m, NAMES[frm], sock(frm))
            ts[to].text("Read it.")
            messages.append(m)
            send_tool[m.msg_id] = m.tool_id

    for s, t in ts.items():
        if s in (CO, *EXTRAS):
            t.prompt(f"Start: {NAMES[s]}.\nSecond line of the prompt.")
            t.text("Starting.")
    for child in [*SUBS, *DIRECT]:
        exchange(CO, child)
    exchange(CO, X_ENDED)
    for i, sub in enumerate(SUBS):
        for w in WORKERS[i]:
            exchange(sub, w)

    # The first worker writes to a peer that is not in the archive.
    k += 1
    gone = Msg(W11, None, str(uuidlib.uuid5(_NS, "msg/gone")), "Synthetic message to a gone peer.", "toolu_l2_gone")
    ts[W11].send(gone, GONE_NAME)
    messages.append(gone)
    send_tool[gone.msg_id] = gone.tool_id

    # An agent of the first sub writes to W15.
    agent = Transcript(S1, clock, agent=AGENT_ID)
    agent.prompt("Help the sub: tell worker 5 about the schema.")
    am = Msg(S1, W15, str(uuidlib.uuid5(_NS, "msg/agent")), "Synthetic note from the agent of sub 1.",
             "toolu_l2_agent", agent=AGENT_ID)
    agent.send(am, NAMES[W15])
    am.recv_uuid = ts[W15].receive(am, "fixture-sub-1-helper", "uds:/tmp/cc-socks/39999.sock")
    ts[W15].text("Noted.")
    messages.append(am)
    send_tool[am.msg_id] = am.tool_id

    # The state extras.
    ts[X_ENDED].cost_state()
    ts[X_RESUMED].cost_state()
    ts[X_RESUMED].prompt("Resume: one more thing.")
    ts[X_RESUMED].text("Done again.")
    ts[X_CONT].continued_in(sid("e0000000", 99))
    ts[X_RUN].cost_state()
    ts[X_DEAD].cost_state()
    old = Transcript(X_OLD, Clock(now - timedelta(days=10)))
    old.prompt("Start: an old session.")
    old.text("Starting long ago.")

    projects = claude_dir / "projects"
    for t in [*ts.values(), old, agent]:
        t.write(projects)

    reg = claude_dir / "sessions"
    reg.mkdir(parents=True, exist_ok=True)
    started = int((now - timedelta(hours=3)).timestamp() * 1000)
    for s, pid in ((X_RUN, os.getpid()), (X_DEAD, dead_pid())):
        (reg / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "sessionId": s, "cwd": CWD, "startedAt": started, "version": "2.1.331",
            "kind": "interactive", "entrypoint": "cli", "name": NAMES[s], "status": "idle",
            "updatedAt": started, "statusUpdatedAt": started,
        }), encoding="utf-8")
    (reg / "77777.json").write_text('{"pid": 77777, "sessionId": "cut', encoding="utf-8")
    return Tree(claude_dir, now, messages, send_tool)


# ---------------------------------------------------------------------------
# The environment (the same steps as ``test_peer_messages.use_corpus``)
# ---------------------------------------------------------------------------


def _reset_caches() -> None:
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    si.reset_search_index_for_tests()
    invalidate_detail_dict_cache()
    clear_cache()
    config.get_settings.cache_clear()


@contextmanager
def use_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tree]:
    """Write the corpus, point the settings at it, and index it as the singleton."""
    from backend import search_index as si
    from backend.store import ConversationStore

    root = tmp_path / "orchestra"
    (root / "data").mkdir(parents=True)
    tree = build_tree(root / "claude")
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(root / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "search-index.sqlite")
    _reset_caches()
    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield tree
    finally:
        idx.close()
        _reset_caches()
