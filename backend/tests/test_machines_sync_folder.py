"""A2 (issue #92): the shared sync folder.

``shared_archive_dir`` (config.json) is a folder that every machine can
reach (for example in Dropbox). Each pass:

- pushes this machine's allowed files (mode "export" only) to
  ``<shared>/machines/<own-id>/``, one writer per folder
  (``<shared>/machines/<own-id>/.sync.lock``);
- pulls an append-only copy of every other machine's folder into
  ``<data_dir>/machines/<id>/`` (every mode) and never writes there;
- copies only complete lines (a cloud client can deliver a partial line);
- never puts a search index into the shared folder.

The D13 gate: while a cloud client syncs the shared folder, nothing is
written there until ``allow_writes_in_shared_archive`` equals the resolved
folder (``POST /api/write-consent/shared-archive``). A confirmed command
removes only this machine's own folder. Synthetic data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    archived,
    make_world,
    tree,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


S_B = "a2c5c5c5-0000-4000-8000-00000000000b"
S_B_WORK = "a2c5c5c5-0000-4000-8000-0000000000bb"
S_A = "a2c5c5c5-0000-4000-8000-00000000000a"
W_B = "sharedbword"
W_A = "sharedaword"


def _share(world: World, *machines: Any) -> None:
    for m in machines:
        m.write_config(shared_archive_dir=str(world.shared))


def _push_b(world: World, **rules: Any) -> None:
    b = world.b
    b.cc_session(S_B, W_B, cwd=b.cwd("Source", "personal", "app"))
    b.cc_session(S_B_WORK, "sharedworkword", cwd=b.cwd("Source", "work", "api"))
    _share(world, b)
    b.write_rules(
        **({"mode": "export", "allow": [{}], "deny": ["~/Source/work/**"]} | rules)
    )
    b.sync()
    _shared_sync(b)


def _shared_sync(m: Any, timeout: float = 10) -> Any:
    from backend import machines

    settings = m.settings()
    return machines.sync_shared_folder(settings, "refresh", timeout)


def _b_folder(world: World) -> Path:
    return world.shared / "machines" / world.b.machine_id


def _session_files(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob("*.jsonl")) if root.is_dir() else []


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


def test_a_pass_pushes_this_machines_allowed_sessions(world: World) -> None:
    _push_b(world)

    folder = _b_folder(world)
    assert _session_files(folder) == [f"{S_B}.jsonl"]
    live = world.b.cc_file(S_B, world.b.cwd("Source", "personal", "app")).read_bytes()
    [copy] = list(folder.rglob(f"{S_B}.jsonl"))
    assert copy.read_bytes() == live
    marker = json.loads((folder / "machine.json").read_text(encoding="utf-8"))
    assert marker["machine_id"] == world.b.machine_id


def test_the_watcher_and_backstop_passes_push_too(world: World) -> None:
    b = world.b
    b.cc_session(S_B, W_B)
    _share(world, b)
    b.export_all()

    b.sync(reason="backstop")

    assert _session_files(_b_folder(world)) == [f"{S_B}.jsonl"]


def test_a_pass_appends_new_lines_to_the_pushed_copy(world: World) -> None:
    _push_b(world)
    cwd = world.b.cwd("Source", "personal", "app")
    world.b.cc_add_turn(S_B, "laterword", cwd=cwd)
    world.b.sync()

    _shared_sync(world.b)

    [copy] = list(_b_folder(world).rglob(f"{S_B}.jsonl"))
    assert copy.read_bytes() == world.b.cc_file(S_B, cwd).read_bytes()


@pytest.mark.parametrize("mode", ["off", "receive-only"])
def test_no_push_unless_the_mode_is_export(world: World, mode: str) -> None:
    _push_b(world, mode=mode)
    world.c.cc_session("a2c5c5c5-0000-4000-8000-00000000000c", "cword")
    _share(world, world.c)
    world.c.export_all()
    world.c.sync()
    _shared_sync(world.c)

    # The pass ran (C pushed through it), but B, in mode off or receive-only, sent nothing.
    assert _session_files(world.shared / "machines" / world.c.machine_id)
    assert _session_files(_b_folder(world)) == []


def test_the_shared_folder_never_holds_a_search_index(world: World) -> None:
    from backend.tests._machines_env import no_sqlite

    _push_b(world)
    world.b.hits(W_B)
    _shared_sync(world.b)

    assert _session_files(_b_folder(world))
    assert no_sqlite(world.shared) == []


def test_one_writer_per_folder_a_held_lock_stops_the_push(world: World) -> None:
    from backend.archive_sync import ArchiveLock

    b = world.b
    b.cc_session(S_B, W_B)
    b.export_all()
    # The archive copy first: with the shared folder set, run_archive_syncs pushes too.
    b.sync()
    _share(world, b)
    b.ensure_id()
    lock = ArchiveLock(world.shared / "machines" / b.machine_id / ".sync.lock")
    assert lock.acquire(1.0)
    try:
        report = _shared_sync(b, timeout=0)
    finally:
        lock.release()

    assert report is not None
    assert report.skipped_reason in ("busy", "timeout")
    assert _session_files(_b_folder(world)) == []
    # The next pass, with the lock free, pushes.
    _shared_sync(b)
    assert _session_files(_b_folder(world)) == [f"{S_B}.jsonl"]


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


def test_a_pass_pulls_a_copy_of_other_machines(world: World) -> None:
    _push_b(world)
    _share(world, world.a)

    _shared_sync(world.a)

    copies = list(world.a.foreign(world.b.machine_id).rglob(f"{S_B}.jsonl"))
    assert len(copies) == 1
    live = world.b.cc_file(S_B, world.b.cwd("Source", "personal", "app")).read_bytes()
    assert copies[0].read_bytes() == live
    rows = world.a.rows("CLAUDE_CODE")
    assert rows[S_B].machine_id == world.b.machine_id
    assert W_B in world.a.detail_text(S_B)
    assert S_B in world.a.hits(W_B)


@pytest.mark.parametrize("mode", ["off", "receive-only", "export"])
def test_every_mode_pulls(world: World, mode: str) -> None:
    _push_b(world)
    _share(world, world.a)
    world.a.write_rules(mode=mode, allow=[{}])

    _shared_sync(world.a)

    assert S_B in world.a.rows("CLAUDE_CODE")


def test_a_pull_never_writes_into_another_machines_folder(world: World) -> None:
    _push_b(world)
    _share(world, world.a)
    world.a.cc_session(S_A, W_A)
    world.a.export_all()
    world.a.sync()
    before = tree(_b_folder(world))

    _shared_sync(world.a)
    world.a.rows()
    _shared_sync(world.a)

    assert S_B in world.a.rows("CLAUDE_CODE")
    assert tree(_b_folder(world)) == before


def test_a_pull_does_not_pull_this_machines_own_folder(world: World) -> None:
    _push_b(world)

    _shared_sync(world.b)

    # B's own sessions stay in B's own archive folder, not in a second copy.
    own = world.b.data_dir / "machines" / world.b.machine_id
    assert len(list(world.b.data_dir.rglob(f"{S_B}.jsonl"))) == 1
    assert list(own.rglob(f"{S_B}.jsonl"))


def test_a_pulled_session_stays_when_its_machine_leaves_the_shared_folder(
    world: World,
) -> None:
    from backend import machines

    _push_b(world)
    _share(world, world.a)
    _shared_sync(world.a)
    machines.remove_own_shared_folder(world.b.settings(), confirm=world.b.machine_id)
    assert not _b_folder(world).exists()

    _shared_sync(world.a)

    assert S_B in world.a.rows("CLAUDE_CODE")
    assert W_B in world.a.detail_text(S_B)


def test_a_pull_copies_only_complete_lines(world: World) -> None:
    from backend.tests._machines_env import cc_entries, line

    _push_b(world)
    _share(world, world.a)
    [shared_copy] = list(_b_folder(world).rglob(f"{S_B}.jsonl"))
    full = shared_copy.read_bytes()
    extra = line(
        cc_entries(
            S_B,
            "partialword",
            cwd=world.b.cwd("Source", "personal", "app"),
            ts="2026-11-20T11:00:00Z",
            prefix="p-",
        )[0]
    )
    shared_copy.write_bytes(full + extra[: len(extra) // 2])

    _shared_sync(world.a)

    [copy] = list(world.a.foreign(world.b.machine_id).rglob(f"{S_B}.jsonl"))
    assert copy.read_bytes() == full
    assert W_B in world.a.detail_text(S_B)

    # The cloud client delivers the rest of the line: the next pass appends it.
    shared_copy.write_bytes(full + extra)
    _shared_sync(world.a)
    assert copy.read_bytes() == full + extra
    assert "partialword" in world.a.detail_text(S_B)


def test_a_pull_reads_a_foreign_codex_folder(world: World) -> None:
    b = world.b
    b.codex_thread("019b0a40-0000-7000-8000-0000000000bb", "sharedcodexword")
    _share(world, b)
    b.export_all()
    b.sync()
    _shared_sync(b)
    _share(world, world.a)

    _shared_sync(world.a)

    rows = world.a.rows("CODEX")
    assert "019b0a40-0000-7000-8000-0000000000bb" in rows
    assert rows["019b0a40-0000-7000-8000-0000000000bb"].machine_id == b.machine_id


# ---------------------------------------------------------------------------
# The cloud-sync gate (D13) and the corrupt config
# ---------------------------------------------------------------------------


def _shared_in_dropbox(world: World) -> Path:
    shared = world.b.home / "Dropbox" / "ce-shared"
    shared.mkdir(parents=True)
    world.shared = shared
    return shared


def test_no_push_into_a_cloud_synced_shared_folder_without_consent(
    world: World,
) -> None:
    shared = _shared_in_dropbox(world)
    b = world.b
    b.cc_session(S_B, W_B)
    _share(world, b)
    b.export_all()
    b.sync()

    report = _shared_sync(b)

    assert report is not None
    assert report.skipped_reason == "cloud_sync_gate"
    assert _session_files(shared) == []


def test_the_consent_opens_the_shared_folder_gate(world: World) -> None:
    from backend.tests._machines_env import realpath

    shared = _shared_in_dropbox(world)
    b = world.b
    b.cc_session(S_B, W_B)
    _share(world, b)
    b.write_config(allow_writes_in_shared_archive=realpath(shared))
    b.export_all()
    b.sync()

    _shared_sync(b)

    assert _session_files(shared / "machines" / b.machine_id) == [f"{S_B}.jsonl"]


def test_a_consent_for_another_folder_keeps_the_gate_closed(world: World) -> None:
    shared = _shared_in_dropbox(world)
    b = world.b
    b.cc_session(S_B, W_B)
    _share(world, b)
    b.write_config(allow_writes_in_shared_archive=str(world.tmp / "elsewhere"))
    b.export_all()
    b.sync()

    report = _shared_sync(b)

    assert report.skipped_reason == "cloud_sync_gate"
    assert _session_files(shared) == []


def test_a_corrupt_config_stops_the_shared_folder_pass(world: World) -> None:
    _push_b(world)
    before = tree(world.shared)
    world.b.cc_add_turn(S_B, "afterbreakword")
    world.b.write_corrupt_config()

    report = _shared_sync(world.b)

    assert report is not None
    assert report.skipped_reason == "corrupt_config"
    assert tree(world.shared) == before


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def test_removal_takes_only_this_machines_own_folder(world: World) -> None:
    from backend import machines

    _push_b(world)
    world.c.cc_session("a2c5c5c5-0000-4000-8000-00000000000c", "cword")
    _share(world, world.c)
    world.c.export_all()
    world.c.sync()
    _shared_sync(world.c)
    c_before = tree(world.shared / "machines" / world.c.machine_id)
    local_before = archived(world.b.data_dir)
    assert c_before

    machines.remove_own_shared_folder(world.b.settings(), confirm=world.b.machine_id)

    assert not _b_folder(world).exists()
    assert tree(world.shared / "machines" / world.c.machine_id) == c_before
    assert archived(world.b.data_dir) == local_before


def test_removal_needs_the_machine_id_as_confirmation(world: World) -> None:
    from backend import machines

    _push_b(world)
    before = tree(world.shared)

    with pytest.raises(ValueError):
        machines.remove_own_shared_folder(world.b.settings(), confirm="yes")

    assert tree(world.shared) == before


def test_a_shared_jsonl_file_over_the_decoded_limit_is_skipped_and_the_pass_goes_on(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A2 security round 2, S3: already true through sync_stream's decode cap; kept as a guard.
    from backend import archive_sync, machines

    b = world.b
    s_big, s_small = (
        "a2abab01-0000-4000-8000-000000000001",
        "a2abab01-0000-4000-8000-000000000002",
    )
    b.cc_session(s_big, "bigword", cwd=b.cwd("Source", "personal", "one"))
    b.cc_session(s_small, "smallword", cwd=b.cwd("Source", "personal", "two"))
    _push_b(world, allow=[{}], deny=[])
    folder = _b_folder(world)
    [big] = list(folder.rglob(f"{s_big}.jsonl"))
    [small] = list(folder.rglob(f"{s_small}.jsonl"))
    first = big.read_bytes().splitlines(keepends=True)[0]
    big.write_bytes(big.read_bytes() + first * 40)
    limit = small.stat().st_size + 10
    assert big.stat().st_size > limit
    monkeypatch.setattr(machines, "MAX_DECODED_BYTES", limit, raising=False)
    monkeypatch.setattr(archive_sync, "MAX_DECODED_BYTES", limit)
    _share(world, world.a)

    report = _shared_sync(world.a)

    copy = world.a.foreign(b.machine_id)
    assert list(copy.rglob(f"{s_small}.jsonl"))
    assert not list(copy.rglob(f"{s_big}.jsonl"))
    assert len([w for w in report.warnings if s_big in w]) == 1
