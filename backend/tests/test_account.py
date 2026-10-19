"""``GET /api/account``: the signed-in Anthropic identity for the sidebar.

Contract: ``backend/models.py:AccountResponse`` and UX.md → "Signed-in
account (sidebar footer)".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ORG = "11111111-1111-4111-8111-111111111111"

RAW_ACCOUNT = {
    "uuid": "00000000-0000-4000-8000-0000000000a1",
    "email_address": "ada@example.com",
    "full_name": "Ada Lovelace @ Engines.co",
    "display_name": "Ada Lovelace @ Engines.co",
    # Fields that must never reach the response.
    "settings": {"enabled_web_search": True},
    "memberships": [{"organization": {"uuid": ORG}}],
    "statsig": {"user": {}},
}


def _write_creds(path: Path, session_key: str = "sk-test-fake-key", orgs=None) -> None:
    path.write_text(json.dumps({
        "schema_version": 2,
        "session_key": session_key,
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-10-13T15:47:00+00:00",
        "orgs": orgs if orgs is not None else [
            {"uuid": ORG, "name": "Analytical Engines", "capabilities": ["chat"], "seen_in_response": True},
        ],
        "primary_org_id": ORG,
        "legacy_migration_target": ORG,
        "org_id": ORG,
    }))


class FakeHTTPError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP Error {status}")
        self.response = type("R", (), {"status_code": status})()


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_fetch(session_key, cf_bm=None, cf_clearance=None):
        calls.append(session_key)
        return RAW_ACCOUNT

    monkeypatch.setattr("fetcher.account.fetch_account", fake_fetch)
    return calls


def test_no_credentials_is_signed_out(
    client: TestClient, _isolated_credentials_path: Path, lookups: list[str]
) -> None:
    r = client.get("/api/account")
    assert r.status_code == 200
    assert r.json() == {
        "authenticated": False, "account_uuid": None, "email": None,
        "full_name": None, "display_name": None, "org_name": None, "error": None,
    }
    assert lookups == []


def test_identity_and_primary_workspace(
    client: TestClient, _isolated_credentials_path: Path, lookups: list[str]
) -> None:
    _write_creds(_isolated_credentials_path)

    r = client.get("/api/account")

    assert r.json() == {
        "authenticated": True,
        "account_uuid": "00000000-0000-4000-8000-0000000000a1",
        "email": "ada@example.com",
        "full_name": "Ada Lovelace @ Engines.co",
        "display_name": "Ada Lovelace @ Engines.co",
        "org_name": "Analytical Engines",
        "error": None,
    }


def test_lookup_is_cached_per_session_key(
    client: TestClient, _isolated_credentials_path: Path, lookups: list[str]
) -> None:
    _write_creds(_isolated_credentials_path, session_key="sk-test-first")
    client.get("/api/account")
    client.get("/api/account")
    assert lookups == ["sk-test-first"]

    # A recapture (new session key) is looked up again.
    _write_creds(_isolated_credentials_path, session_key="sk-test-second")
    client.get("/api/account")
    assert lookups == ["sk-test-first", "sk-test-second"]


def test_expired_session_reports_an_actionable_error(
    client: TestClient, _isolated_credentials_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_creds(_isolated_credentials_path)

    def expired(*_a, **_k):
        raise FakeHTTPError(401)

    monkeypatch.setattr("fetcher.account.fetch_account", expired)

    body = client.get("/api/account").json()

    assert body["authenticated"] is True
    assert body["org_name"] == "Analytical Engines"
    assert body["email"] is None
    assert body["error"] == "Session expired. Click Refresh to sign in again."


def test_network_failure_reports_offline(
    client: TestClient, _isolated_credentials_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_creds(_isolated_credentials_path)

    def offline(*_a, **_k):
        raise OSError("Could not resolve host: claude.ai")

    monkeypatch.setattr("fetcher.account.fetch_account", offline)

    body = client.get("/api/account").json()

    assert body["error"] == "Could not reach claude.ai to look up the signed-in account."


def test_disable_flag_skips_the_network(
    client: TestClient, _isolated_credentials_path: Path, lookups: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_creds(_isolated_credentials_path)
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_ACCOUNT_LOOKUP", "1")

    body = client.get("/api/account").json()

    assert lookups == []
    assert body["authenticated"] is True and body["org_name"] == "Analytical Engines"
    assert body["email"] is None and body["error"] is None


def test_corrupt_credentials_is_503(
    client: TestClient, _isolated_credentials_path: Path, lookups: list[str]
) -> None:
    _isolated_credentials_path.write_text("{not json")

    r = client.get("/api/account")

    assert r.status_code == 503
    assert "Credentials file is corrupt" in r.json()["detail"]
    assert lookups == []
