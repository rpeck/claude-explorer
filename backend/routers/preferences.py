"""Preferences router (P3a).

Per-user preferences blob, persisted as a single JSON file under
``~/.claude-explorer/preferences.json``. Versioned envelope so we can evolve
the schema without breaking existing installs:

    {"version": 1, "data": {"theme": "dark", "keyboardMode": "vim", ...}}

PATCH is the primary write path used by the frontend: it deep-merges (top-
level overwrite per key) into the existing data so unrelated keys are
preserved when a single setting is toggled. PUT replaces the whole blob and
is exposed for completeness / future use.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import orjson

from fetcher.credentials import harden_path_permissions
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..deps import refuse_if_config_corrupt


router = APIRouter(prefix="/preferences", tags=["preferences"])

# In-process lock that guards the read-modify-write window for PATCH/PUT.
# A single backend process is the deployment model, so a threading.Lock
# is sufficient and simpler than fcntl.flock.
_write_lock = threading.Lock()

PREFS_VERSION = 1


def _resolve_path() -> Path:
    """Resolve preferences file location.

    Lives in the parent of the configured data dir, i.e.
    ``~/.claude-explorer/preferences.json`` for the default
    ``~/.claude-explorer/conversations`` data dir.
    """
    settings = get_settings()
    return settings.data_dir.parent / "preferences.json"


def _read_blob() -> dict[str, Any]:
    path = _resolve_path()
    if not path.exists():
        return {"version": PREFS_VERSION, "data": {}}
    try:
        raw = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return {"version": PREFS_VERSION, "data": {}}
    if not isinstance(raw, dict):
        return {"version": PREFS_VERSION, "data": {}}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    version = raw.get("version") if isinstance(raw.get("version"), int) else PREFS_VERSION
    return {"version": version, "data": data}


def _write_atomic(blob: dict[str, Any]) -> None:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(
            orjson.dumps(blob, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE)
        )
        # Windows ignores mode bits, so route through the shared helper
        # that also sets an NTFS ACL there.
        harden_path_permissions(tmp)
        os.replace(tmp, path)
    except BaseException:
        # If anything between write and replace raises, the .tmp file would
        # otherwise leak in the user's data dir. Best-effort cleanup; we
        # re-raise so the failure surfaces.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


class PreferencesEnvelope(BaseModel):
    version: int = Field(default=PREFS_VERSION)
    data: dict[str, Any] = Field(default_factory=dict)


class PreferencesWrite(BaseModel):
    # ``extra='forbid'`` turns a typo'd top-level field (e.g. a frontend bug
    # writing ``themee`` at the root instead of inside ``data``) into a 422
    # at the wire boundary instead of a silent no-op write. The ``data``
    # field itself stays ``dict[str, Any]`` so unknown KEYS INSIDE ``data``
    # keep working — forward-compat for future preference keys is preserved
    # while the envelope shape is locked. See
    # ``test_preferences.test_patch_preferences_{unknown,typo}_field_returns_422``.
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "",
    response_model=PreferencesEnvelope,
    summary="Get the full preferences blob (versioned envelope)",
)
async def get_preferences() -> PreferencesEnvelope:
    blob = _read_blob()
    return PreferencesEnvelope(version=blob["version"], data=blob["data"])


@router.put(
    "",
    response_model=PreferencesEnvelope,
    summary="Replace the whole preferences blob",
    # Layer 2 of PLANS/2026.05.18-config-corruption-safe-mode.md:
    # refuse writes when config.json is corrupt. GET is unchanged.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def put_preferences(payload: PreferencesWrite) -> PreferencesEnvelope:
    if not isinstance(payload.data, dict):
        raise HTTPException(status_code=400, detail="`data` must be an object")
    with _write_lock:
        blob = {"version": PREFS_VERSION, "data": dict(payload.data)}
        _write_atomic(blob)
    return PreferencesEnvelope(version=blob["version"], data=blob["data"])


@router.patch(
    "",
    response_model=PreferencesEnvelope,
    summary="Top-level merge into existing preferences blob (per-key overwrite)",
    # See put_preferences for Layer-2 gate rationale.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def patch_preferences(payload: PreferencesWrite) -> PreferencesEnvelope:
    if not isinstance(payload.data, dict):
        raise HTTPException(status_code=400, detail="`data` must be an object")
    with _write_lock:
        existing = _read_blob()
        merged: dict[str, Any] = dict(existing.get("data", {}))
        # Top-level overwrite per key — nested objects are values, not merge
        # targets, per the P3a spec.
        for k, v in payload.data.items():
            merged[k] = v
        blob = {"version": PREFS_VERSION, "data": merged}
        _write_atomic(blob)
    return PreferencesEnvelope(version=blob["version"], data=blob["data"])
