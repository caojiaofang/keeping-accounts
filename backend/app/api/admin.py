"""Web 管理接口，独立前缀 /prod-api；业务数据只读。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import (
    ADMIN_COOKIE,
    audit_context,
    get_admin_auth,
    require_admin,
    verify_admin_origin,
)
from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.models.admin import AdminAccount
from app.schemas.admin import (
    AdminAccountResponse,
    AdminAuditResponse,
    AdminBook,
    AdminBookDetail,
    AdminIdentity,
    AdminLogin,
    AdminUser,
    AuditAction,
    AuditOutcome,
    Overview,
    Page,
    PasswordChange,
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
    token, account = await auth.login(
        body.username,
        body.password.get_secret_value(),
        audit_context(request),
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
    return AdminIdentity(username=account.username)


@router.get("/auth/me", response_model=AdminIdentity)
async def me(account: Annotated[AdminAccount, Depends(require_admin)]):
    return AdminIdentity(username=account.username)


@router.post(
    "/auth/logout",
    status_code=204,
    dependencies=[Depends(verify_admin_origin)],
)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[AdminAuth, Depends(get_admin_auth)],
    account: Annotated[AdminAccount, Depends(require_admin)],
):
    await auth.logout(account, request.cookies[ADMIN_COOKIE], audit_context(request))
    clear_admin_cookie(response, auth.settings)


def clear_admin_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        ADMIN_COOKIE,
        path="/prod-api",
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
    )


def get_admin_service(session: Annotated[AsyncSession, Depends(get_db)]) -> AdminService:
    return AdminService(session)


Service = Annotated[AdminService, Depends(get_admin_service)]
protected = APIRouter(dependencies=[Depends(require_admin)])
PageNumber = Annotated[int, Query(ge=1, le=1000000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=100)]


@protected.get("/account", response_model=AdminAccountResponse)
async def account_info(account: Annotated[AdminAccount, Depends(require_admin)]):
    return AdminAccountResponse.model_validate(account)


@protected.post("/account/password", status_code=204, dependencies=[Depends(verify_admin_origin)])
async def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    auth: Annotated[AdminAuth, Depends(get_admin_auth)],
    account: Annotated[AdminAccount, Depends(require_admin)],
):
    await auth.change_password(
        account,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
        audit_context(request),
    )
    clear_admin_cookie(response, auth.settings)


@protected.post("/account/logout-all", status_code=204, dependencies=[Depends(verify_admin_origin)])
async def logout_all(
    request: Request,
    response: Response,
    auth: Annotated[AdminAuth, Depends(get_admin_auth)],
    account: Annotated[AdminAccount, Depends(require_admin)],
):
    await auth.logout_all(account, audit_context(request))
    clear_admin_cookie(response, auth.settings)


@protected.get("/account/logs", response_model=Page[AdminAuditResponse])
async def audit_logs(
    auth: Annotated[AdminAuth, Depends(get_admin_auth)],
    action: AuditAction | None = None,
    outcome: AuditOutcome | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
):
    rows, total = await auth.repository.logs(action, outcome, page, page_size)
    return Page(
        items=[AdminAuditResponse.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


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
