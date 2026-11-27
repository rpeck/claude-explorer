"""A2 (issue #92): which machine a session came from, and duplicates.

Rows (``ConversationSummary`` and the list item) carry ``machine_id``,
``machine_label``, and ``machine_labels``. The shown label is the local
name (``~/.claude-explorer/machine-labels.json``, never shared), else the
machine's own label, else "Machine <first 8 of id>". The same session on two
machines is one row: live first, then machine-id order. ``machine_labels``
lists every machine whose copy has the same prefix; a diverged copy stays
on disk unchanged. Synthetic data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    make_world,
)

pytestmark = pytest.mark.xfail(strict=True, reason="A2 not implemented yet")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


S = "a2d0d0d0-0000-4000-8000-000000000001"
S_ONLY_B = "a2d0d0d0-0000-4000-8000-0000000000b1"
S_ONLY_C = "a2d0d0d0-0000-4000-8000-0000000000c1"


def _label(m: Any, label: str | None) -> None:
    from backend import machines

    m.activate()
    machines.set_machine_label(label)


def _bundle_into_a(world: World, m: Any, name: str) -> None:
    m.export_all()
    m.sync()
    dest = world.tmp / name
    m.export(dest)
    world.a.import_(dest)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_set_machine_label_writes_the_identity_and_the_machine_folder(
    world: World,
) -> None:
    a = world.a
    a.ensure_id()

    _label(a, "  Home Mac  ")

    ident = json.loads((a.explorer_home / "machine.json").read_text(encoding="utf-8"))
    marker = json.loads(
        (a.data_dir / "machines" / a.machine_id / "machine.json").read_text(
            encoding="utf-8"
        )
    )
    assert ident["label"] == "Home Mac"
    assert marker["label"] == "Home Mac"


def test_an_empty_label_clears_it(world: World) -> None:
    a = world.a
    a.ensure_id()
    _label(a, "Home Mac")

    _label(a, "   ")

    ident = json.loads((a.explorer_home / "machine.json").read_text(encoding="utf-8"))
    assert ident["label"] is None


@pytest.mark.parametrize("bad", ["x" * 65, "tab\there", "new\nline"])
def test_a_label_that_is_too_long_or_has_a_control_char_is_refused(
    world: World, bad: str
) -> None:
    from backend import machines

    world.a.ensure_id()
    world.a.activate()

    with pytest.raises(ValueError):
        machines.set_machine_label(bad)


def test_live_rows_carry_this_machines_id_and_label(world: World) -> None:
    a = world.a
    a.cc_session(S, "liveword")
    a.sync()
    _label(a, "Home Mac")

    row = a.rows("CLAUDE_CODE")[S]

    assert row.machine_id == a.machine_id
    assert row.machine_label == "Home Mac"
    assert row.machine_labels == ["Home Mac"]


def test_a_machine_without_a_label_shows_machine_and_the_first_8_of_its_id(
    world: World,
) -> None:
    b = world.b
    b.cc_session(S_ONLY_B, "bword")
    _bundle_into_a(world, b, "b.cebundle")

    row = world.a.rows("CLAUDE_CODE")[S_ONLY_B]

    assert row.machine_id == b.machine_id
    assert row.machine_label == f"Machine {b.machine_id[:8]}"


def test_a_foreign_row_shows_the_label_of_its_machine(world: World) -> None:
    b = world.b
    b.cc_session(S_ONLY_B, "bword")
    b.sync()
    _label(b, "Work laptop")
    _bundle_into_a(world, b, "b.cebundle")

    assert world.a.rows("CLAUDE_CODE")[S_ONLY_B].machine_label == "Work laptop"


def test_a_local_name_wins_over_the_machines_own_label(world: World) -> None:
    b = world.b
    b.cc_session(S_ONLY_B, "bword")
    b.sync()
    _label(b, "Work laptop")
    _bundle_into_a(world, b, "b.cebundle")
    (world.a.explorer_home / "machine-labels.json").write_text(
        json.dumps({b.machine_id: "Office"}), encoding="utf-8"
    )

    assert world.a.rows("CLAUDE_CODE")[S_ONLY_B].machine_label == "Office"


def test_list_items_carry_the_machine_fields(world: World) -> None:
    from backend.models import ConversationListItem

    a = world.a
    a.cc_session(S, "liveword")
    a.sync()

    item = ConversationListItem.model_validate(
        a.rows("CLAUDE_CODE")[S], from_attributes=True
    )

    assert item.machine_id == a.machine_id
    assert item.machine_label == f"Machine {a.machine_id[:8]}"
    assert item.machine_labels == [item.machine_label]


def test_list_machines_lists_this_machine_first_with_session_counts(
    world: World,
) -> None:
    from backend import machines

    a, b = world.a, world.b
    a.cc_session(S, "liveword")
    a.sync()
    _label(a, "Home Mac")
    b.cc_session(S_ONLY_B, "bword")
    b.cc_session("a2d0d0d0-0000-4000-8000-0000000000b2", "bword2")
    b.sync()
    _label(b, "Work laptop")
    _bundle_into_a(world, b, "b.cebundle")
    a.activate()

    infos = machines.list_machines()

    assert [i.machine_id for i in infos] == [a.machine_id, b.machine_id]
    assert infos[0].is_this_machine is True
    assert infos[0].label == "Home Mac"
    assert infos[1].is_this_machine is False
    assert infos[1].label == "Work laptop"
    assert infos[1].session_count == 2


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_the_same_session_with_the_same_prefix_is_one_row_with_both_labels(
    world: World,
) -> None:
    from backend.tests._machines_env import cc_entries
    from backend.tests.builders import write_cc_jsonl

    a, b = world.a, world.b
    # One session file that exists on both machines (for example a synced ~/.claude).
    cwd = "/tmp/a2/shared-project"
    entries = cc_entries(S, "bothword", cwd=cwd, ts="2026-11-20T10:00:00Z")
    write_cc_jsonl(b.cc_file(S, cwd), entries[:1])
    b.sync()
    _label(b, "Work laptop")
    _bundle_into_a(world, b, "b.cebundle")
    write_cc_jsonl(a.cc_file(S, cwd), entries)
    a.sync()
    _label(a, "Home Mac")

    rows = a.rows("CLAUDE_CODE")

    assert rows[S].machine_id == a.machine_id
    assert rows[S].machine_labels == ["Home Mac", "Work laptop"]


def test_a_diverged_copy_is_one_row_and_both_copies_stay_on_disk(world: World) -> None:
    from backend.tests._machines_env import cc_entries
    from backend.tests.builders import write_cc_jsonl

    a, b = world.a, world.b
    cwd = "/tmp/a2/shared-project"
    write_cc_jsonl(
        b.cc_file(S, cwd), cc_entries(S, "bversion", cwd=cwd, ts="2026-11-20T10:00:00Z")
    )
    b.sync()
    _label(b, "Work laptop")
    _bundle_into_a(world, b, "b.cebundle")
    write_cc_jsonl(
        a.cc_file(S, cwd), cc_entries(S, "aversion", cwd=cwd, ts="2026-11-21T10:00:00Z")
    )
    a.sync()
    _label(a, "Home Mac")
    b_copy = list(a.foreign(b.machine_id).rglob(f"{S}.jsonl"))
    a_copy = list((a.data_dir / "machines" / a.machine_id).rglob(f"{S}.jsonl"))
    before = (b_copy[0].read_bytes(), a_copy[0].read_bytes())

    rows = a.rows("CLAUDE_CODE")

    assert rows[S].machine_id == a.machine_id
    assert rows[S].machine_labels == ["Home Mac"]
    assert "aversion" in a.detail_text(S)
    assert (b_copy[0].read_bytes(), a_copy[0].read_bytes()) == before
    assert b"bversion" in before[0]


def test_without_a_live_file_the_first_machine_in_id_order_shows(world: World) -> None:
    from backend.tests._machines_env import cc_entries
    from backend.tests.builders import write_cc_jsonl

    b, c = world.b, world.c
    cwd = "/tmp/a2/shared-project"
    write_cc_jsonl(
        b.cc_file(S, cwd), cc_entries(S, "bversion", cwd=cwd, ts="2026-11-20T10:00:00Z")
    )
    write_cc_jsonl(
        c.cc_file(S, cwd), cc_entries(S, "cversion", cwd=cwd, ts="2026-11-21T10:00:00Z")
    )
    b.sync()
    c.sync()
    _bundle_into_a(world, b, "b.cebundle")
    _bundle_into_a(world, c, "c.cebundle")
    first = min(b.machine_id, c.machine_id)

    rows = world.a.rows("CLAUDE_CODE")

    assert rows[S].machine_id == first
    assert ("bversion" if first == b.machine_id else "cversion") in world.a.detail_text(
        S
    )


def test_sessions_of_three_machines_all_show_in_one_list(world: World) -> None:
    a, b, c = world.a, world.b, world.c
    a.cc_session(S, "aword")
    a.sync()
    b.cc_session(S_ONLY_B, "bword")
    c.cc_session(S_ONLY_C, "cword")
    b.sync()
    c.sync()
    _bundle_into_a(world, b, "b.cebundle")
    _bundle_into_a(world, c, "c.cebundle")

    rows = a.rows("CLAUDE_CODE")

    assert {
        rows[S].machine_id,
        rows[S_ONLY_B].machine_id,
        rows[S_ONLY_C].machine_id,
    } == {
        a.machine_id,
        b.machine_id,
        c.machine_id,
    }
    assert a.hits("cword") == {S_ONLY_C}
