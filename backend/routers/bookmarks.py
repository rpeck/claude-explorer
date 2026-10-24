"""Bookmarks router (Build-4).

Per-message bookmarks with notes, persisted to a single JSON file under the
Claude Explorer data directory. Schema:

    {
      "bookmarks": [
        {
          "id": "<uuid>",
          "conversation_id": "<uuid>",
          "message_uuid": "<uuid>",
          "source": "claude_desktop" | "claude_code",
          "created_at": "<iso8601>",
          "note": "<user-supplied string>",
          "snippet": "<auto-truncated message text, ~140 chars>"
        }
      ]
    }
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..deps import refuse_if_config_corrupt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


def _resolve_path() -> Path:
    """Resolve the bookmark file location.

    Honors CLAUDE_EXPLORER_BOOKMARKS_FILE for tests; otherwise stores
    under ~/.claude-explorer/ alongside the existing data dir. Legacy
    ``~/.claude-exporter/bookmarks.json`` is read as a fallback if the
    canonical path is missing (one-release deprecation window — the
    backend's lifespan migration will move the file to the canonical
    location on its next startup).
    """
    env = os.environ.get("CLAUDE_EXPLORER_BOOKMARKS_FILE")
    if env:
        return Path(env)
    canonical = Path.home() / ".claude-explorer" / "bookmarks.json"
    if canonical.exists():
        return canonical
    legacy = Path.home() / ".claude-exporter" / "bookmarks.json"
    if legacy.exists():
        return legacy
    # Neither exists yet — default to canonical for writes.
    return canonical


class Bookmark(BaseModel):
    id: str
    conversation_id: str
    message_uuid: str
    source: Literal["claude_desktop", "claude_code"]
    created_at: str
    note: str = ""
    snippet: str = ""


class BookmarkCreate(BaseModel):
    # ``extra='forbid'`` mirrors ``PreferencesWrite``: a typo'd field on a
    # mutation endpoint (e.g. ``{"notee": "x"}``) used to return 201 with
    # the field silently dropped — the user sees "saved" but their input
    # never persisted. Forbid turns the typo into a 422 at the wire
    # boundary. The frontend's only documented caller sends exactly
    # ``Omit<Bookmark, 'id' | 'created_at'>`` (lib/api.ts:createBookmark),
    # so this is a tightening with no known caller breakage. See
    # ``test__create_bookmark__unknown_field__returns_422``.
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    message_uuid: str
    source: Literal["claude_desktop", "claude_code"]
    snippet: str = Field(default="", max_length=500)
    note: str = ""


class BookmarkUpdate(BaseModel):
    # Same rationale as ``BookmarkCreate`` — PATCH is the worst-case
    # silent-drop surface because the route returns 200 OK with the
    # bookmark unchanged when the caller PATCHes an unknown field. See
    # ``test__update_bookmark__unknown_field__returns_422``.
    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    snippet: str | None = None


class BookmarkList(BaseModel):
    bookmarks: list[Bookmark]


def _read_all() -> list[Bookmark]:
    path = _resolve_path()
    if not path.exists():
        return []
    try:
        data = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return []
    raw_list = data.get("bookmarks", []) if isinstance(data, dict) else []
    out: list[Bookmark] = []
    for item in raw_list:
        try:
            out.append(Bookmark(**item))
        except Exception:
            # Single malformed entry (e.g., user hand-edited the JSON
            # file or schema drift across versions) — skip and keep
            # the rest. WARNING level rather than ERROR because the
            # bookmarks file is user-managed: ERROR would trigger
            # operator alerting on what is really a user-side typo.
            logger.warning(
                "bookmarks: skipping malformed entry %r", item, exc_info=True
            )
            continue
    return out


def _write_all(bookmarks: list[Bookmark]) -> None:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"bookmarks": [b.model_dump() for b in bookmarks]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(
            orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE)
        )
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


@router.get(
    "",
    response_model=BookmarkList,
    summary="List all bookmarks (read-only; never gated by corrupt-config)",
)
async def list_bookmarks() -> BookmarkList:
    return BookmarkList(bookmarks=_read_all())


@router.post(
    "",
    response_model=Bookmark,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new bookmark on a message",
    # Layer 2 of PLANS/2026.05.18-config-corruption-safe-mode.md:
    # refuse writes when config.json is corrupt to avoid orphaning the
    # archive at the default data_dir. GET (list_bookmarks) is NOT
    # gated — the user needs read access while they fix the file.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def create_bookmark(payload: BookmarkCreate) -> Bookmark:
    bookmark = Bookmark(
        id=str(uuid.uuid4()),
        conversation_id=payload.conversation_id,
        message_uuid=payload.message_uuid,
        source=payload.source,
        created_at=datetime.now(UTC).isoformat(),
        note=payload.note,
        snippet=payload.snippet[:140],
    )
    bookmarks = _read_all()
    bookmarks.append(bookmark)
    _write_all(bookmarks)
    return bookmark


@router.patch(
    "/{bookmark_id}",
    response_model=Bookmark,
    summary="Update note or snippet on an existing bookmark",
    # See create_bookmark for Layer-2 gate rationale.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def update_bookmark(bookmark_id: str, payload: BookmarkUpdate) -> Bookmark:
    bookmarks = _read_all()
    for i, bm in enumerate(bookmarks):
        if bm.id == bookmark_id:
            updated = bm.model_copy(
                update={
                    k: v
                    for k, v in {"note": payload.note, "snippet": payload.snippet}.items()
                    if v is not None
                }
            )
            bookmarks[i] = updated
            _write_all(bookmarks)
            return updated
    raise HTTPException(status_code=404, detail="Bookmark not found")


@router.delete(
    "/{bookmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a bookmark by id",
    # See create_bookmark for Layer-2 gate rationale.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def delete_bookmark(bookmark_id: str) -> None:
    bookmarks = _read_all()
    next_list = [b for b in bookmarks if b.id != bookmark_id]
    if len(next_list) == len(bookmarks):
        raise HTTPException(status_code=404, detail="Bookmark not found")
    _write_all(next_list)
