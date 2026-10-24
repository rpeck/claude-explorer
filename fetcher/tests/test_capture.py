"""Tests for the playwright capture path's v2 credentials behavior.

C2 of the cowork-multi-org plan. Covers NEW2-P0-θ (recapture preserves
manually-pinned primary_org_id) and NEW2-P0-β (v1 → v2 capture writes
legacy_migration_target).
"""

from __future__ import annotations

import json
from pathlib import Path

from fetcher.credentials import (
    CredentialsV2,
    OrgRef,
    load_credentials,
    save_credentials,
)
from fetcher.playwright_capture import _build_credentials


def _orgs(*pairs: tuple[str, str | None]) -> list[OrgRef]:
    """Helper. Each pair is (uuid, name)."""
    return [
        {
            "uuid": uuid,
            "name": name,
            "capabilities": ["chat"] if name else [],
            "seen_in_response": True,
        }
        for uuid, name in pairs
    ]


# ---------------------------------------------------------------------------
# _build_credentials: pure function, easy to test
# ---------------------------------------------------------------------------


def test_fresh_install_first_capture_writes_legacy_migration_target(tmp_path: Path) -> None:
    """No prior creds → first capture sets legacy_migration_target = primary.

    On a fresh install there is no v1 file, so any "legacy" untagged JSONs
    that ever appear in the data dir came from this same primary org. Setting
    legacy_migration_target = primary is correct.
    """
    creds_path = tmp_path / "credentials.json"
    creds = _build_credentials(
        creds_path=creds_path,
        session_key="sk-ant-fresh",
        cf_bm="bm",
        cf_clearance="cf",
        captured_at="2026-05-01T00:00:00+00:00",
        orgs=_orgs(("org-personal", "Personal")),
    )

    assert creds["primary_org_id"] == "org-personal"
    assert creds["legacy_migration_target"] == "org-personal"


def test_v1_to_v2_writes_legacy_migration_target(tmp_path: Path) -> None:
    """NEW2-P0-β. v1 creds with org_id: X → recapture as v2 → legacy_migration_target: X.

    Even when heuristic primary-org selection later picks a *different* org
    (e.g. user has 2 orgs and the capability hint picks Cowork), the
    legacy_migration_target stays pinned to X so migration of pre-multi-org
    untagged JSONs routes correctly.
    """
    creds_path = tmp_path / "credentials.json"
    # Lay down a v1 file (this is what every existing user has on disk).
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    with open(creds_path, "w") as f:
        json.dump(
            {
                "session_key": "old-key",
                "org_id": "org-original-personal",
                "cf_bm": "bm",
                "cf_clearance": "cf",
                "captured_at": "2026-03-09T19:17:39.570096+00:00",
            },
            f,
        )

    # Recapture: API now returns 2 orgs, of which "Cowork" was previously
    # unknown.
    creds = _build_credentials(
        creds_path=creds_path,
        session_key="sk-ant-fresh",
        cf_bm="bm",
        cf_clearance="cf",
        captured_at="2026-05-01T00:00:00+00:00",
        orgs=_orgs(("org-original-personal", "Personal"), ("org-cowork", "Cowork")),
    )

    assert creds["legacy_migration_target"] == "org-original-personal"


def test_inherits_primary_org_id_across_recapture(tmp_path: Path) -> None:
    """NEW2-P0-θ. Manually-pinned primary survives a recapture.

    Setup: user ran `claude-explorer set-primary-org org-cowork` so creds on
    disk have primary_org_id=org-cowork. A subsequent recapture must NOT
    silently re-pick by heuristic and overwrite that choice.
    """
    creds_path = tmp_path / "credentials.json"
    initial: CredentialsV2 = {
        "schema_version": 2,
        "session_key": "sk-ant-old",
        "cf_bm": "bm",
        "cf_clearance": "cf",
        "captured_at": "2026-04-30T00:00:00+00:00",
        "orgs": _orgs(("org-personal", "Personal"), ("org-cowork", "Cowork")),
        "primary_org_id": "org-cowork",  # manually pinned
        "legacy_migration_target": "org-personal",
        "org_id": "org-cowork",
    }
    save_credentials(initial, creds_path)

    # Recapture sees the same orgs.
    creds = _build_credentials(
        creds_path=creds_path,
        session_key="sk-ant-fresh",
        cf_bm="bm",
        cf_clearance="cf",
        captured_at="2026-05-01T00:00:00+00:00",
        orgs=_orgs(("org-personal", "Personal"), ("org-cowork", "Cowork")),
    )

    assert creds["primary_org_id"] == "org-cowork"
    # legacy_migration_target also inherited
    assert creds["legacy_migration_target"] == "org-personal"


def test_pinned_primary_dropped_when_no_longer_in_orgs(tmp_path: Path) -> None:
    """If the pinned primary is gone (user lost access), fall back to resolution."""
    creds_path = tmp_path / "credentials.json"
    initial: CredentialsV2 = {
        "schema_version": 2,
        "session_key": "sk-ant-old",
        "cf_bm": "bm",
        "cf_clearance": "cf",
        "captured_at": "2026-04-30T00:00:00+00:00",
        "orgs": _orgs(("org-personal", "Personal"), ("org-vanished-cowork", "Cowork")),
        "primary_org_id": "org-vanished-cowork",
        "legacy_migration_target": "org-personal",
        "org_id": "org-vanished-cowork",
    }
    save_credentials(initial, creds_path)

    creds = _build_credentials(
        creds_path=creds_path,
        session_key="sk-ant-fresh",
        cf_bm="bm",
        cf_clearance="cf",
        captured_at="2026-05-01T00:00:00+00:00",
        orgs=_orgs(("org-personal", "Personal")),  # cowork is gone
    )

    # Falls back to the only remaining org.
    assert creds["primary_org_id"] == "org-personal"
    # legacy_migration_target unchanged.
    assert creds["legacy_migration_target"] == "org-personal"


def test_capture_result_persists_via_save_credentials(tmp_path: Path) -> None:
    """The dict produced by _build_credentials must survive save_credentials.

    This is the contract that backend/routers/fetch.py and fetcher/cli.py
    rely on: capture returns a dict, the caller hands it to save_credentials,
    no transformation needed.
    """
    creds_path = tmp_path / "credentials.json"
    creds = _build_credentials(
        creds_path=creds_path,
        session_key="sk-ant-x",
        cf_bm="bm",
        cf_clearance="cf",
        captured_at="2026-05-01T00:00:00+00:00",
        orgs=_orgs(("org-a", "A"), ("org-b", "B")),
    )
    save_credentials(creds, creds_path)

    reloaded = load_credentials(creds_path)
    assert reloaded["primary_org_id"] == creds["primary_org_id"]
    assert {o["uuid"] for o in reloaded["orgs"]} == {"org-a", "org-b"}
