"""Process health endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """Report only whether the bridge process can serve requests."""
    return {"status": "ok"}
