"""``/api/orgs`` endpoint — workspace selector source.

cowork-multi-org C6 / Council P1-2 + NEW-P0-C: the sidebar's workspace
selector reads from credentials.json (always known *before* any conversation
is fetched) rather than from ``useConversations({}).data`` (which would force
a 5-10k row reduction every render and flip set membership during streaming
SSE fetches).

Three-state response:

* ``200 {authenticated: true, orgs: [...]}`` — creds present + parseable.
* ``200 {authenticated: false, orgs: []}`` — creds file absent. Returns 200
  rather than 404 so the frontend's global ApiError toast doesn't fire.
* ``503 {detail: "<string>"}`` — creds present but unreadable; the user
  needs to wipe and recapture.

Council code-review B+D batch (2026-05-21) unified the corrupt branch
with ``files.py``'s response for the same condition. Pre-council the
shape was ``500 {detail: {error: "credentials_corrupt", message: ...}}``
— the ONLY HTTPException site in the backend with a dict-shaped
``detail``. Forty other sites use string ``detail``. The FE
``getOrgs`` / ``useOrgs`` consumer never read ``detail.error`` (it
just throws ``ApiError(status, response.text())``), so the
unification is a wire-shape tightening with no known caller
breakage. See ``backend/tests/test_orgs.py`` for the regression
suite that pins the new contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from fetcher.credentials import (
    DEFAULT_CREDENTIALS_PATH,
    CredentialsCorruptError,
    load_credentials,
)

from ..models import Org, OrgsResponse

log = logging.getLogger(__name__)

router = APIRouter()


# Synthetic "_claude_code" pseudo-org is filtered out — it's a source, not
# a tenant (P1-1 orthogonality). No real claude.ai org id starts with an
# underscore.
def _is_synthetic(uuid: str) -> bool:
    return uuid.startswith("_")


@router.get(
    "/orgs",
    response_model=OrgsResponse,
    summary="List workspaces (orgs) available to the current credentials",
    responses={
        503: {"description": "Credentials file is corrupt; re-run capture"},
    },
)
def get_orgs() -> OrgsResponse:
    """Return the list of workspaces available to the user.

    See module docstring for the three-state response shape. Returns
    a typed ``OrgsResponse`` (Task B Pydantic↔TS drift audit, 2026-05-18)
    so the OpenAPI schema documents the wire shape and the frontend
    ``OrgsResponse`` interface in ``lib/types.ts`` has a Pydantic
    counterpart that catches future drift.
    """
    if not DEFAULT_CREDENTIALS_PATH.exists():
        return OrgsResponse(authenticated=False, orgs=[])

    try:
        creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
    except CredentialsCorruptError as e:
        # Council code-review B1+B3 unification (2026-05-21):
        #   * 500 → 503: this is "service unavailable due to required
        #     local dependency invalid", not a coding fault. Matches
        #     files.py:71 which returns 503 for the same condition.
        #   * dict detail → string detail: matches every other
        #     HTTPException site in the backend (40+ call sites). The
        #     FE `useOrgs` consumer never inspected `detail.error` —
        #     it relies on a thrown ApiError(status, response.text()),
        #     so the string carries the diagnostic intact.
        # The original CredentialsCorruptError message is preserved so
        # the user can see WHICH field / parse failure happened.
        log.warning("credentials.json is corrupt: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"Credentials file is corrupt: {e}. Re-run capture to refresh credentials.",
        )

    primary = creds.get("primary_org_id")
    out: list[Org] = []
    for org in creds.get("orgs", []):
        uuid = org.get("uuid")
        if not uuid or _is_synthetic(uuid):
            continue
        out.append(Org(
            org_id=uuid,
            name=org.get("name"),
            is_primary=uuid == primary,
        ))
    return OrgsResponse(authenticated=True, orgs=out)
