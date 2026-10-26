"""Web 管理接口，独立前缀 /prod-api；业务数据只读。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import (
    ADMIN_COOKIE,
    get_admin_auth,
    require_admin,
    verify_admin_origin,
)
from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.schemas.admin import (
    AdminBook,
    AdminBookDetail,
    AdminIdentity,
    AdminLogin,
    AdminUser,
    Overview,
    Page,
)
from app.services.admin import AdminService
from app.services.admin_auth import AdminAuth

router = APIRouter(prefix="/prod-api", tags=["Web 管理端"])


@router.post(
    "/auth/login", response_model=AdminIdentity, dependencies=[Depends(verify_admin_origin)]
)
async def login(
    body: AdminLogin,
    request: Request,
    response: Response,
    auth: Annotated[AdminAuth, Depends(get_admin_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    token = await auth.login(
        body.username,
        body.password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=settings.admin_session_ttl_seconds,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/prod-api",
    )
    return AdminIdentity(username=auth.username)


@router.get("/auth/me", response_model=AdminIdentity)
async def me(username: Annotated[str, Depends(require_admin)]):
    return AdminIdentity(username=username)


@router.post(
    "/auth/logout",
    status_code=204,
    dependencies=[Depends(verify_admin_origin), Depends(require_admin)],
)
async def logout(
    request: Request, response: Response, auth: Annotated[AdminAuth, Depends(get_admin_auth)]
):
    await auth.revoke(request.cookies[ADMIN_COOKIE])
    response.delete_cookie(
        ADMIN_COOKIE,
        path="/prod-api",
        httponly=True,
        samesite="strict",
        secure=auth.settings.app_env == "production",
    )


def get_admin_service(session: Annotated[AsyncSession, Depends(get_db)]) -> AdminService:
    return AdminService(session)


Service = Annotated[AdminService, Depends(get_admin_service)]
protected = APIRouter(dependencies=[Depends(require_admin)])
PageNumber = Annotated[int, Query(ge=1, le=1000000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=100)]


@protected.get("/overview", response_model=Overview)
async def overview(service: Service):
    return await service.overview()


@protected.get("/users", response_model=Page[AdminUser])
async def users(service: Service, q: Search = "", page: PageNumber = 1, page_size: PageSize = 20):
    return await service.users(q.strip(), page, page_size)


@protected.get("/users/{user_id}", response_model=AdminUser)
async def user(user_id: UUID, service: Service):
    return await service.user(user_id)


@protected.get("/books", response_model=Page[AdminBook])
async def books(
    service: Service,
    q: Search = "",
    kind: Literal["personal", "shared"] | None = None,
    status: Literal["active", "closed"] | None = None,
    owner_id: UUID | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
):
    return await service.books(q.strip(), kind, status, owner_id, page, page_size)


@protected.get("/books/{book_id}", response_model=AdminBookDetail)
async def book(book_id: UUID, service: Service):
    return await service.book(book_id)


router.include_router(protected)
