from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["运行检查"])


@router.get("/health", response_model=HealthResponse, summary="检查应用是否运行")
async def health() -> HealthResponse:
    """仅检查应用存活，不检测 PostgreSQL、Redis 或其他外部服务。"""
    return HealthResponse(status="ok")
