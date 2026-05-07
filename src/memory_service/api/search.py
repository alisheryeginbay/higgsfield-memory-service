"""POST /search — explicit structured memory search.

Stub for now; real ranking lands alongside the recall pipeline.
"""

from fastapi import APIRouter, Depends, status

from memory_service.deps import require_auth
from memory_service.schemas import SearchIn, SearchOut

router = APIRouter(tags=["search"])


@router.post(
    "/search",
    response_model=SearchOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_auth)],
)
async def post_search(payload: SearchIn) -> SearchOut:  # noqa: ARG001
    return SearchOut(results=[])
