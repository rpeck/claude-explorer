"""``POST /api/write-consent/cloud-sync``: allow writes in a cloud-synced folder.

Plan D13 and §4.3: no Explorer writer puts data into a cloud-sync folder
until the user opts in. The UI calls this route only after the last step
of its consent dialog. The route writes ``allow_writes_in_cloud_sync`` into
``~/.claude-explorer/config.json``, with the resolved ``data_dir`` as its
value, so a moved folder closes the gate again. To revoke, delete the key.

The request must name the folder and the provider that detection finds
now, so a stale dialog cannot allow another folder. It follows the
``POST /api/shutdown`` rules: a custom header (which needs a preflight that
the server never answers) and an Origin check against the Host guard's
allowlist. A corrupt config refuses the write (503).
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from fetcher.atomic import atomic_write_bytes

from ..config import Settings, canonical_home_dir, get_settings, legacy_home_dir
from ..deps import refuse_if_config_corrupt
from ..write_gate import cloud_synced_target, same_path
from .shutdown import _origin_allowed

router = APIRouter()

ACTION_HEADER_VALUE = "allow-cloud-sync-writes"


class CloudSyncConsent(BaseModel):
    #: The resolved data directory that the dialog showed.
    data_dir: str
    #: The provider name that the dialog showed and the user typed.
    provider: str


def _read_config() -> dict:
    """The keys of the config that ``Settings`` read: canonical, then legacy."""
    for path in (canonical_home_dir() / "config.json", legacy_home_dir() / "config.json"):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    return {}


@router.post(
    "/write-consent/cloud-sync",
    summary="Allow Claude Explorer to write in a cloud-synced data folder",
    responses={
        400: {"description": "The data folder is not cloud-synced"},
        403: {"description": "Missing action header, or a cross-origin request"},
        409: {"description": "The folder or the provider differs from the current detection"},
        503: {"description": "Config file is corrupt; refuse to write"},
    },
)
async def allow_cloud_sync_writes(
    body: CloudSyncConsent,
    request: Request,
    x_claude_explorer_action: str | None = Header(default=None),
    settings: Settings = Depends(refuse_if_config_corrupt),
) -> dict[str, str]:
    if x_claude_explorer_action != ACTION_HEADER_VALUE:
        raise HTTPException(
            status_code=403,
            detail=f"Missing header X-Claude-Explorer-Action: {ACTION_HEADER_VALUE}.",
        )
    if not _origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Cross-origin consent refused.")

    target = cloud_synced_target(settings)
    if target is None:
        raise HTTPException(status_code=400, detail="The data folder is not cloud-synced.")
    provider, folder = target
    if not same_path(body.data_dir, settings.data_dir) or body.provider.casefold() != provider.casefold():
        raise HTTPException(
            status_code=409,
            detail=f"The data folder is now {settings.data_dir}, synced by {provider} ({folder}). "
            "Open the dialog again.",
        )

    allowed = os.path.realpath(settings.data_dir)
    config = _read_config()
    config["allow_writes_in_cloud_sync"] = allowed
    path = canonical_home_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    get_settings.cache_clear()
    return {"allow_writes_in_cloud_sync": allowed}
