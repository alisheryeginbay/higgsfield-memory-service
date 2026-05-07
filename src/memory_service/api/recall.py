"""POST /recall — return formatted context for the next agent turn.

Stub: returns an empty context. Ranking + extraction-aware retrieval
land in a later milestone; this exists to satisfy the cold-session
contract ("never error on cold sessions") and to keep the route
reachable by the eval harness.
"""

from fastapi import APIRouter, Depends, status

from memory_service.deps import require_auth
from memory_service.schemas import RecallIn, RecallOut

router = APIRouter(tags=["recall"])


@router.post(
    "/recall",
    response_model=RecallOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_auth)],
)
async def post_recall(payload: RecallIn) -> RecallOut:  # noqa: ARG001
    return RecallOut(context="", citations=[])
