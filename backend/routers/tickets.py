"""Issue #93: the ticket page data.

``GET /api/tickets/{owner}/{repo}/{number}`` for a GitHub issue ("_/_" is the
unknown repo), and ``GET /api/tickets/key/{key}`` for a Linear or Jira key.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_store
from ..store import ConversationStore
from ..tickets import TicketDetail, parse_ticket_key

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _ticket(store: ConversationStore, scope: str | None, number: int) -> TicketDetail:
    detail = store.get_ticket(scope, number)
    if detail is None:
        raise HTTPException(status_code=503, detail="ticket index not ready")
    return detail


@router.get(
    "/key/{key}",
    response_model=TicketDetail,
    summary="Get every session that refers to a Linear or Jira key",
)
async def get_ticket_by_key(key: str, store: ConversationStore = Depends(get_store)) -> TicketDetail:
    """``key`` is "ENG-123": an upper-case prefix, "-", and a number of 1 or more."""
    parsed = parse_ticket_key(key)
    if parsed is None or parsed[0] is None or "/" in parsed[0] or "#" in key or "://" in key:
        raise HTTPException(status_code=400, detail="key must be <PREFIX>-<N>, for example ENG-123")
    return _ticket(store, *parsed)


@router.get(
    "/{owner}/{repo}/{number}",
    response_model=TicketDetail,
    summary="Get every session that refers to a GitHub issue",
)
async def get_ticket(
    owner: str, repo: str, number: str, store: ConversationStore = Depends(get_store)
) -> TicketDetail:
    """Every session that refers to the ticket. Owner and repo "_" mean the unknown repo."""
    if not (number.isascii() and number.isdigit()) or int(number) < 1:
        raise HTTPException(status_code=400, detail="number must be a positive integer")
    full = None if (owner, repo) == ("_", "_") else f"{owner}/{repo}".lower()
    return _ticket(store, full, int(number))
