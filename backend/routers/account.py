"""``/api/account`` endpoint — the signed-in Anthropic identity.

claude.ai and Claude Desktop show the signed-in account at the bottom of
their sidebar. Claude Explorer does the same, so a user with more than one
account can see whose archive the Refresh button fetches. (A work account
and a personal account have separate histories; fetching with the "wrong"
one looks like a broken fetch.)

The identity comes from ``GET https://claude.ai/api/account`` with the
captured session cookie. One lookup per session key per process: the result
is cached in memory under a SHA-256 of the session key, so a recapture (new
key) is looked up again and nothing identifying is written to disk. A
failed lookup is cached for a short time only, so an offline start recovers
on its own.

Set ``CLAUDE_EXPLORER_DISABLE_ACCOUNT_LOOKUP=1`` to skip the network call
(the e2e backend does, because it reads the developer's real credentials).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from fastapi import APIRouter, HTTPException

from fetcher import account as account_api
from fetcher.credentials import (
    CredentialsCorruptError,
    DEFAULT_CREDENTIALS_PATH,
    load_credentials,
)

from ..models import AccountResponse


log = logging.getLogger(__name__)

router = APIRouter()

#: Seconds a successful lookup stays fresh. Names rarely change.
SUCCESS_TTL = 6 * 60 * 60
#: Seconds before a failed lookup is retried.
FAILURE_TTL = 60

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Forget every cached identity (tests, and after a recapture)."""
    with _cache_lock:
        _cache.clear()


def _lookup(session_key: str, cf_bm: str | None, cf_clearance: str | None) -> dict:
    """Identity fields, or ``{"error": ...}``, cached per session key."""
    key = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            stored_at, value = hit
            ttl = FAILURE_TTL if "error" in value else SUCCESS_TTL
            if now - stored_at < ttl:
                return value

    try:
        raw = account_api.fetch_account(session_key, cf_bm, cf_clearance)
        value: dict = account_api.identity_from_account(raw)
    except Exception as exc:  # noqa: BLE001  any failure is shown, never raised
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            message = "Session expired. Click Refresh to sign in again."
        else:
            message = "Could not reach claude.ai to look up the signed-in account."
        log.info("account lookup failed (HTTP %s): %s", status, exc)
        value = {"error": message}

    with _cache_lock:
        _cache[key] = (now, value)
    return value


@router.get(
    "/account",
    response_model=AccountResponse,
    summary="The Anthropic account the captured credentials belong to",
    responses={503: {"description": "Credentials file is corrupt; re-run capture"}},
)
def get_account() -> AccountResponse:
    """Return the signed-in identity. See the module docstring."""
    if not DEFAULT_CREDENTIALS_PATH.exists():
        return AccountResponse(authenticated=False)

    try:
        creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
    except CredentialsCorruptError as e:
        # Same contract as /api/orgs for the same condition.
        log.warning("credentials.json is corrupt: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"Credentials file is corrupt: {e}. Re-run capture to refresh credentials.",
        )

    primary = creds.get("primary_org_id") or creds.get("org_id")
    org_name = next(
        (o.get("name") for o in creds.get("orgs", []) if o.get("uuid") == primary),
        None,
    )

    session_key = creds.get("session_key")
    if not session_key or os.environ.get("CLAUDE_EXPLORER_DISABLE_ACCOUNT_LOOKUP") == "1":
        return AccountResponse(authenticated=True, org_name=org_name)

    identity = _lookup(session_key, creds.get("cf_bm"), creds.get("cf_clearance"))
    return AccountResponse(authenticated=True, org_name=org_name, **identity)
