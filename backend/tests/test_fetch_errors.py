"""Fetch error mapping + credentials age surfacing.

Build-1 from PLANS/explorer-improvements-build.md.

The fetch error pipeline used to only string-match "401" and surface
generic errors otherwise. Cloudflare blocks return 403 with a
'cf-mitigated' header, and an expired sessionKey can also return 403.
Both must map to a single actionable message: "Session expired or
Cloudflare-blocked. Re-run claude-explorer capture."

In addition, /api/fetch/status now exposes credentials_age_days so the
UI can warn when credentials are stale.
"""

from __future__ import annotations

import json
import os
import time

from backend.routers.fetch import classify_fetch_error


def test_classify_401_as_session_expired() -> None:
    msg = classify_fetch_error("401 Client Error: Unauthorized for url: https://...")
    assert "Re-run" in msg and "capture" in msg.lower()


def test_classify_403_as_session_expired() -> None:
    msg = classify_fetch_error("403 Client Error: Forbidden for url: https://...")
    assert "Re-run" in msg and "capture" in msg.lower()


def test_classify_cf_mitigated_as_session_expired() -> None:
    msg = classify_fetch_error(
        "403 Client Error: Forbidden | cf-mitigated: challenge"
    )
    assert "Re-run" in msg and "capture" in msg.lower()


def test_classify_other_500_passes_through() -> None:
    msg = classify_fetch_error("500 Internal Server Error")
    assert "Re-run" not in msg
    assert "500" in msg or "Internal" in msg


def test_classify_connection_error_passes_through() -> None:
    msg = classify_fetch_error("Connection refused")
    assert "Re-run" not in msg
    assert "Connection refused" in msg


def test_status_includes_credentials_age_days(client, tmp_path, monkeypatch) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"session_key": "sk", "org_id": "o"}))
    age_seconds = 20 * 24 * 3600
    old = time.time() - age_seconds
    os.utime(creds, (old, old))

    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )

    response = client.get("/api/fetch/status")
    assert response.status_code == 200
    body = response.json()
    assert body["has_credentials"] is True
    assert "credentials_age_days" in body
    assert 19 <= body["credentials_age_days"] <= 21


def test_status_credentials_age_none_when_missing(client, tmp_path, monkeypatch) -> None:
    creds = tmp_path / "no_creds_here.json"
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )

    response = client.get("/api/fetch/status")
    assert response.status_code == 200
    body = response.json()
    assert body["has_credentials"] is False
    assert body["credentials_age_days"] is None


# ---------------------------------------------------------------------------
# P4.4 — /api/fetch/conversation/<uuid> error bodies (each carries the
# constant string the frontend dispatches on, not just a status code).
# ---------------------------------------------------------------------------


def _v2_creds_blob():
    return {
        "schema_version": 2,
        "session_key": "sk-test",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-05-01T00:00:00+00:00",
        "orgs": [{
            "uuid": "11111111-1111-1111-1111-111111111111",
            "name": "Personal",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }],
        "primary_org_id": "11111111-1111-1111-1111-111111111111",
        "legacy_migration_target": "11111111-1111-1111-1111-111111111111",
        "org_id": "11111111-1111-1111-1111-111111111111",
    }


def _seed_creds(tmp_path, monkeypatch):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(json.dumps(_v2_creds_blob()))
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds_path, raising=True
    )
    return creds_path


def test__post_fetch_conversation__auth_error__returns_401_with_session_expired_message(
    client, tmp_path, monkeypatch
) -> None:
    """RFC-401 (P4.4). AUTH error path → 401 with SESSION_EXPIRED_MESSAGE detail."""
    from backend.routers import fetch as fetch_mod

    _seed_creds(tmp_path, monkeypatch)

    class _BoomFetcher:
        def __init__(self, **_kwargs): pass
        def fetch_conversation(self, _uuid):
            raise RuntimeError("401 Client Error: Unauthorized for url: https://...")

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _BoomFetcher)

    r = client.post("/api/fetch/conversation/abc-uuid-not-real")
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == fetch_mod.SESSION_EXPIRED_MESSAGE


def test__post_fetch_conversation__transient_error__returns_503_with_transient_message(
    client, tmp_path, monkeypatch
) -> None:
    """RFC-503 (P4.4). TRANSIENT error path → 503 with TRANSIENT_USER_MESSAGE detail."""
    from backend.routers import fetch as fetch_mod

    _seed_creds(tmp_path, monkeypatch)

    class _NetFlakeFetcher:
        def __init__(self, **_kwargs): pass
        def fetch_conversation(self, _uuid):
            # _classify_error checks substrings in the exception message;
            # a 503 message routes through TRANSIENT (fetch.py:98-99).
            raise RuntimeError("503 Service Unavailable from claude.ai")

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _NetFlakeFetcher)

    r = client.post("/api/fetch/conversation/abc-uuid-not-real")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == fetch_mod.TRANSIENT_USER_MESSAGE


def test__post_fetch_conversation__404_uuid_in_org_list__returns_conversation_gone_message(
    client, tmp_path, monkeypatch
) -> None:
    """RFC-404-GONE (P4.4). Empty/None response + UUID present in org list →
    404 with CONVERSATION_GONE_MESSAGE (deleted/archived disambiguation)."""
    from backend.routers import fetch as fetch_mod

    _seed_creds(tmp_path, monkeypatch)
    target_uuid = "11111111-2222-3333-4444-555555555555"

    class _GoneFetcher:
        def __init__(self, **_kwargs): pass
        def fetch_conversation(self, _uuid):
            return None
        def fetch_conversation_list(self):
            return [{"uuid": target_uuid, "name": "still here"}]

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _GoneFetcher)

    r = client.post(f"/api/fetch/conversation/{target_uuid}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == fetch_mod.CONVERSATION_GONE_MESSAGE


def test__post_fetch_conversation__404_uuid_missing_from_org_list__returns_cross_workspace_message(
    client, tmp_path, monkeypatch
) -> None:
    """RFC-404-XWORK (P4.4). Empty/None response + UUID NOT in org list →
    404 with CONVERSATION_CROSS_WORKSPACE_MESSAGE."""
    from backend.routers import fetch as fetch_mod

    _seed_creds(tmp_path, monkeypatch)
    missing_uuid = "00000000-aaaa-bbbb-cccc-deadbeefdead"

    class _XWorkFetcher:
        def __init__(self, **_kwargs): pass
        def fetch_conversation(self, _uuid):
            return None
        def fetch_conversation_list(self):
            return [{"uuid": "11111111-2222-3333-4444-555555555555", "name": "other"}]

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _XWorkFetcher)

    r = client.post(f"/api/fetch/conversation/{missing_uuid}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == fetch_mod.CONVERSATION_CROSS_WORKSPACE_MESSAGE


# ---------------------------------------------------------------------------
# P4.5 — /api/fetch/status boundary cases (zero-age, fresh creds).
# ---------------------------------------------------------------------------


def test__get_fetch_status__freshly_written_creds__age_days_is_zero(
    client, tmp_path, monkeypatch
) -> None:
    """STATUS-AGE-ZERO (P4.5). Freshly written creds.json → age_days == 0.

    Lower-bound boundary: age computation is `(now - mtime) // 86400`, so a
    creds file written within the last 24h must report 0, not None and not
    a negative value."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds_blob()))
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )

    r = client.get("/api/fetch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["has_credentials"] is True
    assert body["credentials_age_days"] == 0


def test__get_fetch_status__creds_present__age_is_non_negative_integer(
    client, tmp_path, monkeypatch
) -> None:
    """STATUS-AGE-NONNEG (P4.5). credentials_age_days is always a non-negative int
    when creds are present (never None, never negative, never a float)."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds_blob()))
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )

    r = client.get("/api/fetch/status")
    body = r.json()
    age = body["credentials_age_days"]
    assert isinstance(age, int), f"age must be int, got {type(age).__name__}"
    assert age >= 0, f"age must be non-negative, got {age}"


# ---------------------------------------------------------------------------
# Pydantic ↔ TS drift audit (Task B): FetchProgress.type Literal contract
# ---------------------------------------------------------------------------


def test__fetch_progress__type_field_is_closed_literal_union() -> None:
    """RED→GREEN: `FetchProgress.type` must be a closed `Literal` union
    mirroring the frontend `FetchProgress.type` in
    `frontend/src/components/fetch/FetchToast.tsx`. Pre-Task-B the
    backend declared `type: str` (any string accepted), while the
    frontend narrowed to a closed union — silent drift on either side
    was invisible.

    If a future PR adds a new SSE event type on the backend dict-build
    path WITHOUT also adding it to this Literal, the Pydantic model
    no longer documents the wire contract. This test pins the
    contract.
    """
    from pydantic import ValidationError

    from backend.routers.fetch import FetchProgress

    # GREEN: every documented type validates.
    documented_types = [
        "start",
        "progress",
        "complete",
        "error",
        "capture_start",
        "capture_waiting_login",
        "capture_done",
    ]
    for t in documented_types:
        FetchProgress(type=t, message="ok")  # must not raise

    # RED-direction: an unknown type must raise ValidationError. If the
    # field is still `type: str` (not Literal), this passes silently
    # and the test fails.
    try:
        FetchProgress(type="not_a_documented_type", message="oops")
    except ValidationError:
        pass
    else:
        raise AssertionError(
            "FetchProgress.type accepted an undocumented value — "
            "field must be a closed Literal union to lock the SSE contract."
        )


def test__fetch_progress__schema_documents_type_literal_in_openapi() -> None:
    """Companion to the runtime test: the JSON schema must list the
    Literal variants under `enum` so OpenAPI / TS codegen can see the
    closed set. A `type: str` field produces no `enum` — this test
    catches that drift via the model's own JSON schema.
    """
    from backend.routers.fetch import FetchProgress

    schema = FetchProgress.model_json_schema()
    type_field = schema.get("properties", {}).get("type", {})
    assert "enum" in type_field, (
        f"FetchProgress.type must surface as an enum in the JSON schema "
        f"(Literal[...] required); got {type_field!r}"
    )
    assert set(type_field["enum"]) >= {
        "start",
        "progress",
        "complete",
        "error",
        "capture_start",
        "capture_waiting_login",
        "capture_done",
    }, (
        f"FetchProgress.type enum missing required variants; got "
        f"{type_field['enum']!r}"
    )


def test__build_error_event__preserves_extra_fields_after_validation() -> None:
    """The SSE error dict carries `kind` and `retryable` — fields NOT
    on the Pydantic model. Any future change that pipes the dict
    through `FetchProgress.model_dump()` would silently strip them,
    breaking the frontend's `kind`/`retryable` handling. This test
    pins the contract: `_build_error_event` MUST emit these fields.
    """
    from backend.routers.fetch import _build_error_event

    event = _build_error_event("TRANSIENT", "503 Service Unavailable")
    assert event["type"] == "error"
    assert event["kind"] == "TRANSIENT"
    assert event["retryable"] is True
    assert event.get("message")

    auth_event = _build_error_event("AUTH", "401")
    assert auth_event["kind"] == "AUTH"
    assert auth_event["retryable"] is False
