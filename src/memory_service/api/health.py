from fastapi import APIRouter

from memory_service.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut()
