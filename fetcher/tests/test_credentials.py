"""Tests for fetcher.credentials.

Covers cowork-multi-org Commit 1 (NEW3-P0-C, NEW-P0-A, NEW-P1-H, NEW3-P2-A,
NEW2-P2-α). See PLANS/cowork-multi-org.md.

The module under test is the SOLE reader/writer of credentials.json. No other
module in the project may touch the file directly after C1 lands; a final-step
grep audit (in C5) enforces this.
"""

from __future__ import annotations

import json
import stat
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from fetcher.credentials import (
    CredentialsCorruptError,
    CredentialsV2,
    OrgRef,
    load_credentials,
    merge_orgs_and_save,
    save_credentials,
    wipe_credentials,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _v1_creds(org_id: str = "org-personal-uuid") -> dict:
    """Legacy v1 credentials shape — what the user currently has on disk."""
    return {
        "session_key": "sk-ant-test-session-key",
        "org_id": org_id,
        "cf_bm": "cf_bm_value",
        "cf_clearance": "cf_clearance_value",
        "captured_at": "2026-03-09T19:17:39.570096+00:00",
    }


def _v2_creds(
    primary: str = "org-personal-uuid",
    extra_orgs: list[OrgRef] | None = None,
    legacy_target: str | None = "org-personal-uuid",
) -> CredentialsV2:
    orgs: list[OrgRef] = [
        {
            "uuid": primary,
            "name": "Personal",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }
    ]
    if extra_orgs:
        orgs.extend(extra_orgs)
    return {
        "schema_version": 2,
        "session_key": "sk-ant-test-session-key",
        "cf_bm": "cf_bm_value",
        "cf_clearance": "cf_clearance_value",
        "captured_at": "2026-03-09T19:17:39.570096+00:00",
        "orgs": orgs,
        "primary_org_id": primary,
        "legacy_migration_target": legacy_target,
        "org_id": primary,
    }


def _write_v1_file(path: Path, **overrides) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    creds = _v1_creds(**overrides)
    with open(path, "w") as f:
        json.dump(creds, f)


# ---------------------------------------------------------------------------
# v1 -> v2 in-memory upgrade (NEW3-P0-C)
# ---------------------------------------------------------------------------


def test_v1_loads_as_v2_in_memory(tmp_path: Path) -> None:
    """Loading a v1 file yields a CredentialsV2; on-disk file is unchanged."""
    creds_path = tmp_path / "credentials.json"
    _write_v1_file(creds_path, org_id="org-aaa")

    raw_before = creds_path.read_bytes()
    creds = load_credentials(creds_path)
    raw_after = creds_path.read_bytes()

    # In-memory shape is v2:
    assert creds["schema_version"] == 2
    assert creds["primary_org_id"] == "org-aaa"
    assert creds["org_id"] == "org-aaa"  # legacy mirror
    assert len(creds["orgs"]) == 1
    assert creds["orgs"][0]["uuid"] == "org-aaa"
    assert creds["orgs"][0]["name"] is None
    assert creds["orgs"][0]["seen_in_response"] is False
    # Disk is untouched:
    assert raw_before == raw_after


def test_v1_load_synthesizes_legacy_migration_target_in_memory(tmp_path: Path) -> None:
    """NEW3-P0-C. load_credentials must surface legacy_migration_target == old org_id."""
    creds_path = tmp_path / "credentials.json"
    _write_v1_file(creds_path, org_id="org-original-personal")

    creds = load_credentials(creds_path)
    assert creds["legacy_migration_target"] == "org-original-personal"


def test_v2_loads_unchanged(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    written = _v2_creds()
    with open(creds_path, "w") as f:
        json.dump(written, f)

    loaded = load_credentials(creds_path)
    assert loaded["schema_version"] == 2
    assert loaded["primary_org_id"] == written["primary_org_id"]
    assert loaded["legacy_migration_target"] == written["legacy_migration_target"]


def test_load_missing_file_raises(tmp_path: Path) -> None:
    """The /api/orgs route depends on FileNotFoundError to return 'authenticated: false'."""
    with pytest.raises(FileNotFoundError):
        load_credentials(tmp_path / "nonexistent.json")


def test_load_corrupt_file_raises(tmp_path: Path) -> None:
    """Distinguishing corrupt from missing lets the route emit a specific error."""
    creds_path = tmp_path / "credentials.json"
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text("{not valid json")

    with pytest.raises(CredentialsCorruptError):
        load_credentials(creds_path)


# ---------------------------------------------------------------------------
# Atomic write semantics (P0-5, NEW3-P2-A, NEW2-P2-α)
# ---------------------------------------------------------------------------


def test_save_writes_v2_schema(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    creds = _v2_creds()
    save_credentials(creds, creds_path)

    on_disk = json.loads(creds_path.read_text())
    assert on_disk["schema_version"] == 2
    assert on_disk["primary_org_id"] == "org-personal-uuid"


def test_perms_0600(tmp_path: Path) -> None:
    """NEW-P1-H. Saved file is mode 0o600 (Unix only)."""
    if sys.platform.startswith("win"):
        pytest.skip("Windows skipped")
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(), creds_path)

    mode = stat.S_IMODE(creds_path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_parent_dir_0700(tmp_path: Path) -> None:
    if sys.platform.startswith("win"):
        pytest.skip("Windows skipped")
    creds_path = tmp_path / "subdir" / "credentials.json"
    save_credentials(_v2_creds(), creds_path)

    parent_mode = stat.S_IMODE(creds_path.parent.stat().st_mode)
    assert parent_mode == 0o700, f"expected 0o700, got {oct(parent_mode)}"


def test_invalid_schema_rejected(tmp_path: Path) -> None:
    """save_credentials({}) raises before touching disk."""
    creds_path = tmp_path / "credentials.json"
    with pytest.raises(Exception):  # ValidationError or similar
        save_credentials({}, creds_path)  # type: ignore[arg-type]
    assert not creds_path.exists()


def test_invalid_primary_not_in_orgs_rejected(tmp_path: Path) -> None:
    """primary_org_id must be present in the orgs array."""
    creds_path = tmp_path / "credentials.json"
    bad = _v2_creds()
    bad["primary_org_id"] = "uuid-not-in-orgs"
    with pytest.raises(Exception):
        save_credentials(bad, creds_path)
    assert not creds_path.exists()


def test_invalid_empty_session_key_rejected(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    bad = _v2_creds()
    bad["session_key"] = ""
    with pytest.raises(Exception):
        save_credentials(bad, creds_path)


def test_invalid_empty_orgs_rejected(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    bad = _v2_creds()
    bad["orgs"] = []
    with pytest.raises(Exception):
        save_credentials(bad, creds_path)


# ---------------------------------------------------------------------------
# .bak lifecycle (NEW-P1-H, NEW3-P2-A)
# ---------------------------------------------------------------------------


def test_first_save_creates_no_bak(tmp_path: Path) -> None:
    """No .bak on first save — there's no prior version to back up."""
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(), creds_path)

    assert creds_path.exists()
    assert not creds_path.with_suffix(".json.bak").exists()
    assert not creds_path.with_suffix(".json.bak.prev").exists()


def test_second_save_creates_one_bak(tmp_path: Path) -> None:
    """After two saves: live + exactly one .bak (holding V1)."""
    creds_path = tmp_path / "credentials.json"
    v1 = _v2_creds(primary="org-v1-uuid")
    v2 = _v2_creds(primary="org-v2-uuid")
    save_credentials(v1, creds_path)
    save_credentials(v2, creds_path)

    assert creds_path.exists()
    bak = creds_path.with_suffix(".json.bak")
    assert bak.exists()
    bak_data = json.loads(bak.read_text())
    assert bak_data["primary_org_id"] == "org-v1-uuid"

    live_data = json.loads(creds_path.read_text())
    assert live_data["primary_org_id"] == "org-v2-uuid"


def test_bak_deleted_on_next_save(tmp_path: Path) -> None:
    """NEW-P1-H. After every successful save, exactly one backup exists.

    No .bak.prev should leak past a successful save.
    """
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(primary="org-v1-uuid"), creds_path)
    save_credentials(_v2_creds(primary="org-v2-uuid"), creds_path)
    save_credentials(_v2_creds(primary="org-v3-uuid"), creds_path)

    assert not creds_path.with_suffix(".json.bak.prev").exists(), (
        ".bak.prev should be deleted after each successful save"
    )
    bak = creds_path.with_suffix(".json.bak")
    assert bak.exists()
    bak_data = json.loads(bak.read_text())
    # .bak holds the IMMEDIATELY prior version — V2, not V1.
    assert bak_data["primary_org_id"] == "org-v2-uuid"


def test_bak_lifecycle_matches_pseudocode(tmp_path: Path) -> None:
    """NEW3-P2-A. After two consecutive saves: exactly one .bak (holding V1),
    no .bak.prev, live holds V2."""
    creds_path = tmp_path / "credentials.json"
    v1 = _v2_creds(primary="org-v1-uuid")
    v2 = _v2_creds(primary="org-v2-uuid")
    save_credentials(v1, creds_path)
    save_credentials(v2, creds_path)

    assert json.loads(creds_path.read_text())["primary_org_id"] == "org-v2-uuid"
    assert json.loads(creds_path.with_suffix(".json.bak").read_text())["primary_org_id"] == "org-v1-uuid"
    assert not creds_path.with_suffix(".json.bak.prev").exists()


# ---------------------------------------------------------------------------
# Concurrency: lost-update prevention (NEW-P0-A)
# ---------------------------------------------------------------------------


def _worker_merge(args: tuple[str, list[OrgRef]]) -> None:
    """Helper invoked in subprocess by ProcessPoolExecutor."""
    creds_path_str, new_orgs = args
    from fetcher.credentials import merge_orgs_and_save

    merge_orgs_and_save(new_orgs, Path(creds_path_str))


def test_lost_update_race_prevented(tmp_path: Path) -> None:
    """NEW-P0-A. Two processes merge disjoint org sets; final file contains union.

    Without portalocker, the lost-update race causes one writer's orgs to vanish.
    Threads in the same process don't reproduce this — must use processes.
    """
    creds_path = tmp_path / "credentials.json"
    # Seed a v2 file with one org
    save_credentials(_v2_creds(primary="org-personal-uuid"), creds_path)

    new_orgs_a: list[OrgRef] = [
        {"uuid": "org-cowork-aaa", "name": "Cowork-A", "capabilities": ["chat"], "seen_in_response": True}
    ]
    new_orgs_b: list[OrgRef] = [
        {"uuid": "org-cowork-bbb", "name": "Cowork-B", "capabilities": ["chat"], "seen_in_response": True}
    ]

    with ProcessPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(_worker_merge, (str(creds_path), new_orgs_a)),
            pool.submit(_worker_merge, (str(creds_path), new_orgs_b)),
        ]
        for f in futs:
            f.result()

    final = load_credentials(creds_path)
    uuids = {o["uuid"] for o in final["orgs"]}
    assert "org-personal-uuid" in uuids
    assert "org-cowork-aaa" in uuids, f"Lost update: cowork-aaa missing from {uuids}"
    assert "org-cowork-bbb" in uuids, f"Lost update: cowork-bbb missing from {uuids}"


def test_concurrent_writes_no_corruption(tmp_path: Path) -> None:
    """Two writers race; final file is always valid JSON matching exactly one
    writer's intent (no half-merged blob)."""
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(primary="org-shared-uuid"), creds_path)

    new_orgs_a: list[OrgRef] = [{"uuid": "org-a", "name": "A", "capabilities": [], "seen_in_response": True}]
    new_orgs_b: list[OrgRef] = [{"uuid": "org-b", "name": "B", "capabilities": [], "seen_in_response": True}]

    with ProcessPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(_worker_merge, (str(creds_path), new_orgs_a)),
            pool.submit(_worker_merge, (str(creds_path), new_orgs_b)),
        ]
        for f in futs:
            f.result()

    # File parses as valid JSON
    data = json.loads(creds_path.read_text())
    assert data["schema_version"] == 2
    # Shared org always preserved
    assert any(o["uuid"] == "org-shared-uuid" for o in data["orgs"])


# ---------------------------------------------------------------------------
# merge_orgs_and_save semantics
# ---------------------------------------------------------------------------


def test_merge_unions_orgs_by_uuid(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(primary="org-personal-uuid"), creds_path)

    new_orgs: list[OrgRef] = [
        {"uuid": "org-cowork-uuid", "name": "Cowork", "capabilities": ["chat"], "seen_in_response": True},
    ]
    result = merge_orgs_and_save(new_orgs, creds_path)
    uuids = {o["uuid"] for o in result["orgs"]}
    assert uuids == {"org-personal-uuid", "org-cowork-uuid"}


def test_merge_prefers_seen_in_response(tmp_path: Path) -> None:
    """When two records refer to the same uuid, prefer seen_in_response=True
    so URL-only fallbacks don't overwrite real names."""
    creds_path = tmp_path / "credentials.json"
    initial = _v2_creds()
    # Add a URL-only org (no name, not seen in /api/organizations)
    initial["orgs"].append({
        "uuid": "org-cowork-uuid",
        "name": None,
        "capabilities": [],
        "seen_in_response": False,
    })
    save_credentials(initial, creds_path)

    # Now merge in a real /api/organizations record for the same UUID
    new_orgs: list[OrgRef] = [
        {"uuid": "org-cowork-uuid", "name": "Real Cowork Name", "capabilities": ["chat"], "seen_in_response": True},
    ]
    result = merge_orgs_and_save(new_orgs, creds_path)

    cowork = next(o for o in result["orgs"] if o["uuid"] == "org-cowork-uuid")
    assert cowork["name"] == "Real Cowork Name"
    assert cowork["seen_in_response"] is True


def test_merge_keeps_existing_when_new_is_url_only(tmp_path: Path) -> None:
    """Reverse of above: existing seen_in_response=True must NOT be downgraded
    by an incoming URL-only ref."""
    creds_path = tmp_path / "credentials.json"
    initial = _v2_creds()
    initial["orgs"].append({
        "uuid": "org-cowork-uuid",
        "name": "Real Cowork",
        "capabilities": ["chat"],
        "seen_in_response": True,
    })
    save_credentials(initial, creds_path)

    new_orgs: list[OrgRef] = [
        {"uuid": "org-cowork-uuid", "name": None, "capabilities": [], "seen_in_response": False},
    ]
    result = merge_orgs_and_save(new_orgs, creds_path)

    cowork = next(o for o in result["orgs"] if o["uuid"] == "org-cowork-uuid")
    assert cowork["name"] == "Real Cowork"
    assert cowork["seen_in_response"] is True


def test_merge_requires_existing_creds(tmp_path: Path) -> None:
    """merge_orgs_and_save raises FileNotFoundError if no creds exist.

    mitm is an enricher, not a bootstrapper — it cannot synthesize a
    session_key on its own.
    """
    creds_path = tmp_path / "credentials.json"
    new_orgs: list[OrgRef] = [{"uuid": "org-x", "name": None, "capabilities": [], "seen_in_response": False}]
    with pytest.raises(FileNotFoundError):
        merge_orgs_and_save(new_orgs, creds_path)


# ---------------------------------------------------------------------------
# wipe_credentials (NEW-P1-H)
# ---------------------------------------------------------------------------


def test_wipe_creds_removes_all_artifacts(tmp_path: Path) -> None:
    """NEW-P1-H. After save, manually create .bak.prev + .lock + .tmp residue;
    wipe_credentials removes all of them."""
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(), creds_path)
    save_credentials(_v2_creds(primary="org-v2-uuid"), creds_path)

    # Ensure .bak exists; manually fabricate residue
    (tmp_path / "credentials.json.bak.prev").write_text("{}")
    (tmp_path / "credentials.json.tmp").write_text("{}")
    (tmp_path / "credentials.json.bak.tmp").write_text("{}")

    wipe_credentials(creds_path)

    assert not creds_path.exists()
    assert not (tmp_path / "credentials.json.bak").exists()
    assert not (tmp_path / "credentials.json.bak.prev").exists()
    assert not (tmp_path / "credentials.json.tmp").exists()
    assert not (tmp_path / "credentials.json.bak.tmp").exists()
    # .lock may legitimately persist as an empty file on some platforms
    # (it's the lock target, not state) — wipe should remove it but it's
    # OK if it's already gone.
    assert not (tmp_path / "credentials.json.lock").exists()


def test_wipe_creds_idempotent(tmp_path: Path) -> None:
    creds_path = tmp_path / "credentials.json"
    # Wiping a nonexistent file should not raise
    wipe_credentials(creds_path)
    wipe_credentials(creds_path)


# ---------------------------------------------------------------------------
# Crash recovery: live file never disappears (Python Expert finding)
# ---------------------------------------------------------------------------


def test_live_file_present_throughout_save(tmp_path: Path, monkeypatch) -> None:
    """The live credentials.json must never momentarily disappear during a save.

    Concurrent unlocked readers (e.g. /api/orgs route handler) would otherwise
    observe FileNotFoundError and falsely report 'authenticated: false'.

    We monkeypatch os.replace to assert the live file exists before each
    rename of tmp -> live.
    """
    creds_path = tmp_path / "credentials.json"
    save_credentials(_v2_creds(primary="org-v1-uuid"), creds_path)

    # Now do a second save with monkeypatched os.replace that records
    # whether the live file was visible right before each os.replace call.
    import os as _os

    real_replace = _os.replace
    snapshots: list[bool] = []

    def spy_replace(src, dst, *args, **kwargs):
        # Right before this rename, the live credentials path must exist.
        snapshots.append(creds_path.exists())
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("fetcher.credentials.os.replace", spy_replace)
    save_credentials(_v2_creds(primary="org-v2-uuid"), creds_path)

    # The live file must have been present at every os.replace call.
    assert all(snapshots), (
        f"Live credentials.json was missing during a save sequence. snapshots={snapshots}"
    )
