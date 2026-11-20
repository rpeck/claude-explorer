"""N11: the history links of a Codex conversation, and the slice 2 notices.

Plan §4.4 (N10), §4.5, and the N11 row of §6.1. The contract (orchestrator,
2026-11-20):

* ``parent_uuid``: the projection's value first (``forked_from_id``, then
  the subagent spawn parent), else the thread file's ``parent_thread_id``.
  Never the conversation's own uuid.
* ``ConversationDetail.child_uuids``: the Codex conversations that the
  store finds (archived ones too) whose ``parent_uuid`` is this uuid,
  ordered by ``created_at`` and then uuid. ``[]`` for every other source.
* The Codex adapter gives ``project_rollout`` a ``load_base``, so a
  ``history_base`` whose base rollout is in the archive resolves. A
  resolved compaction, rollback, or history base gives no "incomplete:"
  notice.
* MCP ``get_session_outline``: ``parent_uuid`` and ``child_uuids`` for a
  Codex session, and neither key for another source.

Every test builds a synthetic Codex home (``backend.tests.codex_fake`` plus
the slice 2 records below); nothing reads a real ``~/.codex`` (D10).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

pytestmark = pytest.mark.xfail(strict=True, reason="N11 not implemented yet")

T0 = datetime(2026, 11, 16, 9, 0, 0, tzinfo=UTC)
CWD = "/home/fixture/proj-sigma"
MODEL = "gpt-5.5-codex"

#: A root thread with three turns, and the parent of three children.
ROOT = "019ab000-0000-7000-8000-0000000011a0"
#: Children of ROOT. The uuid order is the reverse of the created_at order.
CHILD_FIRST = "019ab000-0000-7000-8000-0000000011c3"  # forked_from_id ROOT and parent_thread_id OTHER
CHILD_FORK = (
    "019ab000-0000-7000-8000-0000000011c2"  # inherits turns 1-2 through history_base
)
CHILD_ARCHIVED = "019ab000-0000-7000-8000-0000000011c1"  # in archived_sessions/
#: A fork with a session_meta and no turn: it is not a conversation (D2).
CHILD_NO_TURNS = "019ab000-0000-7000-8000-0000000011c4"
#: A thread that is not in the Codex home.
OTHER = "019ab000-0000-7000-8000-0000000011f0"
MISSING_BASE = "019ab000-0000-7000-8000-0000000011f1"
#: A fork whose history_base names MISSING_BASE.
FORK_MISSING = "019ab000-0000-7000-8000-0000000011d0"
#: A thread whose session_meta names itself as its fork and subagent parent.
SELF_PARENT = "019ab000-0000-7000-8000-0000000011d1"
COMPACTED = "019ab000-0000-7000-8000-0000000011e0"
ROLLED_BACK = "019ab000-0000-7000-8000-0000000011e1"

DESKTOP_UUID = "2b3c4d5e-6f70-4812-9a3b-4c5d6e7f8092"


# ---------------------------------------------------------------------------
# Slice 2 records (the shapes of openai/codex at f2d3086, as in N10's tests)
# ---------------------------------------------------------------------------


class _Rollout:
    """A paginated rollout. Each line gets the next ``ordinal``.

    The ``session_meta`` of a rollout with a base has the ordinal where its
    own history starts (``history_base.end_ordinal_exclusive``).
    """

    def __init__(
        self,
        thread_id: str,
        started: datetime,
        *,
        history_base: dict[str, Any] | None = None,
        **meta: Any,
    ) -> None:
        self.thread_id = thread_id
        self.started = started
        self.clock = 0
        self.lines: list[dict[str, Any] | str] = []
        self.next = history_base["end_ordinal_exclusive"] if history_base else 0
        kwargs = {
            k: meta.pop(k)
            for k in list(meta)
            if k in ("parent_thread_id", "source", "thread_source")
        }
        line = fake.session_meta(
            started, thread_id, cwd=CWD, history_mode="paginated", **kwargs
        )
        line["payload"].update(meta)
        if history_base is not None:
            line["payload"]["history_base"] = history_base
        self.add(line)

    def tick(self) -> datetime:
        self.clock += 1
        return self.started + timedelta(seconds=self.clock)

    def add(self, line: dict[str, Any]) -> int:
        line = {**line, "ordinal": self.next}
        self.next += 1
        self.lines.append(line)
        return line["ordinal"]

    def turn(self, turn_id: str, user: str, reply: str) -> int:
        """Add one turn and return the ordinal where it starts."""
        tid = self.thread_id
        start = self.add(fake.turn_context(self.tick(), turn_id, cwd=CWD, model=MODEL))
        self.add(
            fake.item_completed(
                self.tick(), tid, turn_id, fake.user_message_item(f"{turn_id}-u", user)
            )
        )
        self.add(
            fake.item_completed(
                self.tick(),
                tid,
                turn_id,
                fake.agent_message_item(f"{turn_id}-r", reply),
            )
        )
        return start

    def base_at(self, end_ordinal_exclusive: int) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "end_ordinal_exclusive": end_ordinal_exclusive,
            "end_byte_offset": 64 * end_ordinal_exclusive,
        }

    def write(self, home: Path, *, archived: bool = False) -> Path:
        return fake.write_rollout(
            fake.rollout_path(home, self.started, self.thread_id, archived=archived),
            self.lines,
        )


def _add_history_threads(home: Path) -> None:
    root = _Rollout(ROOT, T0)
    starts = [
        root.turn(f"turn-r{n}", f"Root question {n}.", f"Root answer {n}.")
        for n in (1, 2, 3)
    ]
    root.write(home)

    first = _Rollout(
        CHILD_FIRST,
        T0 + timedelta(hours=1),
        forked_from_id=ROOT,
        parent_thread_id=OTHER,
    )
    first.turn("turn-c3", "First child question.", "First child answer.")
    first.write(home)

    fork = _Rollout(
        CHILD_FORK,
        T0 + timedelta(hours=2),
        history_base=root.base_at(starts[2]),
        forked_from_id=ROOT,
    )
    fork.turn("turn-c2", "Fork question.", "Fork answer.")
    fork.write(home)

    archived = _Rollout(CHILD_ARCHIVED, T0 + timedelta(hours=3), forked_from_id=ROOT)
    archived.turn("turn-c1", "Archived child question.", "Archived child answer.")
    archived.write(home, archived=True)

    _Rollout(CHILD_NO_TURNS, T0 + timedelta(hours=4), forked_from_id=ROOT).write(home)

    missing = _Rollout(
        FORK_MISSING,
        T0 + timedelta(hours=5),
        history_base={
            "thread_id": MISSING_BASE,
            "end_ordinal_exclusive": 4,
            "end_byte_offset": 256,
        },
        forked_from_id=MISSING_BASE,
    )
    missing.turn("turn-d0", "Orphan fork question.", "Orphan fork answer.")
    missing.write(home)

    self_parent = _Rollout(
        SELF_PARENT,
        T0 + timedelta(hours=6),
        forked_from_id=SELF_PARENT,
        parent_thread_id=SELF_PARENT,
    )
    self_parent.turn("turn-d1", "Self question.", "Self answer.")
    self_parent.write(home)

    compacted = _Rollout(COMPACTED, T0 + timedelta(hours=7))
    compacted.turn("turn-e1", "Compact question 1.", "Compact answer 1.")
    compacted.add(fake.turn_context(compacted.tick(), "turn-e2", cwd=CWD, model=MODEL))
    compacted.add(
        fake.item_completed(
            compacted.tick(),
            COMPACTED,
            "turn-e2",
            fake.user_message_item("turn-e2-u", "Compact question 2."),
        )
    )
    when = compacted.tick()
    compacted.add(
        fake.rollout_line(
            when,
            "compacted",
            {
                "message": "Summary: question 1 and answer 1.",
                "replacement_history": [
                    {
                        "item": fake.message(
                            "user", fake.input_text("REPLACEMENT CONTEXT.")
                        ),
                        "metadata": None,
                    }
                ],
            },
        )
    )
    compacted.add(
        fake.item_completed(
            when,
            COMPACTED,
            "turn-e2",
            {"type": "ContextCompaction", "id": "turn-e2-compact"},
        )
    )
    compacted.add(
        fake.item_completed(
            compacted.tick(),
            COMPACTED,
            "turn-e2",
            fake.agent_message_item("turn-e2-r", "Compact answer 2."),
        )
    )
    compacted.write(home)

    rolled = _Rollout(ROLLED_BACK, T0 + timedelta(hours=8))
    for n in (1, 2, 3):
        rolled.turn(f"turn-g{n}", f"Rolled question {n}.", f"Rolled answer {n}.")
    rolled.add(
        fake.rollout_line(
            rolled.tick(), "event_msg", {"type": "thread_rolled_back", "num_turns": 1}
        )
    )
    rolled.write(home)


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


@dataclass
class Env:
    codex: Path
    data_dir: Path
    claude_dir: Path

    def store(self) -> Any:
        from backend.store import ConversationStore

        return ConversationStore(data_dir=self.data_dir, claude_dir=self.claude_dir)

    def sync(self) -> None:
        from backend import config
        from backend.archive_sync import SyncContext
        from backend.sources.registry import get_adapter

        config.get_settings.cache_clear()
        ctx = SyncContext(
            settings=config.get_settings(), reason="cli", lock_timeout_s=10
        )
        get_adapter("CODEX", self.store()).archive_sync(ctx)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    from backend import config
    from backend import search_index as si
    from backend.sources.codex import clear_codex_caches
    from backend.store import invalidate_detail_dict_cache

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(home / ".codex")
    _add_history_threads(codex)
    data_dir = tmp_path / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(
        si, "default_index_path", lambda: tmp_path / "explorer" / "search-index.sqlite"
    )
    si.reset_search_index_for_tests()
    config.get_settings.cache_clear()
    clear_codex_caches()
    invalidate_detail_dict_cache()
    e = Env(codex=codex, data_dir=data_dir, claude_dir=claude_dir)
    e.sync()
    try:
        yield e
    finally:
        clear_codex_caches()
        invalidate_detail_dict_cache()
        si.reset_search_index_for_tests()
        config.get_settings.cache_clear()


@pytest.fixture
def api(env: Env) -> TestClient:
    from backend.main import app

    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def _detail(api: TestClient, uuid: str) -> dict[str, Any]:
    r = api.get(f"/api/conversations/{uuid}")
    assert r.status_code == 200, r.text
    return r.json()


def _texts(detail: dict[str, Any]) -> list[str]:
    out = []
    for m in detail["messages"]:
        text = m.get("text") or "".join(
            b.get("text") or "" for b in m.get("content") or []
        )
        if text:
            out.append(text)
    return out


def _incomplete(detail: dict[str, Any]) -> list[str]:
    return [n for n in detail.get("notices") or [] if n.startswith("incomplete")]


# ---------------------------------------------------------------------------
# parent_uuid
# ---------------------------------------------------------------------------


def test_fork_parent_uuid_is_its_forked_from_id(api: TestClient) -> None:
    assert _detail(api, CHILD_FORK)["parent_uuid"] == ROOT


def test_forked_from_id_wins_over_parent_thread_id(api: TestClient) -> None:
    assert _detail(api, CHILD_FIRST)["parent_uuid"] == ROOT


def test_parent_uuid_is_never_the_own_uuid(api: TestClient) -> None:
    assert _detail(api, SELF_PARENT)["parent_uuid"] is None


def test_parent_not_in_the_archive_is_still_the_parent_uuid(api: TestClient) -> None:
    assert _detail(api, FORK_MISSING)["parent_uuid"] == MISSING_BASE


# ---------------------------------------------------------------------------
# child_uuids
# ---------------------------------------------------------------------------


def test_parent_lists_its_subagent_as_a_child(api: TestClient) -> None:
    assert _detail(api, fake.THREAD_PAGINATED)["child_uuids"] == [fake.THREAD_SUBAGENT]


def test_child_uuids_are_in_created_at_order_with_archived_children(
    api: TestClient,
) -> None:
    assert _detail(api, ROOT)["child_uuids"] == [
        CHILD_FIRST,
        CHILD_FORK,
        CHILD_ARCHIVED,
    ]


def test_a_thread_without_turns_is_not_a_child(api: TestClient) -> None:
    assert CHILD_NO_TURNS not in _detail(api, ROOT)["child_uuids"]


def test_a_session_without_children_has_empty_child_uuids(api: TestClient) -> None:
    assert _detail(api, fake.THREAD_LEGACY)["child_uuids"] == []


def test_a_subagent_has_no_children_and_names_its_parent(api: TestClient) -> None:
    sub = _detail(api, fake.THREAD_SUBAGENT)
    assert (sub["parent_uuid"], sub["child_uuids"]) == (fake.THREAD_PAGINATED, [])


def test_the_store_detail_model_carries_child_uuids(env: Env) -> None:
    conv = env.store().get_conversation(ROOT)
    assert conv is not None
    assert conv.child_uuids == [CHILD_FIRST, CHILD_FORK, CHILD_ARCHIVED]


def test_other_sources_have_empty_child_uuids(env: Env, api: TestClient) -> None:
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(
        env.data_dir,
        build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"),
    )
    desktop = _detail(api, DESKTOP_UUID)
    assert (desktop["parent_uuid"], desktop["child_uuids"]) == (None, [])


def test_a_new_child_shows_after_the_next_sync(env: Env, api: TestClient) -> None:
    before = _detail(api, fake.THREAD_LEGACY)["child_uuids"]
    child = "019ab000-0000-7000-8000-0000000011b8"
    late = _Rollout(child, T0 + timedelta(hours=9), forked_from_id=fake.THREAD_LEGACY)
    late.turn("turn-b8", "Late fork question.", "Late fork answer.")
    late.write(env.codex)
    env.sync()

    assert (before, _detail(api, fake.THREAD_LEGACY)["child_uuids"]) == ([], [child])


# ---------------------------------------------------------------------------
# The notices after N10
# ---------------------------------------------------------------------------


def test_resolved_fork_shows_no_incomplete_notice(api: TestClient) -> None:
    assert _incomplete(_detail(api, CHILD_FORK)) == []


def test_resolved_fork_shows_the_inherited_messages_first(api: TestClient) -> None:
    assert _texts(_detail(api, CHILD_FORK)) == [
        "Root question 1.",
        "Root answer 1.",
        "Root question 2.",
        "Root answer 2.",
        "Fork question.",
        "Fork answer.",
    ]


def test_fork_with_a_missing_base_keeps_the_incomplete_notice(api: TestClient) -> None:
    assert _incomplete(_detail(api, FORK_MISSING)) == [
        "incomplete: history_base missing"
    ]


def test_a_base_copied_later_resolves_on_the_next_detail_call(
    env: Env, api: TestClient
) -> None:
    base_id = "019ab000-0000-7000-8000-0000000011f2"
    fork_id = "019ab000-0000-7000-8000-0000000011d2"
    base = _Rollout(base_id, T0 + timedelta(hours=10))
    starts = [
        base.turn(f"turn-h{n}", f"Late base question {n}.", f"Late base answer {n}.")
        for n in (1, 2)
    ]
    fork = _Rollout(
        fork_id,
        T0 + timedelta(hours=11),
        history_base=base.base_at(starts[1]),
        forked_from_id=base_id,
    )
    fork.turn("turn-i1", "Late fork question.", "Late fork answer.")
    fork.write(env.codex)
    env.sync()
    before = _detail(api, fork_id)
    base.write(env.codex)
    env.sync()
    # The fork's own files did not change; only its base arrived.

    after = _detail(api, fork_id)
    assert _incomplete(before) == ["incomplete: history_base missing"]
    assert _incomplete(after) == []
    assert _texts(after) == [
        "Late base question 1.",
        "Late base answer 1.",
        "Late fork question.",
        "Late fork answer.",
    ]


def test_compacted_session_shows_no_incomplete_notice(api: TestClient) -> None:
    assert _incomplete(_detail(api, COMPACTED)) == []


def test_compacted_session_has_one_compact_marker(api: TestClient) -> None:
    assert len(_detail(api, COMPACTED)["compact_markers"]) == 1


def test_rolled_back_session_shows_no_incomplete_notice(api: TestClient) -> None:
    assert _incomplete(_detail(api, ROLLED_BACK)) == []


def test_rolled_back_turn_is_hidden(api: TestClient) -> None:
    texts = _texts(_detail(api, ROLLED_BACK))
    assert "Rolled question 3." not in texts
    assert texts[-1] == "Rolled answer 2."


# ---------------------------------------------------------------------------
# MCP get_session_outline
# ---------------------------------------------------------------------------


@pytest.fixture
def outline(env: Env, monkeypatch: pytest.MonkeyPatch) -> Any:
    from mcp_server import server

    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_settings", None)
    server.reset_archive_sync_for_tests()

    def call(uuid: str) -> dict[str, Any]:
        fn = getattr(server.get_session_outline, "fn", server.get_session_outline)
        return fn(conversation_id=uuid)

    yield call
    server.reset_archive_sync_for_tests()


def test_outline_of_a_parent_lists_its_subagent(outline: Any) -> None:
    out = outline(fake.THREAD_PAGINATED)
    assert (out["parent_uuid"], out["child_uuids"]) == (None, [fake.THREAD_SUBAGENT])


def test_outline_of_a_subagent_names_its_parent(outline: Any) -> None:
    out = outline(fake.THREAD_SUBAGENT)
    assert (out["parent_uuid"], out["child_uuids"]) == (fake.THREAD_PAGINATED, [])


def test_outline_of_a_fork_names_its_forked_from_id(outline: Any) -> None:
    assert outline(CHILD_FORK)["parent_uuid"] == ROOT


def test_outline_children_have_the_detail_order(outline: Any) -> None:
    assert outline(ROOT)["child_uuids"] == [CHILD_FIRST, CHILD_FORK, CHILD_ARCHIVED]


def test_only_codex_outlines_carry_the_link_keys(env: Env, outline: Any) -> None:
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(
        env.data_dir,
        build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"),
    )
    codex, desktop = outline(fake.THREAD_LEGACY), outline(DESKTOP_UUID)
    assert {"parent_uuid", "child_uuids"} <= codex.keys()
    assert not {"parent_uuid", "child_uuids"} & desktop.keys()
