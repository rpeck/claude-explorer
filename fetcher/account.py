"""Look up which Anthropic account the captured credentials belong to.

``GET https://claude.ai/api/account`` returns the signed-in user's profile,
the same identity claude.ai and Claude Desktop show at the bottom of their
sidebar. Claude Explorer shows it too, so a user with several accounts (a
work laptop and a home laptop, say) can see whose archive they are fetching.
"""

from __future__ import annotations

from typing import Any

from curl_cffi import requests as curl_requests

ACCOUNT_URL = "https://claude.ai/api/account"
REQUEST_TIMEOUT = 15.0


def fetch_account(
    session_key: str,
    cf_bm: str | None = None,
    cf_clearance: str | None = None,
) -> dict[str, Any]:
    """Return the raw ``/api/account`` body. Raises on HTTP or transport errors."""
    cookies = {"sessionKey": session_key}
    if cf_bm:
        cookies["__cf_bm"] = cf_bm
    if cf_clearance:
        cookies["cf_clearance"] = cf_clearance
    resp = curl_requests.get(
        ACCOUNT_URL, cookies=cookies, impersonate="chrome", timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def identity_from_account(raw: dict[str, Any]) -> dict[str, str | None]:
    """The display fields of an ``/api/account`` body, nothing else.

    The body also carries settings, feature flags and memberships; none of
    that leaves this function.
    """
    def text(key: str) -> str | None:
        value = raw.get(key)
        return value.strip() or None if isinstance(value, str) else None

    return {
        "account_uuid": text("uuid"),
        "email": text("email_address"),
        "full_name": text("full_name"),
        "display_name": text("display_name"),
    }


__all__ = ["ACCOUNT_URL", "fetch_account", "identity_from_account"]
