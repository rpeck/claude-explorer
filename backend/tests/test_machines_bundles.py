"""A2 (issue #92): export and import bundles.

A bundle carries this machine's allowed sessions to another machine:
a zip with ``machines/<id>/machine.json``, ``machines/<id>/manifest.json``
(path, sha256, bytes, offset per file), and the files. Later exports carry
only new bytes. Import is append-only into ``<data_dir>/machines/<id>/``: a
diverged file keeps the old copy as ``.rev-<n>``, and nothing is deleted.
An optional passphrase encrypts the bundle (``CEXB1`` prefix).

Machine B (the work laptop) exports; machine A imports. Synthetic data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    bundle_manifest,
    bundle_members,
    make_world,
    member_of,
    rewrite_member,
    tree,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


S1 = "a2b1b1b1-0000-4000-8000-000000000001"
S2 = "a2b1b1b1-0000-4000-8000-000000000002"
T_A = "019b0a30-0000-7000-8000-0000000000aa"
T_B = "019b0a30-0000-7000-8000-0000000000bb"
W1 = "bundleoneword"
W2 = "bundletwoword"
PASS = "synthetic correct horse"


def _b_two_sessions(world: World) -> None:
    b = world.b
    b.cc_session(S1, W1, cwd=b.cwd("Source", "personal", "one"))
    b.cc_session(S2, W2, cwd=b.cwd("Source", "personal", "two"))
    b.sync()
    b.export_all()


def _export(world: World, name: str = "b1.cebundle", **kw: Any) -> Path:
    dest = world.tmp / name
    world.b.export(dest, **kw)
    return dest


def _copy_in_a(world: World, session: str, *parts: str) -> Path:
    """A's archive copy of a B session file."""
    matches = list(world.a.foreign(world.b.machine_id).rglob(f"{session}.jsonl"))
    assert len(matches) == 1, f"{session}: {matches}"
    return matches[0]


def _no_writes(counts: dict[str, int]) -> bool:
    return (
        counts.get("copied", 0)
        == counts.get("appended", 0)
        == counts.get("revisions", 0)
        == 0
    )


# ---------------------------------------------------------------------------
# The bundle file
# ---------------------------------------------------------------------------


def test_a_plain_bundle_is_a_zip_with_machine_json_and_a_manifest(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)

    prefix, manifest = bundle_manifest(dest)
    members = bundle_members(dest)

    assert prefix == f"machines/{world.b.machine_id}/"
    assert (
        json.loads(members[prefix + "machine.json"])["machine_id"] == world.b.machine_id
    )
    assert manifest["version"] == 1
    assert manifest["machine_id"] == world.b.machine_id
    assert manifest["encrypted"] is False
    assert manifest["exported_at"]


def test_manifest_hashes_and_sizes_match_the_members(world: World) -> None:
    import hashlib

    _b_two_sessions(world)
    dest = _export(world)

    prefix, manifest = bundle_manifest(dest)
    members = bundle_members(dest)
    assert len(manifest["files"]) >= 2
    for f in manifest["files"]:
        data = members[member_of(prefix, f["path"])]
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f["path"]
        assert len(data) == f["bytes"], f["path"]
        assert f["offset"] == 0, f["path"]


def test_the_bundle_holds_the_session_bytes(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)

    prefix, manifest = bundle_manifest(dest)
    members = bundle_members(dest)
    live = world.b.cc_file(S1, world.b.cwd("Source", "personal", "one")).read_bytes()
    [f] = [f for f in manifest["files"] if f["path"].endswith(f"/{S1}.jsonl")]
    assert members[member_of(prefix, f["path"])] == live


def test_export_result_reports_the_files_and_bytes(world: World) -> None:
    _b_two_sessions(world)
    dest = world.tmp / "b1.cebundle"

    result = world.b.export(dest)

    _, manifest = bundle_manifest(dest)
    assert Path(result.path) == dest
    assert result.file_count == len(manifest["files"])
    assert result.total_bytes == sum(f["bytes"] for f in manifest["files"])
    assert result.encrypted is False


def test_the_bundle_carries_the_machine_label(world: World) -> None:
    from backend import machines

    _b_two_sessions(world)
    world.b.activate()
    machines.set_machine_label("Work laptop")
    dest = _export(world)

    prefix, manifest = bundle_manifest(dest)
    assert manifest["label"] == "Work laptop"
    assert (
        json.loads(bundle_members(dest)[prefix + "machine.json"])["label"]
        == "Work laptop"
    )


def test_local_names_of_other_machines_never_leave_this_machine(world: World) -> None:
    _b_two_sessions(world)
    labels = world.b.explorer_home / "machine-labels.json"
    labels.write_text(
        json.dumps({"0e0e0e0e-0000-4000-8000-000000000000": "privatenickname"}),
        encoding="utf-8",
    )

    dest = _export(world)

    assert b"privatenickname" not in dest.read_bytes()
    assert all(b"privatenickname" not in data for data in bundle_members(dest).values())


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_puts_the_files_under_the_foreign_machine_folder(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)

    result = world.a.import_(dest)

    assert result.machine_id == world.b.machine_id
    assert result.counts["copied"] >= 2
    live = world.b.cc_file(S1, world.b.cwd("Source", "personal", "one")).read_bytes()
    assert _copy_in_a(world, S1).read_bytes() == live
    marker = json.loads(
        (world.a.foreign(world.b.machine_id) / "machine.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["machine_id"] == world.b.machine_id


def test_an_imported_session_shows_in_the_list_viewer_and_search(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)
    world.a.import_(dest)

    rows = world.a.rows("CLAUDE_CODE")
    assert S1 in rows
    assert rows[S1].machine_id == world.b.machine_id
    assert W1 in world.a.detail_text(S1)
    assert S1 in world.a.hits(W1)


def test_import_works_in_receive_only_mode(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)
    world.a.write_rules(mode="receive-only")

    result = world.a.import_(dest)

    assert result.counts["copied"] >= 2
    assert S1 in world.a.rows("CLAUDE_CODE")


def test_a_second_import_of_the_same_bundle_changes_nothing(world: World) -> None:
    _b_two_sessions(world)
    dest = _export(world)
    world.a.import_(dest)
    before = tree(world.a.data_dir / "machines")

    again = world.a.import_(dest)

    assert _no_writes(again.counts), again.counts
    assert tree(world.a.data_dir / "machines") == before


def test_a_bundle_of_this_machine_is_refused(world: World) -> None:
    from backend import machines

    _b_two_sessions(world)
    dest = _export(world)
    before = tree(world.b.data_dir)

    with pytest.raises(machines.BundleError) as err:
        world.b.import_(dest)

    assert err.value.reason == "own_machine"
    assert tree(world.b.data_dir) == before


def test_a_file_that_is_not_a_bundle_is_refused(world: World) -> None:
    from backend import machines

    junk = world.tmp / "junk.cebundle"
    junk.write_bytes(b"this is not a bundle at all")

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(junk)

    assert err.value.reason == "not_a_bundle"
    assert not (world.a.data_dir / "machines").exists() or not any(
        (world.a.data_dir / "machines").iterdir()
    )


def test_a_bad_hash_refuses_the_whole_bundle(world: World) -> None:
    from backend import machines

    _b_two_sessions(world)
    dest = _export(world)
    prefix, manifest = bundle_manifest(dest)
    [f] = [f for f in manifest["files"] if f["path"].endswith(f"/{S2}.jsonl")]
    name = member_of(prefix, f["path"])
    rewrite_member(
        dest, name, bundle_members(dest)[name].replace(W2.encode(), b"tamperedword")
    )

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(dest)

    assert err.value.reason == "bad_hash"
    # Not even the good file lands: a refused import writes nothing.
    assert tree(world.a.foreign(world.b.machine_id)) == {}


# ---------------------------------------------------------------------------
# Incremental export
# ---------------------------------------------------------------------------


def test_a_later_export_carries_only_the_new_bytes(world: World) -> None:
    _b_two_sessions(world)
    first = _export(world, "b1.cebundle")
    cwd = world.b.cwd("Source", "personal", "one")
    old_size = world.b.cc_file(S1, cwd).stat().st_size
    world.b.cc_add_turn(S1, "growthword", cwd=cwd)
    world.b.sync()

    second = _export(world, "b2.cebundle")

    prefix, manifest = bundle_manifest(second)
    sessions = [f for f in manifest["files"] if f["path"].endswith(".jsonl")]
    assert [f["path"] for f in sessions] == [
        f["path"] for f in sessions if f["path"].endswith(f"/{S1}.jsonl")
    ]
    [f] = sessions
    new_size = world.b.cc_file(S1, cwd).stat().st_size
    assert f["offset"] == old_size
    assert f["bytes"] == new_size - old_size
    assert (
        bundle_members(second)[member_of(prefix, f["path"])]
        == world.b.cc_file(S1, cwd).read_bytes()[old_size:]
    )
    assert first.exists()


def test_a_later_export_with_nothing_new_carries_no_session_file(world: World) -> None:
    _b_two_sessions(world)
    _export(world, "b1.cebundle")

    second = _export(world, "b2.cebundle")

    _, manifest = bundle_manifest(second)
    assert [f for f in manifest["files"] if f["path"].endswith(".jsonl")] == []


def test_a_full_export_carries_every_file_again(world: World) -> None:
    _b_two_sessions(world)
    _export(world, "b1.cebundle")

    second = _export(world, "b2.cebundle", full=True)

    _, manifest = bundle_manifest(second)
    ends = {f["path"].rsplit("/", 1)[-1] for f in manifest["files"] if f["offset"] == 0}
    assert {f"{S1}.jsonl", f"{S2}.jsonl"} <= ends


def test_incremental_bundles_imported_in_order_rebuild_the_file(world: World) -> None:
    _b_two_sessions(world)
    first = _export(world, "b1.cebundle")
    cwd = world.b.cwd("Source", "personal", "one")
    world.b.cc_add_turn(S1, "growthword", cwd=cwd)
    world.b.sync()
    second = _export(world, "b2.cebundle")

    world.a.import_(first)
    result = world.a.import_(second)

    assert result.counts["appended"] == 1
    assert _copy_in_a(world, S1).read_bytes() == world.b.cc_file(S1, cwd).read_bytes()
    assert "growthword" in world.a.detail_text(S1)


def test_an_incremental_part_without_its_base_writes_nothing(world: World) -> None:
    _b_two_sessions(world)
    _export(world, "b1.cebundle")
    cwd = world.b.cwd("Source", "personal", "one")
    world.b.cc_add_turn(S1, "growthword", cwd=cwd)
    world.b.sync()
    second = _export(world, "b2.cebundle")

    result = world.a.import_(second)

    assert result.counts["skipped"] >= 1
    assert any("missing_base" in w for w in result.warnings), result.warnings
    assert not list(world.a.foreign(world.b.machine_id).rglob(f"{S1}.jsonl"))


# ---------------------------------------------------------------------------
# Append-only: revisions, never delete
# ---------------------------------------------------------------------------


def test_a_diverged_file_keeps_the_old_copy_as_a_revision(world: World) -> None:
    from backend.tests._machines_env import cc_entries
    from backend.tests.builders import write_cc_jsonl

    _b_two_sessions(world)
    world.a.import_(_export(world, "b1.cebundle"))
    old = _copy_in_a(world, S1).read_bytes()
    cwd = world.b.cwd("Source", "personal", "one")
    write_cc_jsonl(
        world.b.cc_file(S1, cwd),
        cc_entries(S1, "rewrittenword", cwd=cwd, ts="2026-11-21T10:00:00Z"),
    )
    world.b.sync()
    second = _export(world, "b2.cebundle", full=True)

    result = world.a.import_(second)

    assert result.counts["revisions"] == 1
    new = world.b.cc_file(S1, cwd).read_bytes()
    assert _copy_in_a(world, S1).read_bytes() == new
    revs = list(world.a.foreign(world.b.machine_id).rglob(f"{S1}.rev-1.jsonl"))
    assert len(revs) == 1, revs
    assert revs[0].read_bytes() == old


def test_import_never_deletes_a_file_that_a_later_bundle_lacks(world: World) -> None:
    _b_two_sessions(world)
    world.a.import_(_export(world, "b1.cebundle"))
    kept = _copy_in_a(world, S2).read_bytes()
    world.b.export_all(deny=["~/Source/personal/two/**"])
    second = _export(world, "b2.cebundle", full=True)
    _, manifest = bundle_manifest(second)
    assert not [f for f in manifest["files"] if f["path"].endswith(f"/{S2}.jsonl")]

    world.a.import_(second)

    assert _copy_in_a(world, S2).read_bytes() == kept
    assert S2 in world.a.rows("CLAUDE_CODE")


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


def test_an_encrypted_bundle_hides_every_session_byte(world: World) -> None:
    import zipfile

    _b_two_sessions(world)
    dest = world.tmp / "enc.cebundle"

    result = world.b.export(dest, passphrase=PASS)

    data = dest.read_bytes()
    assert result.encrypted is True
    assert data.startswith(b"CEXB1")
    assert not zipfile.is_zipfile(dest)
    for word in (W1, W2, S1, world.b.machine_id):
        assert word.encode() not in data, word


def test_an_encrypted_bundle_needs_the_passphrase(world: World) -> None:
    from backend import machines

    _b_two_sessions(world)
    dest = world.tmp / "enc.cebundle"
    world.b.export(dest, passphrase=PASS)

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(dest)

    assert err.value.reason == "passphrase_required"
    assert not (world.a.data_dir / "machines").exists() or not any(
        (world.a.data_dir / "machines").iterdir()
    )


def test_a_wrong_passphrase_is_refused(world: World) -> None:
    from backend import machines

    _b_two_sessions(world)
    dest = world.tmp / "enc.cebundle"
    world.b.export(dest, passphrase=PASS)

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(dest, passphrase="synthetic wrong guess")

    assert err.value.reason == "bad_passphrase"
    assert not (world.a.data_dir / "machines").exists() or not any(
        (world.a.data_dir / "machines").iterdir()
    )


def test_an_encrypted_bundle_imports_with_the_passphrase(world: World) -> None:
    _b_two_sessions(world)
    dest = world.tmp / "enc.cebundle"
    world.b.export(dest, passphrase=PASS)

    result = world.a.import_(dest, passphrase=PASS)

    assert result.counts["copied"] >= 2
    assert W1 in world.a.detail_text(S1)


def test_among_other_machines_codex_copies_the_newest_thread_shows(
    world: World,
) -> None:
    # A2 security round 2, S5: the newest foreign copy wins when this machine has none.
    tid = "019b0a60-0000-7000-8000-0000000000ef"
    world.b.codex_thread(tid, "codexolderbword", day="2026-11-15")
    world.c.codex_thread(tid, "codexnewercword", day="2026-11-20")
    for m, name in ((world.b, "b.cebundle"), (world.c, "c.cebundle")):
        m.sync()
        m.export_all()
        dest = world.tmp / name
        m.export(dest)
        world.a.import_(dest)

    row = world.a.rows("CODEX")[tid]

    assert row.machine_id == world.c.machine_id
    assert "codexnewercword" in world.a.detail_text(tid)


def test_a_member_whose_header_lies_about_its_size_is_refused(world: World) -> None:
    # A2 security round 2, S3 amendment. CPython's zipfile never returns more
    # than the declared file_size: it raises "Bad CRC-32" there. So a lying
    # header cannot get past a limit; the bundle is refused, and nothing is written.
    import io
    import struct
    import zipfile

    from backend import machines

    _b_two_sessions(world)
    dest = _export(world)
    prefix, manifest = bundle_manifest(dest)
    [f] = [f for f in manifest["files"] if f["path"].endswith(f"/{S1}.jsonl")]
    target = member_of(prefix, f["path"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in bundle_members(dest).items():
            zf.writestr(name, data)
    raw = bytearray(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zf:
        offset = zf.getinfo(target).header_offset
    struct.pack_into("<I", raw, offset + 22, 10)  # the local header's file size
    at = raw.find(b"PK\x01\x02")
    while at != -1:
        name_len = struct.unpack_from("<H", raw, at + 28)[0]
        if bytes(raw[at + 46 : at + 46 + name_len]) == target.encode():
            struct.pack_into(
                "<I", raw, at + 24, 10
            )  # the central directory's file size
        at = raw.find(b"PK\x01\x02", at + 4)
    dest.write_bytes(bytes(raw))

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(dest)

    assert err.value.reason in ("too_large", "bad_hash")
    assert tree(world.a.data_dir / "machines") == {}


def test_an_encrypted_bundle_from_before_round_2_still_decrypts(world: World) -> None:
    import shutil

    # backend/tests/fixtures/machines/README.md: made at 124fccaf, no scrypt
    # parameters in its header (A2 security round 2, S6).
    fixture = Path(__file__).parent / "fixtures" / "machines" / "v1-encrypted.cebundle"
    dest = world.tmp / "v1.cebundle"
    shutil.copyfile(fixture, dest)

    result = world.a.import_(dest, passphrase="synthetic fixture pass")

    assert result.machine_id == "46b6aaee-ff4b-4cb3-a561-8ee694d67501"
    assert "a2f6f6f6-0000-4000-8000-000000000006" in world.a.rows("CLAUDE_CODE")
    assert "fixturevonekdfword" in world.a.detail_text(
        "a2f6f6f6-0000-4000-8000-000000000006"
    )


# ---------------------------------------------------------------------------
# Codex: two machines with one home path (the home_key collision)
# ---------------------------------------------------------------------------


def _same_codex_home(world: World) -> Path:
    import shutil

    same = world.tmp / "same-codex-home"
    world.a.codex_home = world.b.codex_home = same
    same.mkdir(exist_ok=True)
    world.b.codex_thread(T_B, "codexbword")
    world.b.sync()
    world.b.export_all()
    dest = _export(world, "codex.cebundle")
    # The same path on machine A holds A's own thread.
    shutil.rmtree(same)
    same.mkdir()
    world.a.codex_thread(T_A, "codexaword")
    world.a.sync()
    return dest


def test_two_machines_with_one_codex_home_path_keep_both_threads(world: World) -> None:
    dest = _same_codex_home(world)
    own = tree(world.a.data_dir / "codex")
    assert own

    world.a.import_(dest)

    rows = world.a.rows("CODEX")
    assert T_A in rows
    assert T_B in rows
    assert rows[T_B].machine_id == world.b.machine_id
    assert tree(world.a.data_dir / "codex") == own
    assert list(
        world.a.foreign(world.b.machine_id).rglob(f"codex/*/threads/{T_B}.json")
    )


def test_an_imported_codex_thread_shows_in_the_viewer_and_search(world: World) -> None:
    dest = _same_codex_home(world)

    world.a.import_(dest)

    assert "codexbword" in world.a.detail_text(T_B)
    assert T_B in world.a.hits("codexbword")
    assert T_A in world.a.hits("codexaword")
