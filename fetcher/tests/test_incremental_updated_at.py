"""Incremental fetch must re-download a conversation the server has updated.

Earned 2026-09-21 on the maintainer's own archive. Incremental mode filtered
purely on UUID presence, so a conversation that was already on disk was never
downloaded again, however much it grew afterwards. Two real conversations had
drifted badly:

    Synology NAS   server 2026-09-11, local copy 2026-04-30,  670 messages missing
    emacs stuff    server 2026-08-18, local copy 2026-04-19,  106 messages missing

New conversations always arrived, which is why the gap stayed invisible: only
continued conversations went stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from fetcher.bulk_fetch import ClaudeFetcher, needs_refetch

ORG = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


def _make_fetcher(tmp_path: Path) -> ClaudeFetcher:
    return ClaudeFetcher(
        session_key="sk-test",
        orgs=[{"uuid": ORG, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        primary_org_id=ORG,
        output_dir=tmp_path / "conversations",
        files_dir=tmp_path / "files",
        download_files=False,
        delay=0.0,
    )


# --- the decision rule -----------------------------------------------------

def test_absent_locally_is_fetched() -> None:
    assert needs_refetch("2026-09-11T22:41:50.412613Z", None) is True


def test_server_newer_is_refetched() -> None:
    """The regression itself."""
    assert needs_refetch("2026-09-11T22:41:50.412613Z", "2026-04-30T00:00:00Z") is True


def test_same_timestamp_is_skipped() -> None:
    ts = "2026-09-11T22:41:50.412613Z"
    assert needs_refetch(ts, ts) is False


def test_local_newer_is_skipped() -> None:
    """Clock skew must not cause an endless re-fetch loop."""
    assert needs_refetch("2026-04-30T00:00:00Z", "2026-09-11T22:41:50.412613Z") is False


def test_missing_server_timestamp_is_fetched() -> None:
    """Without a server timestamp we cannot prove the local copy is current."""
    assert needs_refetch(None, "2026-04-30T00:00:00Z") is True


def test_equivalent_timestamps_in_different_formats_are_skipped() -> None:
    """`Z` and `+00:00` denote the same instant; string comparison would not."""
    assert needs_refetch("2026-09-11T22:41:50.412613Z",
                         "2026-09-11T22:41:50.412613+00:00") is False


def test_unparseable_timestamps_fall_back_to_inequality() -> None:
    """Garbage in must still be safe: differing values re-fetch, equal skip."""
    assert needs_refetch("not-a-date", "other-garbage") is True
    assert needs_refetch("not-a-date", "not-a-date") is False


# --- reading what is on disk ----------------------------------------------

def test_local_updated_at_reads_stored_value(tmp_path: Path) -> None:
    f = _make_fetcher(tmp_path)
    org_dir = f.output_dir / "by-org" / ORG
    org_dir.mkdir(parents=True)
    (org_dir / "u1.json").write_text(json.dumps({"uuid": "u1", "updated_at": "2026-04-30T00:00:00Z"}))

    got = f.local_updated_at_for_org(ORG, ["u1"])
    assert got == {"u1": "2026-04-30T00:00:00Z"}


def test_local_updated_at_omits_missing_and_corrupt(tmp_path: Path) -> None:
    """A missing or unreadable file must be absent, so the caller re-fetches."""
    f = _make_fetcher(tmp_path)
    org_dir = f.output_dir / "by-org" / ORG
    org_dir.mkdir(parents=True)
    (org_dir / "corrupt.json").write_text("{ this is not json")
    (org_dir / "nodate.json").write_text(json.dumps({"uuid": "nodate"}))

    got = f.local_updated_at_for_org(ORG, ["corrupt", "nodate", "absent"])
    assert got == {}


def test_local_updated_at_only_reads_requested_uuids(tmp_path: Path) -> None:
    """Cost must track the server list, not the whole local corpus."""
    f = _make_fetcher(tmp_path)
    org_dir = f.output_dir / "by-org" / ORG
    org_dir.mkdir(parents=True)
    for i in range(5):
        (org_dir / f"u{i}.json").write_text(json.dumps({"uuid": f"u{i}", "updated_at": "2026-01-01T00:00:00Z"}))

    got = f.local_updated_at_for_org(ORG, ["u1"])
    assert set(got) == {"u1"}


# --- the end-to-end scenario that bit the maintainer ----------------------

def test_synology_scenario_is_selected_for_refetch(tmp_path: Path) -> None:
    """A stale local copy plus a fresh server entry must be re-fetched."""
    f = _make_fetcher(tmp_path)
    org_dir = f.output_dir / "by-org" / ORG
    org_dir.mkdir(parents=True)
    (org_dir / "syn.json").write_text(json.dumps(
        {"uuid": "syn", "name": "Synology NAS", "updated_at": "2026-04-30T00:00:00Z"}))
    (org_dir / "cur.json").write_text(json.dumps(
        {"uuid": "cur", "name": "Current", "updated_at": "2026-09-01T00:00:00Z"}))

    server = [
        {"uuid": "syn", "name": "Synology NAS", "updated_at": "2026-09-11T22:41:50.412613Z"},
        {"uuid": "cur", "name": "Current", "updated_at": "2026-09-01T00:00:00Z"},
        {"uuid": "new", "name": "Brand new", "updated_at": "2026-09-20T00:00:00Z"},
    ]
    local = f.local_updated_at_for_org(ORG, [c["uuid"] for c in server])
    selected = [c["name"] for c in server if needs_refetch(c.get("updated_at"), local.get(c["uuid"]))]

    assert selected == ["Synology NAS", "Brand new"]
