"""Routes of the Machines settings (issue #92): labels, export rules, bundles, the shared folder.

Every POST and PUT follows the ``POST /api/shutdown`` rules: the header
``X-Claude-Explorer-Action: machines`` (which needs a preflight that the
server never answers) and an Origin check against the Host guard's
allowlist. A corrupt config refuses them (503).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import machines
from ..config import Settings, get_settings
from ..deps import refuse_if_config_corrupt
from ..write_gate import cloud_sync_block, cloud_sync_message, shared_archive_block
from .shutdown import _origin_allowed
from .write_consent import write_config_key

router = APIRouter()

ACTION_HEADER_VALUE = "machines"


def guard(
    request: Request,
    x_claude_explorer_action: str | None = Header(default=None),
) -> Settings:
    """The header and Origin checks (403), then the corrupt-config check (503)."""
    if x_claude_explorer_action != ACTION_HEADER_VALUE:
        raise HTTPException(status_code=403, detail=f"Missing header X-Claude-Explorer-Action: {ACTION_HEADER_VALUE}.")
    if not _origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Cross-origin request refused.")
    get_settings.cache_clear()  # the user can fix or break the config while we run
    return refuse_if_config_corrupt(get_settings())


def _block_json(block: tuple[str, Path] | None) -> dict[str, str] | None:
    return {"provider": block[0], "folder": str(block[1])} if block else None


def _shared_state(settings: Settings) -> dict[str, Any]:
    shared = settings.shared_archive_dir
    return {
        "shared_archive_dir": str(shared) if shared else None,
        "shared_archive_block": _block_json(shared_archive_block(settings)),
    }


@router.get("/machines", summary="The machines in this archive")
async def get_machines() -> dict[str, Any]:
    infos = await asyncio.to_thread(machines.list_machines)
    get_settings.cache_clear()
    settings = get_settings()
    return {
        "this_machine_id": next((i.machine_id for i in infos if i.is_this_machine), None),
        "machines": [i.model_dump() for i in infos],
        **_shared_state(settings),
    }


class LabelBody(BaseModel):
    label: str | None = None


def _label(fn: Any, *args: Any) -> machines.MachineInfo:
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except machines.WriteBlocked as exc:  # the cloud-sync gate of the data folder
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/machines/self/label", response_model=machines.MachineInfo, summary="Set this machine's label")
async def put_own_label(body: LabelBody, _: Settings = Depends(guard)) -> machines.MachineInfo:
    return _label(machines.set_machine_label, body.label)


@router.get("/machines/export-rules", response_model=machines.ExportRules, summary="This machine's export rules")
async def get_export_rules() -> machines.ExportRules:
    return machines.load_export_rules()


@router.put("/machines/export-rules", response_model=machines.ExportRules, summary="Save the export rules")
async def put_export_rules(rules: machines.ExportRules, _: Settings = Depends(guard)) -> machines.ExportRules:
    machines.save_export_rules(rules)
    return rules


@router.get("/machines/export-preview", response_model=machines.ExportPreview, summary="What an export holds now")
async def get_export_preview(full: bool = False) -> machines.ExportPreview:
    return await asyncio.to_thread(machines.preview_export, full=full)


class ExportBody(BaseModel):
    passphrase: str | None = None
    full: bool = False


@router.post("/machines/export", summary="Download a bundle of this machine's allowed sessions",
             responses={409: {"description": "Export is off or receive-only"}})
async def post_export(body: ExportBody, _: Settings = Depends(guard)) -> FileResponse:
    tmp = Path(tempfile.mkdtemp(prefix="claude-explorer-export-"))
    name = machines.default_bundle_name()
    try:
        await asyncio.to_thread(machines.export_bundle, tmp / name, passphrase=body.passphrase or None, full=body.full)
    except machines.BundleError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": str(exc)}) from exc
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return FileResponse(tmp / name, media_type="application/octet-stream", filename=name,
                        background=BackgroundTask(shutil.rmtree, tmp, True))


async def _reindex() -> None:
    """Make the imported sessions searchable now (as after a fetch)."""
    from .fetch import _reindex_search_after_fetch

    await _reindex_search_after_fetch()


@router.post("/machines/import", response_model=machines.ImportResult, summary="Import a bundle",
             responses={400: {"description": "Not a valid bundle (detail.reason)"},
                        409: {"description": "The data folder's cloud-sync gate is closed"},
                        413: {"description": "The upload is over MAX_BUNDLE_BYTES"},
                        503: {"description": "Config file is corrupt; refuse to write"}})
async def post_import(
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str | None, Form()] = None,
    settings: Settings = Depends(guard),
) -> machines.ImportResult:
    block = cloud_sync_block(settings)
    if block is not None:
        raise HTTPException(status_code=409, detail=cloud_sync_message(block))
    limit = machines.MAX_BUNDLE_BYTES
    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail=f"The bundle is over MAX_BUNDLE_BYTES ({limit} bytes).")
    fd, name = tempfile.mkstemp(prefix="claude-explorer-import-", suffix=machines.BUNDLE_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as out:
            if not await asyncio.to_thread(_copy_capped, file.file, out, limit):
                raise HTTPException(status_code=413, detail=f"The bundle is over MAX_BUNDLE_BYTES ({limit} bytes).")
        try:
            result = await asyncio.to_thread(machines.import_bundle, Path(name), passphrase=passphrase or None)
        except machines.BundleError as exc:
            raise HTTPException(status_code=400, detail={"reason": exc.reason, "message": str(exc)}) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        Path(name).unlink(missing_ok=True)
    await _reindex()
    return result


def _copy_capped(src: Any, out: Any, limit: int) -> bool:
    """Copy at most ``limit`` bytes; False when the upload is longer."""
    total = 0
    while block := src.read(1 << 20):
        total += len(block)
        if total > limit:
            return False
        out.write(block)
    return True


class SharedFolderBody(BaseModel):
    path: str | None = None


@router.put("/machines/shared-folder", summary="Set or clear the shared sync folder")
async def put_shared_folder(body: SharedFolderBody, settings: Settings = Depends(guard)) -> dict[str, Any]:
    raw = (body.path or "").strip()
    value: str | None = None
    if raw:
        path = Path(os.path.expanduser(raw))
        if not path.is_absolute():
            raise HTTPException(status_code=422, detail="The shared folder must be an absolute path.")
        real = Path(os.path.realpath(path))
        data = Path(os.path.realpath(settings.data_dir))
        if real == data or real.is_relative_to(data) or data.is_relative_to(real):
            raise HTTPException(status_code=422, detail="The shared folder must be outside the data folder.")
        value = str(path)
    write_config_key("shared_archive_dir", value)
    get_settings.cache_clear()
    return _shared_state(get_settings())


class RemoveBody(BaseModel):
    confirm: str


@router.post("/machines/shared-folder/remove", summary="Remove this machine's folder from the shared folder")
async def post_remove_shared(body: RemoveBody, settings: Settings = Depends(guard)) -> dict[str, bool]:
    try:
        await asyncio.to_thread(machines.remove_own_shared_folder, settings, confirm=body.confirm)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"removed": True}


# Last: "/machines/{machine_id}/label" must not catch "/machines/self/label".
@router.put("/machines/{machine_id}/label", response_model=machines.MachineInfo,
            summary="Set a local name for another machine")
async def put_local_label(machine_id: str, body: LabelBody, _: Settings = Depends(guard)) -> machines.MachineInfo:
    return _label(machines.set_local_label, machine_id, body.label)
