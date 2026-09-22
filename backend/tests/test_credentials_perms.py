"""On-disk permission-bit regression tests for preferences + credentials.

Frame: REGRESSION PREVENTION. The implementations are already correct
(``preferences.py:71`` chmods the temp file to ``0o600`` before
``os.replace``; ``credentials.py:303`` does the same). This file pins
that contract so a future refactor of the atomic-write path can't
silently drop the chmod (the kind of slip that doesn't break any
functional test but exposes secrets to a co-tenant on disk).

Spec-driven discipline (CLAUDE-TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * ``PLANS/2026.05.07-frontend-api-contract.md``
        (``PREF-PATCH-PERMS``, ``PREF-PUT-PERMS``, ``RFR-CRED-PERMS``)
      * ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` (P2.7)
      * ``backend/routers/preferences.py`` (under test)
      * ``fetcher/credentials.py`` (under test)
      * ``backend/tests/conftest.py`` (``isolated_data_dir``)

Lives in a NEW file (not extending ``test_preferences.py`` /
``test_orgs.py``) per the P2.7 task spec — avoids merge conflicts with
parallel agents that may be modifying those files for other tiers.
``test_preferences.py:test_file_mode_0600`` already covers PATCH; this
file covers PUT (currently uncovered) and the credentials path
end-to-end.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mode_octal(p: Path) -> int:
    """Return ``st_mode & 0o777`` (the permission bits only)."""

    return stat.S_IMODE(os.stat(p).st_mode)


def assert_owner_only(p: Path) -> None:
    """Assert the path is readable by its owner alone, per platform rules.

    POSIX carries this in the mode bits. Windows ignores mode bits and uses
    an NTFS access control list, so there the check reads the ACL and
    requires that no broad principal appears in it.
    """
    if sys.platform == "win32":
        out = subprocess.run(
            ["icacls", str(p)], capture_output=True, text=True, timeout=30
        ).stdout
        for principal in ("Everyone", "BUILTIN\\Users", "AUTHENTICATED USERS"):
            assert principal not in out, f"{p} still grants {principal}:\n{out}"
        return
    mode = _mode_octal(p)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)} on {p}"


def _make_v2_creds() -> dict:
    """Build a minimal but ``_validate``-passing CredentialsV2 dict."""

    org_uuid = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    return {
        "schema_version": 2,
        "session_key": "sk-ant-sid01-fake-test-key",
        "cf_bm": "fake-cf-bm",
        "cf_clearance": "fake-cf-clearance",
        "captured_at": "2026-05-08T00:00:00Z",
        "orgs": [
            {
                "uuid": org_uuid,
                "name": "Personal",
                "capabilities": ["chat"],
                "seen_in_response": True,
            }
        ],
        "primary_org_id": org_uuid,
        "legacy_migration_target": None,
        "org_id": org_uuid,
    }


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@pytest.fixture
def prefs_client(isolated_data_dir: Path) -> tuple[TestClient, Path]:
    """TestClient pinned to the isolated data dir; preferences live one
    level up at ``<isolated_data_dir>.parent / "preferences.json"``.
    """

    from backend.main import app

    prefs_path = isolated_data_dir.parent / "preferences.json"
    return TestClient(app), prefs_path


def test__patch_preferences__on_disk__has_mode_0o600(
    prefs_client: tuple[TestClient, Path],
) -> None:
    """PREF-PATCH-PERMS regression.

    After a successful PATCH, ``preferences.json`` MUST end up with
    mode ``0o600``. The atomic-write path chmods the temp file before
    ``os.replace`` so the mode survives the rename — this test pins
    that invariant.
    """

    client, prefs_path = prefs_client
    resp = client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    assert resp.status_code == 200, resp.text
    assert prefs_path.exists()
    assert_owner_only(prefs_path)


def test__put_preferences__on_disk__has_mode_0o600(
    prefs_client: tuple[TestClient, Path],
) -> None:
    """PREF-PUT-PERMS regression.

    Currently uncovered by ``test_preferences.py``: PUT goes through
    the same ``_write_atomic`` path, so the mode contract MUST be
    identical to PATCH. Pin it explicitly so a future refactor that
    splits the write paths can't drop the chmod on PUT only.
    """

    client, prefs_path = prefs_client
    resp = client.put("/api/preferences", json={"data": {"theme": "light"}})
    assert resp.status_code == 200, resp.text
    assert prefs_path.exists()
    assert_owner_only(prefs_path)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test__save_credentials__on_disk__has_mode_0o600(tmp_path: Path) -> None:
    """RFR-CRED-PERMS regression.

    ``fetcher.credentials.save_credentials`` writes the JSON via the
    ``_unlocked_save`` atomic-write recipe, which chmods the temp file
    to ``0o600`` before ``os.replace`` (``credentials.py:303``). The
    final on-disk file MUST inherit that mode.
    """

    from fetcher.credentials import save_credentials

    creds_path = tmp_path / "credentials.json"
    save_credentials(_make_v2_creds(), path=creds_path)

    assert creds_path.exists()
    assert_owner_only(creds_path)


def test__save_credentials__bak_file__has_mode_0o600(tmp_path: Path) -> None:
    """RFR-CRED-PERMS (defense-in-depth on the ``.bak`` file).

    Two consecutive ``save_credentials`` calls populate the ``.bak``
    file (Step 3 of ``_unlocked_save``). The ``.bak`` is created from
    ``shutil.copyfile`` of the previous live file then renamed atomically;
    the impl explicitly chmods ``.bak.tmp`` to ``0o600`` before rename
    (``credentials.py:313``). The backup contains the same secrets as
    the live file, so its mode contract MUST match the live file.

    This pins the negative-space side of the contract: a future
    refactor that drops the chmod on the backup path leaves credentials
    secrets readable to other users on disk.
    """

    from fetcher.credentials import save_credentials

    creds_path = tmp_path / "credentials.json"
    save_credentials(_make_v2_creds(), path=creds_path)
    # Second save populates the .bak file from the first save's bytes.
    save_credentials(_make_v2_creds(), path=creds_path)

    bak_path = creds_path.with_suffix(".json.bak")
    assert bak_path.exists(), f".bak file not produced at {bak_path}"
    assert_owner_only(bak_path)
