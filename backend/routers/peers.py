"""The Messages tab (issue #91): the sessions and the messages between them."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..models import PeerGraph
from ..store import ConversationStore

router = APIRouter(tags=["peers"])


@router.get(
    "/conversations/{uuid}/peers",
    response_model=PeerGraph,
    summary="Sessions and messages around one session (Messages tab)",
)
def conversation_peers(
    uuid: str,
    hops: str = Query("2", pattern=r"^([1-5]|all)$", description="1 to 5, or all (the connected group)"),
    subagents: bool = Query(False, description="Show agent transcripts as their own sessions"),
    store: ConversationStore = Depends(get_store),
) -> PeerGraph:
    """The sessions at most ``hops`` messages from this one, both directions."""
    graph = store.peer_graph(uuid, None if hops == "all" else int(hops), subagents)
    if graph is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return graph


@router.get(
    "/peers/active",
    response_model=PeerGraph,
    summary="Active sessions and the messages between them (Messages tab)",
)
def active_peers(
    subagents: bool = Query(False, description="Show agent transcripts as their own sessions"),
    store: ConversationStore = Depends(get_store),
) -> PeerGraph:
    """Every running or open session, and the messages between them."""
    graph = store.peer_graph(None, None, subagents)
    assert graph is not None  # only an unknown center gives None
    return graph
