"""A2 security round 2 (issue #92): `_orchestration/A2-SECURITY.md` S1 to S6.

Every test runs plainly. Synthetic data
and tmp_path only; the symlink tests skip on Windows. The size limits are
module constants that the tests set to small values:

- ``backend.machines.MAX_MANIFEST_BYTES`` (1 MiB): ``manifest.json`` and
  ``machine.json`` of a bundle.
- ``backend.machines.MAX_JSON_BYTES`` (8 MiB): any other ``.json`` member, and a
  ``.json`` file in the shared folder.
- ``MAX_DECODED_BYTES`` (the existing one; set on ``backend.machines`` and on
  ``backend.archive_sync``): a ``.jsonl`` member or shared-folder file.
- ``backend.machines.MAX_BUNDLE_BYTES`` (4 GiB): the total uncompressed size of
  a bundle, and the largest upload that the import route takes (else 413).
- ``backend.machines.FREE_SPACE_MARGIN_BYTES`` (1 GiB), with the free space from
  ``shutil.disk_usage`` on the data folder.

New BundleError reasons: ``too_large`` (a size limit), ``not_encrypted`` (S5),
``bad_kdf`` (S6: scrypt parameters in the header out of N 2^15..2^18,
r 1..8, p 1..16, refused before scrypt runs). The Cowork
host folders are the sidecar's ``userSelectedFolders``: string paths, or objects
with a string ``path``. Contract amendments: the end of ``A2-SECURITY.md``.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    bundle_manifest,
    bundle_members,
    cc_entries,
    make_world,
    rewrite_member,
    tree,
)

no_symlinks = pytest.mark.skipif(
    sys.platform == "win32", reason="a symlink needs extra rights on Windows"
)

S1 = "a2abab01-0000-4000-8000-000000000001"
S2 = "a2abab01-0000-4000-8000-000000000002"
SID_WORK = "a2c0ab01-2222-3333-4444-555566667771"
SID_HOME = "a2c0ab01-2222-3333-4444-555566667772"
PASS = "synthetic round two pass"
H = {"X-Claude-Explorer-Action": "machines"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


def _rules(**kw: Any) -> Any:
    from backend.machines import ExportRules

    return ExportRules(**({"mode": "export", "allow": [{}]} | kw))


def _ok(
    world: World, rules: Any, project: Path | str | None, source: str = "claude-code"
) -> bool:
    from backend.machines import session_allowed

    world.b.activate()
    return session_allowed(
        rules, source, None if project is None else str(project), None
    )


def _paths(world: World) -> set[str]:
    return {f.path for f in world.b.preview().files}


def _nothing_imported(world: World) -> bool:
    return tree(world.a.data_dir / "machines") == {}


def _bundle(world: World, name: str = "b.cebundle", **kw: Any) -> Path:
    b = world.b
    if not list(b.claude_dir.rglob("*.jsonl")):
        b.cc_session(S1, "roundtwoword", cwd=b.cwd("Source", "personal", "one"))
    b.sync()
    b.export_all()
    dest = world.tmp / name
    b.export(dest, **kw)
    return dest


def _import_error(world: World, src: Path, **kw: Any) -> Any:
    from backend import machines

    with pytest.raises(machines.BundleError) as err:
        world.a.import_(src, **kw)
    return err.value


# ---------------------------------------------------------------------------
# S1. Deny rules fail closed
# ---------------------------------------------------------------------------


def test_s1_a_deny_with_a_trailing_slash_covers_the_folder_and_below(
    world: World,
) -> None:
    home = world.b.home
    rules = _rules(deny=["~/Source/work/"])

    assert _ok(world, rules, home / "Source" / "personal" / "app")
    assert not _ok(world, rules, home / "Source" / "work")
    assert not _ok(world, rules, home / "Source" / "work" / "api")


def test_s1_a_deny_without_a_wildcard_covers_the_folder_and_below(world: World) -> None:
    home = world.b.home
    rules = _rules(deny=["~/Source/work"])

    assert not _ok(world, rules, home / "Source" / "work")
    assert not _ok(world, rules, home / "Source" / "work" / "api" / "deep")
    assert _ok(world, rules, home / "Source" / "workshop")


def test_s1_a_deny_matches_the_normpath_of_the_project_and_of_the_pattern(
    world: World,
) -> None:
    home = world.b.home

    assert not _ok(
        world, _rules(deny=["~/Source/work/**"]), f"{home}/Source/personal/../work/api"
    )
    assert not _ok(
        world, _rules(deny=["~/Source/x/../work/**"]), home / "Source" / "work" / "api"
    )


@no_symlinks
def test_s1_a_deny_on_a_symlink_denies_the_real_folder(world: World) -> None:
    home = world.b.home
    (home / "real" / "p").mkdir(parents=True)
    (home / "link").symlink_to(home / "real", target_is_directory=True)

    assert not _ok(world, _rules(deny=["~/link/**"]), home / "real" / "p")


@no_symlinks
def test_s1_a_deny_on_the_real_folder_denies_a_path_through_a_symlink(
    world: World,
) -> None:
    home = world.b.home
    (home / "real" / "p").mkdir(parents=True)
    (home / "link").symlink_to(home / "real", target_is_directory=True)

    assert not _ok(world, _rules(deny=["~/real/**"]), home / "link" / "p")


def test_s1_a_session_without_a_project_is_denied_when_a_deny_rule_exists(
    world: World,
) -> None:
    assert _ok(world, _rules(), None)
    assert not _ok(world, _rules(deny=["~/Source/work/**"]), None)


def test_s1_an_allow_with_a_trailing_slash_matches_like_without(world: World) -> None:
    home = world.b.home
    rules = _rules(allow=[{"project": "~/Source/personal/"}])

    assert _ok(world, rules, home / "Source" / "personal")
    # Allow keeps its exact match: no wildcard, no folders below.
    assert not _ok(world, rules, home / "Source" / "personal" / "app")


def test_s1_an_allow_matches_the_normpath_of_the_project(world: World) -> None:
    home = world.b.home

    assert _ok(
        world,
        _rules(allow=[{"project": "~/Source/personal/**"}]),
        f"{home}/Source/work/../personal/app",
    )


@no_symlinks
def test_s1_the_preview_drops_a_session_under_a_denied_symlink(world: World) -> None:
    b = world.b
    (b.home / "real" / "p").mkdir(parents=True)
    (b.home / "link").symlink_to(b.home / "real", target_is_directory=True)
    b.cc_session(S1, "realword", cwd=b.cwd("real", "p"))
    b.cc_session(S2, "otherword", cwd=b.cwd("Source", "personal", "two"))
    b.sync()
    b.export_all(deny=["~/link/**"])

    paths = _paths(world)

    assert any(p.endswith(f"/{S2}.jsonl") for p in paths)
    assert not any(p.endswith(f"/{S1}.jsonl") for p in paths)


# ---------------------------------------------------------------------------
# S2. Cowork deny uses the host folders
# ---------------------------------------------------------------------------


def _cowork(world: World, sid: str, folders: list[Any]) -> Path:
    b = world.b
    folder = b.cowork_session(sid, f"cowork{sid[-1]}word")
    sidecar = folder.with_suffix(".json")
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["cwd"] = "/sessions/synthetic-sandbox"
    data["userSelectedFolders"] = folders
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    return folder


def _cowork_in_preview(world: World, sid: str) -> bool:
    return any(f"local_{sid}" in p for p in _paths(world))


def test_s2_a_deny_on_a_host_folder_drops_the_cowork_session(world: World) -> None:
    b = world.b
    _cowork(world, SID_WORK, [b.cwd("Source", "work", "proj")])
    _cowork(world, SID_HOME, [b.cwd("Source", "personal", "proj")])
    b.sync()
    b.write_rules(
        mode="export", allow=[{"source": "cowork"}], deny=["~/Source/work/**"]
    )

    assert _cowork_in_preview(world, SID_HOME)
    assert not _cowork_in_preview(world, SID_WORK)


def test_s2_a_deny_on_any_one_host_folder_denies_the_session(world: World) -> None:
    b = world.b
    _cowork(
        world,
        SID_WORK,
        [b.cwd("Source", "personal", "proj"), b.cwd("Source", "work", "proj")],
    )
    _cowork(world, SID_HOME, [b.cwd("Source", "personal", "proj")])
    b.sync()
    b.write_rules(
        mode="export", allow=[{"source": "cowork"}], deny=["~/Source/work/**"]
    )

    assert _cowork_in_preview(world, SID_HOME)
    assert not _cowork_in_preview(world, SID_WORK)


def test_s2_a_cowork_session_without_a_host_folder_is_denied_when_a_deny_exists(
    world: World,
) -> None:
    b = world.b
    _cowork(world, SID_WORK, [])
    b.sync()
    b.write_rules(mode="export", allow=[{"source": "cowork"}])
    assert _cowork_in_preview(world, SID_WORK)

    b.write_rules(mode="export", allow=[{"source": "cowork"}], deny=["~/elsewhere/**"])

    assert not _cowork_in_preview(world, SID_WORK)


def test_s2_host_folders_can_be_objects_with_a_path(world: World) -> None:
    b = world.b
    work = b.cwd("Source", "work", "proj")
    home = b.cwd("Source", "personal", "proj")
    _cowork(world, SID_WORK, [{"path": work, "deviceName": "Synthetic Mac"}])
    # Both forms, plus items without a string path, which are ignored.
    _cowork(
        world,
        SID_HOME,
        [home, {"path": home, "deviceName": "Synthetic Mac"}, 42, {"deviceName": "x"}],
    )
    b.sync()
    b.write_rules(
        mode="export", allow=[{"source": "cowork"}], deny=["~/Source/work/**"]
    )

    assert _cowork_in_preview(world, SID_HOME)
    assert not _cowork_in_preview(world, SID_WORK)


# ---------------------------------------------------------------------------
# S3. Size limits on untrusted input
# ---------------------------------------------------------------------------


def test_s3_a_manifest_over_its_limit_refuses_the_bundle(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import machines

    dest = _bundle(world)
    monkeypatch.setattr(machines, "MAX_MANIFEST_BYTES", 64)

    err = _import_error(world, dest)

    assert err.reason == "too_large"
    assert "manifest.json" in str(err)
    assert _nothing_imported(world)


def test_s3_a_machine_json_over_its_limit_refuses_the_bundle(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import machines

    dest = _bundle(world)
    prefix, _ = bundle_manifest(dest)
    members = bundle_members(dest)
    size = len(members[prefix + "manifest.json"])
    marker = json.loads(members[prefix + "machine.json"])
    rewrite_member(
        dest,
        prefix + "machine.json",
        (json.dumps(marker) + " " * (size + 4000)).encode(),
    )
    monkeypatch.setattr(machines, "MAX_MANIFEST_BYTES", size + 2000)

    err = _import_error(world, dest)

    assert err.reason == "too_large"
    assert "machine.json" in str(err)
    assert _nothing_imported(world)


def test_s3_a_json_member_over_its_limit_refuses_the_bundle(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import machines

    world.b.cowork_session(SID_HOME, "jsonlimitword")
    dest = _bundle(world)
    monkeypatch.setattr(machines, "MAX_JSON_BYTES", 16)

    err = _import_error(world, dest)

    assert err.reason == "too_large"
    assert _nothing_imported(world)


def test_s3_a_jsonl_member_over_the_decoded_limit_refuses_the_bundle(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import archive_sync, machines

    dest = _bundle(world)
    monkeypatch.setattr(machines, "MAX_DECODED_BYTES", 32, raising=False)
    monkeypatch.setattr(archive_sync, "MAX_DECODED_BYTES", 32)

    err = _import_error(world, dest)

    assert err.reason == "too_large"
    assert _nothing_imported(world)


def test_s3_a_bundle_over_the_total_limit_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import machines

    dest = _bundle(world)
    monkeypatch.setattr(machines, "MAX_BUNDLE_BYTES", 100)

    err = _import_error(world, dest)

    assert err.reason == "too_large"
    assert _nothing_imported(world)


def test_s3_a_bundle_needs_its_size_plus_the_margin_in_free_space(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:

    from backend import machines

    dest = _bundle(world)
    # The files of the manifest; manifest.json and machine.json have their own cap.
    _, manifest = bundle_manifest(dest)
    total = sum(f["bytes"] for f in manifest["files"])
    monkeypatch.setattr(machines, "FREE_SPACE_MARGIN_BYTES", 1000)
    real = shutil.disk_usage
    free = {"value": total + 1000 - 1}

    def fake(path: Any) -> Any:
        u = real(path)
        return type(u)(u.total, u.used, free["value"])

    monkeypatch.setattr(shutil, "disk_usage", fake)

    err = _import_error(world, dest)
    assert err.reason == "too_large"
    assert _nothing_imported(world)

    free["value"] = total + 1000 + 1_000_000
    assert world.a.import_(dest).counts["copied"] >= 1


def test_s3_the_import_route_refuses_a_large_upload_with_413(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from backend import machines
    from backend.main import app

    monkeypatch.setattr(machines, "MAX_BUNDLE_BYTES", 100)
    world.a.activate()

    r = TestClient(app).post(
        "/api/machines/import",
        files={"file": ("big.cebundle", b"x" * 1000, "application/octet-stream")},
        headers=H,
    )

    assert r.status_code == 413
    assert _nothing_imported(world)


def _shared(world: World) -> None:
    for m in (world.a, world.b):
        m.write_config(shared_archive_dir=str(world.shared))


def _pull(world: World) -> Any:
    from backend import machines

    return machines.sync_shared_folder(world.a.settings(), "refresh", 10)


def _push(world: World) -> Path:
    from backend import machines

    b = world.b
    b.sync()
    b.export_all()
    _shared(world)
    machines.sync_shared_folder(b.settings(), "refresh", 10)
    folder = world.shared / "machines" / b.machine_id
    assert folder.is_dir()
    return folder


def _warnings_naming(report: Any, name: str) -> list[str]:
    return [w for w in report.warnings if name in w]


def test_s3_a_shared_json_file_over_its_limit_is_skipped_with_one_warning(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import machines

    world.b.cowork_session(SID_HOME, "sharedjsonword")
    folder = _push(world)
    [sidecar] = list(folder.rglob(f"local_{SID_HOME}.json"))
    sidecar.write_text(
        sidecar.read_text(encoding="utf-8") + " " * 5000, encoding="utf-8"
    )
    monkeypatch.setattr(machines, "MAX_JSON_BYTES", 2000)

    report = _pull(world)

    copy = world.a.foreign(world.b.machine_id)
    assert list(copy.rglob("audit.jsonl"))
    assert not list(copy.rglob(f"local_{SID_HOME}.json"))
    assert len(_warnings_naming(report, f"local_{SID_HOME}.json")) == 1


# ---------------------------------------------------------------------------
# S4. The shared folder never reaches outside itself
# ---------------------------------------------------------------------------


def _bad_shared(world: World, kind: str) -> str:
    b = world.b
    if kind == "relative":
        return "relative-shared"
    if kind == "data_dir":
        return str(b.data_dir)
    if kind == "inside":
        return str(b.data_dir / "shared")
    if kind == "ancestor":
        return str(b.data_dir.parent)
    link = world.tmp / "link-into-data"
    (b.data_dir / "inner").mkdir(parents=True, exist_ok=True)
    link.symlink_to(b.data_dir / "inner", target_is_directory=True)
    return str(link)


@pytest.mark.parametrize(
    "kind",
    [
        "relative",
        "data_dir",
        "inside",
        "ancestor",
        pytest.param("symlink", marks=no_symlinks),
    ],
)
def test_s4_a_bad_shared_folder_in_the_config_is_ignored_with_a_warning(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    from backend import machines

    b = world.b
    cwd = world.tmp / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    b.cc_session(S1, "badsharedword")
    b.sync()
    b.export_all()
    b.write_config(shared_archive_dir=_bad_shared(world, kind))
    before = (tree(b.data_dir.parent), tree(cwd))

    with caplog.at_level("WARNING"):
        machines.sync_shared_folder(b.settings(), "refresh", 10)

    assert (tree(b.data_dir.parent), tree(cwd)) == before
    assert [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "shared" in r.getMessage().lower()
    ]


@no_symlinks
def test_s4_removal_refuses_a_symlink_at_machines_or_at_the_own_folder(
    world: World,
) -> None:
    from backend import machines

    b = world.b
    b.ensure_id()
    elsewhere = world.tmp / "elsewhere"
    # Case 1: <shared>/machines is a symlink.
    (elsewhere / "m1" / b.machine_id).mkdir(parents=True)
    (elsewhere / "m1" / b.machine_id / "keep.txt").write_text("keep", encoding="utf-8")
    s1 = world.tmp / "shared-1"
    s1.mkdir()
    (s1 / "machines").symlink_to(elsewhere / "m1", target_is_directory=True)
    b.write_config(shared_archive_dir=str(s1))
    with pytest.raises(ValueError):
        machines.remove_own_shared_folder(b.settings(), confirm=b.machine_id)
    assert (elsewhere / "m1" / b.machine_id / "keep.txt").is_file()

    # Case 2: <shared>/machines/<own> is a symlink.
    (elsewhere / "own").mkdir()
    (elsewhere / "own" / "keep.txt").write_text("keep", encoding="utf-8")
    s2 = world.tmp / "shared-2"
    (s2 / "machines").mkdir(parents=True)
    (s2 / "machines" / b.machine_id).symlink_to(
        elsewhere / "own", target_is_directory=True
    )
    b.write_config(shared_archive_dir=str(s2))
    with pytest.raises(ValueError):
        machines.remove_own_shared_folder(b.settings(), confirm=b.machine_id)
    assert (elsewhere / "own" / "keep.txt").is_file()


@no_symlinks
def test_s4_removal_refuses_a_target_inside_the_data_folder(world: World) -> None:
    from backend import machines

    b = world.b
    b.cc_session(S1, "keepmyarchiveword")
    b.sync()
    own = b.data_dir / "machines" / b.machine_id
    before = tree(own)
    assert any(k.endswith(f"{S1}.jsonl") for k in before)
    link = world.tmp / "shared-is-data"
    link.symlink_to(b.data_dir, target_is_directory=True)
    b.write_config(shared_archive_dir=str(link))

    with pytest.raises(ValueError):
        machines.remove_own_shared_folder(b.settings(), confirm=b.machine_id)

    assert tree(own) == before


LEVELS = ["machines", "mid", "part", "folder", "file"]


@no_symlinks
@pytest.mark.parametrize("level", LEVELS)
def test_s4_the_pull_never_follows_a_symlink(world: World, level: str) -> None:
    b = world.b
    b.cc_session(S1, "linkedword", cwd=b.cwd("Source", "personal", "one"))
    b.cc_session(S2, "plainword", cwd=b.cwd("Source", "personal", "two"))
    folder = _push(world)
    [s1_file] = list(folder.rglob(f"{S1}.jsonl"))
    target = {
        "machines": world.shared / "machines",
        "mid": folder,
        "part": folder / "claude-code",
        "folder": s1_file.parent,
        "file": s1_file,
    }[level]
    outside = world.tmp / "outside" / target.name
    outside.parent.mkdir(parents=True)
    shutil.move(str(target), str(outside))
    target.symlink_to(outside, target_is_directory=outside.is_dir())

    report = _pull(world)

    copy = world.a.foreign(b.machine_id)
    assert not list(copy.rglob(f"{S1}.jsonl")) if copy.exists() else True
    if level in ("folder", "file"):
        assert list(copy.rglob(f"{S2}.jsonl")), "the pass goes on after a refused item"
    assert report is not None
    assert len(report.warnings) == 1, report.warnings


# ---------------------------------------------------------------------------
# S5. Bundles and other machines' copies
# ---------------------------------------------------------------------------


def test_s5_a_passphrase_on_a_plain_bundle_is_refused(world: World) -> None:
    dest = _bundle(world)

    err = _import_error(world, dest, passphrase=PASS)

    assert err.reason == "not_encrypted"
    assert str(err) == "You gave a passphrase, but this bundle is not encrypted."
    assert _nothing_imported(world)


def test_s5_a_changed_json_member_keeps_the_old_copy_as_a_revision(
    world: World,
) -> None:
    b = world.b
    b.cowork_session(SID_HOME, "revisionword", title="First synthetic title")
    world.a.import_(_bundle(world, "b1.cebundle", full=True))
    b.cowork_session(SID_HOME, "revisionword", title="Second synthetic title")
    b.sync()

    result = world.a.import_(_bundle(world, "b2.cebundle", full=True))

    copy = world.a.foreign(b.machine_id)
    [current] = list(copy.rglob(f"local_{SID_HOME}.json"))
    assert b"Second synthetic title" in current.read_bytes()
    revs = [p for p in copy.rglob(f"local_{SID_HOME}.rev-*.json")]
    assert len(revs) == 1, revs
    assert b"First synthetic title" in revs[0].read_bytes()
    assert result.counts["revisions"] >= 1


def _longer_on_b(world: World) -> tuple[str, str]:
    from backend.tests.builders import write_cc_jsonl

    a, b = world.a, world.b
    cwd = "/tmp/a2/round-two"
    entries = cc_entries(S1, "displaceword", cwd=cwd, ts="2026-11-20T10:00:00Z")
    write_cc_jsonl(b.cc_file(S1, cwd), entries)
    world.a.import_(_bundle(world))
    write_cc_jsonl(a.cc_file(S1, cwd), entries[:1])
    a.sync()
    return cwd, "done displaceword"


def test_s5_a_foreign_copy_never_displaces_a_live_session(world: World) -> None:
    _, only_on_b = _longer_on_b(world)

    row = world.a.rows("CLAUDE_CODE")[S1]

    assert row.machine_id == world.a.machine_id
    assert only_on_b not in world.a.detail_text(S1)


def test_s5_a_foreign_copy_never_displaces_this_machines_own_archive_copy(
    world: World,
) -> None:
    cwd, only_on_b = _longer_on_b(world)
    world.a.cc_file(S1, cwd).unlink()

    row = world.a.rows("CLAUDE_CODE")[S1]

    assert row.machine_id == world.a.machine_id
    assert only_on_b not in world.a.detail_text(S1)


T_SAME = "019b0a60-0000-7000-8000-0000000000ee"


def _codex_newer_on_b(world: World) -> Path:
    """Thread T_SAME on A (older) and on B (newer); A imports B's copy."""
    a, b = world.a, world.b
    b.codex_thread(T_SAME, "codexnewerbword", day="2026-11-20")
    world.a.import_(_bundle(world, "codex-b.cebundle"))
    rollout = a.codex_thread(T_SAME, "codexolderaword", day="2026-11-15")
    a.sync()
    a.ensure_id()  # a Codex copy alone makes no machine id
    return rollout


def test_s5_a_newer_foreign_codex_copy_never_displaces_the_live_codex_thread(
    world: World,
) -> None:
    _codex_newer_on_b(world)

    row = world.a.rows("CODEX")[T_SAME]

    assert row.machine_id == world.a.machine_id
    text = world.a.detail_text(T_SAME)
    assert "codexolderaword" in text
    assert "codexnewerbword" not in text


def test_s5_a_newer_foreign_codex_copy_never_displaces_this_machines_own_codex_archive(
    world: World,
) -> None:
    rollout = _codex_newer_on_b(world)
    rollout.unlink()  # the live rollout is gone; the own codex/<home_key>/ copy stays

    row = world.a.rows("CODEX")[T_SAME]

    assert row.machine_id == world.a.machine_id
    text = world.a.detail_text(T_SAME)
    assert "codexolderaword" in text
    assert "codexnewerbword" not in text


T_ONLY = "019b0a70-0000-7000-8000-0000000000ab"


def test_s5_a_codex_only_machine_gets_an_identity_and_its_codex_rows_carry_it(
    world: World,
) -> None:
    a = world.a
    a.codex_thread(T_ONLY, "codexonlyword")

    a.sync()

    assert (a.explorer_home / "machine.json").is_file()
    assert a.rows("CODEX")[T_ONLY].machine_id == a.machine_id


def test_s5_a_codex_only_machine_writes_no_identity_while_the_cloud_gate_is_closed(
    world: World,
) -> None:
    from backend.tests._machines_env import Machine, realpath

    d = Machine(
        "d", world.tmp / "machine-d", world.a.mp, world.tmp / "machine-d" / "codex"
    )
    (d.home / "Dropbox").mkdir()
    d.data_dir = d.home / "Dropbox" / "explorer" / "conversations"
    d.data_dir.mkdir(parents=True)
    d.codex_thread(T_ONLY, "codexgatedword")

    d.sync()
    assert not (d.explorer_home / "machine.json").exists()
    assert not (d.data_dir / "machines").exists()

    # With the consent, the same pass makes the identity.
    d.write_config(allow_writes_in_cloud_sync=realpath(d.data_dir))
    d.sync()
    assert (d.explorer_home / "machine.json").is_file()
    assert d.rows("CODEX")[T_ONLY].machine_id == d.machine_id


# ---------------------------------------------------------------------------
# S6. Small items
# ---------------------------------------------------------------------------


def test_s6_new_bundles_use_n_2_17_r_8_p_1_and_enough_maxmem(world: World) -> None:
    from backend import machines

    assert (machines._SCRYPT_LOG2_N, machines._SCRYPT_R, machines._SCRYPT_P) == (
        17,
        8,
        1,
    )
    # maxmem is large enough: the default parameters derive a key.
    assert len(machines._key("synthetic", b"\0" * 16, 17, 8, 1)) == 32


def _kdf(monkeypatch: pytest.MonkeyPatch, log2_n: int, r: int, p: int) -> None:
    from backend import machines

    monkeypatch.setattr(machines, "_SCRYPT_LOG2_N", log2_n)
    monkeypatch.setattr(machines, "_SCRYPT_R", r)
    monkeypatch.setattr(machines, "_SCRYPT_P", p)


@pytest.mark.parametrize(
    ("log2_n", "r", "p"),
    [(16, 8, 1), (18, 2, 1), (15, 1, 1), (15, 8, 16)],
    ids=["n-2-16", "n-2-18", "r-1", "p-16"],
)
def test_s6_decryption_reads_the_scrypt_parameters_from_the_header(
    world: World, monkeypatch: pytest.MonkeyPatch, log2_n: int, r: int, p: int
) -> None:
    with monkeypatch.context() as m:
        _kdf(m, log2_n, r, p)
        dest = _bundle(world, passphrase=PASS)

    result = world.a.import_(dest, passphrase=PASS)

    assert result.counts["copied"] >= 1


@pytest.mark.parametrize(
    ("log2_n", "r", "p"),
    [(14, 8, 1), (19, 2, 1), (15, 9, 1), (15, 8, 17)],
    ids=["n-below-2-15", "n-above-2-18", "r-above-8", "p-above-16"],
)
def test_s6_decryption_refuses_scrypt_parameters_out_of_range_before_scrypt_runs(
    world: World, monkeypatch: pytest.MonkeyPatch, log2_n: int, r: int, p: int
) -> None:
    import hashlib

    with monkeypatch.context() as m:
        _kdf(m, log2_n, r, p)
        dest = _bundle(world, passphrase=PASS)

    def no_scrypt(*args: Any, **kwargs: Any) -> bytes:
        raise AssertionError("scrypt ran on parameters out of range")

    monkeypatch.setattr(hashlib, "scrypt", no_scrypt)

    err = _import_error(world, dest, passphrase=PASS)

    assert err.reason == "bad_kdf"
    assert _nothing_imported(world)


def test_s6_a_header_that_scrypt_cannot_run_is_refused_as_a_bundle_error(
    world: World,
) -> None:
    # scrypt needs N < 2^(16 r) (RFC 7914): N = 2^18 with r = 1 is inside the
    # accepted ranges, but hashlib.scrypt raises ValueError on it. A crafted
    # header must give a BundleError, not an unhandled error (a 500 in the route).
    from backend import machines

    dest = _bundle(world, passphrase=PASS)
    raw = bytearray(dest.read_bytes())
    k = len(machines.BUNDLE_MAGIC) + len(machines._FORMAT_2)
    raw[k], raw[k + 1] = 18, 1
    dest.write_bytes(bytes(raw))

    err = _import_error(world, dest, passphrase=PASS)

    assert err.reason in ("bad_kdf", "bad_passphrase")
    assert _nothing_imported(world)


def test_s6_cli_machine_label_refuses_a_corrupt_config(world: World) -> None:
    from click.testing import CliRunner

    from cli.main import main

    a = world.a
    a.ensure_id()
    before = (a.explorer_home / "machine.json").read_bytes()
    a.write_corrupt_config()
    a.activate()

    result = CliRunner().invoke(main, ["machine", "label", "Home Mac"])

    assert result.exit_code == 1
    assert (a.explorer_home / "machine.json").read_bytes() == before


def test_s6_the_own_label_route_is_409_while_the_cloud_gate_is_closed(
    world: World,
) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.tests._machines_env import Machine

    d = Machine(
        "d", world.tmp / "machine-d", world.a.mp, world.tmp / "machine-d" / "codex"
    )
    (d.home / "Dropbox").mkdir()
    d.data_dir = d.home / "Dropbox" / "explorer" / "conversations"
    d.data_dir.mkdir(parents=True)
    mid = d.ensure_id()
    marker = d.data_dir / "machines" / mid / "machine.json"
    before = marker.read_bytes()
    d.activate()

    r = TestClient(app).put(
        "/api/machines/self/label", json={"label": "Cloud Mac"}, headers=H
    )

    assert r.status_code == 409
    assert "Dropbox" in r.text
    assert marker.read_bytes() == before
