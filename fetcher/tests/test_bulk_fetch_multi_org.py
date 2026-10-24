"""Tests for ClaudeFetcher.run_all_orgs() — the multi-org fetch loop.

C5 of cowork-multi-org. Covers the minimum viable path needed for the user's
primary + Cowork orgs to fetch correctly into the per-org subdir layout.

Larger spec items deferred to a follow-up commit (long-backoff heartbeats,
single-org NO_ACCESSIBLE_ORGS guardrail UX, full `unlock-fetch` CLI) are
out of scope for these tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fetcher.bulk_fetch import ClaudeFetcher, FetchAuthError

PERSONAL = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
COWORK = "0c0c170b-1234-5678-90ab-cdef00000000"


def _make_fetcher(tmp_path: Path, primary: str = PERSONAL, with_cowork: bool = True) -> ClaudeFetcher:
    orgs = [{"uuid": primary, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}]
    if with_cowork:
        orgs.append({"uuid": COWORK, "name": "Cowork", "capabilities": ["chat"], "seen_in_response": True})
    return ClaudeFetcher(
        session_key="sk-test",
        orgs=orgs,
        primary_org_id=primary,
        output_dir=tmp_path / "conversations",
        files_dir=tmp_path / "files",
        download_files=False,
        delay=0.0,
    )


def _conv(uuid: str, name: str = "Test") -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "model": "claude-sonnet-4-6",
        "created_at": "2024-03-01T12:00:00Z",
        "updated_at": "2024-03-01T13:00:00Z",
    }


# ---------------------------------------------------------------------------
# Happy path: fetch from both orgs, files end up in per-org subdirs
# ---------------------------------------------------------------------------


def test_run_all_orgs_writes_per_org_subdirs(tmp_path: Path) -> None:
    fetcher = _make_fetcher(tmp_path)

    fetched_per_org: dict[str, list[dict]] = {
        PERSONAL: [_conv("11111111-1111-2222-3333-444444444444", "Personal Conv")],
        COWORK: [_conv("22222222-1111-2222-3333-444444444444", "Cowork Conv")],
    }

    def fake_list(self):
        return fetched_per_org[self.current_org["uuid"]]

    def fake_fetch(self, uuid):
        for convs in fetched_per_org.values():
            for c in convs:
                if c["uuid"] == uuid:
                    return c
        return None

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    personal_path = tmp_path / "conversations" / "by-org" / PERSONAL / "11111111-1111-2222-3333-444444444444.json"
    cowork_path = tmp_path / "conversations" / "by-org" / COWORK / "22222222-1111-2222-3333-444444444444.json"
    assert personal_path.exists(), f"missing: {personal_path}"
    assert cowork_path.exists(), f"missing: {cowork_path}"

    # Each file carries the right organization_id
    assert json.loads(personal_path.read_text())["organization_id"] == PERSONAL
    assert json.loads(cowork_path.read_text())["organization_id"] == COWORK


def test_run_all_orgs_index_records_each_org(tmp_path: Path) -> None:
    """_index.json reflects status: ok for both orgs after a clean run."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        return [_conv(f"{self.current_org['uuid'][:8]}-1111-2222-3333-444444444444", "X")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    assert idx["schema_version"] == 2
    assert len(idx["orgs"]) == 2
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    assert by_id[PERSONAL]["status"] == "ok"
    assert by_id[COWORK]["status"] == "ok"
    assert by_id[PERSONAL]["fetched_count"] == 1
    assert by_id[COWORK]["fetched_count"] == 1


def test_secondary_403_records_status_and_continues(tmp_path: Path) -> None:
    """Secondary org 403: status=skipped, primary's data unaffected."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("403 Forbidden")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        # Cowork is secondary — should be recorded as skipped, run continues.
        fetcher.run_all_orgs()

    personal_path = tmp_path / "conversations" / "by-org" / PERSONAL / "11111111-1111-2222-3333-444444444444.json"
    assert personal_path.exists(), "primary's data should survive secondary failure"

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    assert by_id[PERSONAL]["status"] == "ok"
    assert by_id[COWORK]["status"] == "skipped"
    # A1-hunt: legacy `error_code` replaced by `(error_kind, http_status)`.
    assert by_id[COWORK]["error_kind"] == "ORG_FORBIDDEN"
    assert by_id[COWORK]["http_status"] == 403


def test_run_all_orgs_preserves_last_successful_on_failure(tmp_path: Path) -> None:
    """Run 1 succeeds for COWORK with N. Run 2 fails for COWORK. Index keeps
    last_successful_fetched_count from run 1."""
    fetcher = _make_fetcher(tmp_path)

    # Run 1: both orgs succeed
    def fake_list_run1(self):
        return [_conv(f"{self.current_org['uuid'][:8]}-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list_run1), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    # Run 2: COWORK fails
    def fake_list_run2(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("403 Forbidden")
        return [_conv(f"{self.current_org['uuid'][:8]}-1111-2222-3333-444444444444")]

    fetcher2 = _make_fetcher(tmp_path)  # fresh instance, same dir
    fetcher2.incremental = False  # force re-fetch
    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list_run2), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher2.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    cowork_entry = next(o for o in idx["orgs"] if o["org_id"] == COWORK)
    assert cowork_entry["status"] == "skipped"
    assert cowork_entry["fetched_count"] == 0
    assert cowork_entry["last_successful_fetched_count"] == 1
    assert cowork_entry["last_successful_fetched_at"] is not None


def test_existing_pairs_dedup_across_orgs(tmp_path: Path) -> None:
    """Same UUID in both orgs: both files saved (no silent overwrite — Council P0-2)."""
    fetcher = _make_fetcher(tmp_path)
    shared_uuid = "11111111-1111-2222-3333-444444444444"

    def fake_list(self):
        return [_conv(shared_uuid)]

    def fake_fetch(self, uuid):
        return _conv(uuid, name=f"Conv from {self.current_org['name']}")

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    p1 = tmp_path / "conversations" / "by-org" / PERSONAL / f"{shared_uuid}.json"
    p2 = tmp_path / "conversations" / "by-org" / COWORK / f"{shared_uuid}.json"
    assert p1.exists()
    assert p2.exists()
    assert json.loads(p1.read_text())["name"] == "Conv from Personal"
    assert json.loads(p2.read_text())["name"] == "Conv from Cowork"


def test_primary_401_hard_aborts(tmp_path: Path) -> None:
    """401 on primary = session expired. Hard abort, do not continue."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == PERSONAL:
            raise FetchAuthError("401 Unauthorized")
        return [_conv("22222222-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch), \
         pytest.raises(FetchAuthError):
        fetcher.run_all_orgs()

    # Cowork must NOT have been fetched
    cowork_path = tmp_path / "conversations" / "by-org" / COWORK / "22222222-1111-2222-3333-444444444444.json"
    assert not cowork_path.exists()


def test_scoped_org_restores_state_after_exception(tmp_path: Path) -> None:
    """Per Python Expert: _scoped_org context manager must restore state."""
    fetcher = _make_fetcher(tmp_path)
    initial = fetcher.current_org

    cowork_org = next(o for o in fetcher.orgs if o["uuid"] == COWORK)

    # _scoped_org should temporarily switch then restore.
    with fetcher._scoped_org(cowork_org):
        assert fetcher.current_org["uuid"] == COWORK
    assert fetcher.current_org["uuid"] == initial["uuid"]

    # And restore even after an exception.
    try:
        with fetcher._scoped_org(cowork_org):
            assert fetcher.current_org["uuid"] == COWORK
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert fetcher.current_org["uuid"] == initial["uuid"]
