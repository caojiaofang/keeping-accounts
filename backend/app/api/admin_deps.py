"""管理端 Cookie 鉴权和请求来源校验，不接受小程序 Bearer 凭证。"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, get_settings
from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.admin import AdminAccount
from app.repositories.admin import AdminRepository, AuditContext
from app.services.admin_auth import AdminAuth
from app.services.sessions import unauthorized

ADMIN_COOKIE = "moji_admin_session"


def get_admin_auth(
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AdminAuth:
    return AdminAuth(redis, settings, AdminRepository(session))


def audit_context(request: Request) -> AuditContext:
    return AuditContext(
        request.client.host if request.client else "unknown", request.headers.get("user-agent", "")
    )


def verify_admin_origin(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin != settings.admin_origin
    ):
        raise ServiceError(403, "请求来源不受信任，请从管理端页面重新操作")


async def require_admin(
    request: Request, auth: Annotated[AdminAuth, Depends(get_admin_auth)]
) -> AdminAccount:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        raise unauthorized()
    return await auth.authenticate(token)
