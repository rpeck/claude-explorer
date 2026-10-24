"""Conversations router."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..models import (
    ConversationDetail,
    ConversationListItem,
    ConversationTree,
)
from ..responses import OrjsonResponse
from ..store import ConversationStore

router = APIRouter(prefix="/conversations", tags=["conversations"])


# OrjsonResponse on the list endpoint cuts serialization of the ~1 MB
# sidebar payload from ~500 ms (Pydantic-via-stdlib-json) to ~30 ms.
# orjson handles datetimes natively, so the response models need no
# special shape handling.
#
# The list endpoint serializes the SKINNY `ConversationListItem` shape
# (backend/models.py) — `summary`, `human_message_count`, and `git_branch`
# are intentionally dropped from each row to shrink the wire payload.
# The router still asks the store for the FULL `ConversationSummary[]`
# because the server-side `?search=` filter inside
# `store.list_conversations` matches against `summary`. The skinny
# projection happens AFTER the filter / sort / subagent expansion, so
# search behavior is unchanged. See PLANS/SPLIT_CONVERSATION_SCHEMA.md.
@router.get(
    "",
    response_model=list[ConversationListItem],
    response_class=OrjsonResponse,
    summary="List conversations with optional filtering, sorting, and search",
)
async def list_conversations(
    search: str | None = Query(None, description="Search in name/summary"),
    starred: bool | None = Query(None, description="Filter by starred status"),
    model: str | None = Query(None, description="Filter by model"),
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = Query(
        "all", description="Filter by source (all, CLAUDE_AI, CLAUDE_CODE)"
    ),
    sort: Literal["updated_at", "created_at", "name", "project"] = Query(
        "updated_at", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        "desc", description="Sort order (asc or desc)"
    ),
    include_phantom: bool = Query(
        False, description="Include phantom sessions (local command artifacts)"
    ),
    include_subagents: bool = Query(
        False, description="Include subagent session details in response"
    ),
    organization_id: str | None = Query(
        None, description="Filter by organization (workspace) UUID"
    ),
    show_archived: bool = Query(
        False,
        description=(
            "Include archived sessions (currently only meaningful for "
            "CLAUDE_COWORK; Desktop + CC have no archived flag). When "
            "false (default), Cowork sessions with sidecar.isArchived "
            "true are hidden."
        ),
    ),
    store: ConversationStore = Depends(get_store),
) -> list[ConversationListItem]:
    """List all conversations with optional filtering."""
    full = store.list_conversations(
        search=search,
        starred=starred,
        model=model,
        source=source,
        sort=sort,
        sort_order=sort_order,
        include_phantom=include_phantom,
        include_subagents=include_subagents,
        organization_id=organization_id,
        show_archived=show_archived,
    )
    # Pydantic v2 from_attributes=True projects the fuller
    # ConversationSummary into the skinny ConversationListItem without
    # copying the dropped fields (summary, human_message_count,
    # git_branch). The cost is microseconds per row at ~1k rows —
    # well inside the post-Phase-1 ~80 ms warm budget.
    return [
        ConversationListItem.model_validate(s, from_attributes=True)
        for s in full
    ]


@router.get(
    "/{uuid}",
    response_model=ConversationDetail,
    response_class=OrjsonResponse,
    summary="Get a single conversation, optionally on a specific branch",
)
async def get_conversation(
    uuid: str,
    leaf: str | None = Query(None, description="Override active branch leaf UUID"),
    store: ConversationStore = Depends(get_store),
) -> OrjsonResponse:
    """Get a single conversation by UUID, optionally on a specific branch.

    W3+W4 (2026-05-23): the route bypasses FastAPI's default Pydantic
    encoder by returning ``OrjsonResponse(content=cached_dict)``
    directly. The dict cache in :func:`backend.store.get_conversation_dict`
    saves the ~186 ms Pydantic rebuild on warm hits, AND the
    OrjsonResponse layer saves the ~70 ms stdlib-json encode.

    ``response_model=ConversationDetail`` is kept ON THE DECORATOR
    purely for OpenAPI schema generation — FastAPI skips its serialization
    pipeline whenever the handler returns a Response object directly.
    """
    conversation = store.get_conversation_dict(uuid, leaf_override=leaf)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return OrjsonResponse(content=conversation)


@router.get(
    "/{uuid}/tree",
    response_model=ConversationTree,
    summary="Get the full message tree for a conversation (all branches)",
)
async def get_conversation_tree(
    uuid: str,
    store: ConversationStore = Depends(get_store),
) -> ConversationTree:
    """Get the full message tree for a conversation."""
    try:
        tree = store.get_conversation_tree(uuid)
        if not tree:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return tree
    except RecursionError:
        raise HTTPException(
            status_code=422,
            detail="Conversation has too many messages for tree visualization"
        )