import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, get_settings
from app.api.v1.ledger import Ledger
from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.ledger import BookResponse, MemberResponse
from app.schemas.shared import (
    AcceptInvitation,
    ActivityResponse,
    BookVersion,
    CreateInvitation,
    InvitationPreview,
    InvitationResponse,
    InvitationToken,
    TransferBook,
)
from app.services.sessions import LOGIN_RATE_SCRIPT
from app.services.shared import SharedBookService

router = APIRouter(tags=["多人账本"])


def shared_service(ledger: Ledger, settings: Annotated[Settings, Depends(get_settings)]):
    return SharedBookService(ledger.session, settings, ledger.user_id)


Shared = Annotated[SharedBookService, Depends(shared_service)]


@router.get("/books/{book_id}", response_model=BookResponse, summary="读取有权限的个人或共同账本")
async def read_book(book_id: UUID, ledger: Ledger):
    return await ledger.describe(await ledger.require_book(book_id))


@router.get(
    "/books/{book_id}/members", response_model=list[MemberResponse], summary="读取当前及历史成员"
)
async def members(book_id: UUID, ledger: Ledger):
    return (await ledger.describe(await ledger.require_book(book_id))).members


@router.post(
    "/books/{book_id}/invitations",
    response_model=InvitationResponse,
    summary="生成或恢复七天有效邀请",
)
async def create_invitation(book_id: UUID, body: CreateInvitation, service: Shared):
    return await service.create_invitation(book_id, body)


@router.delete("/books/{book_id}/invitations/{invitation_id}", status_code=204, summary="撤销邀请")
async def revoke_invitation(book_id: UUID, invitation_id: UUID, service: Shared):
    await service.revoke_invitation(book_id, invitation_id)


@router.post(
    "/book-invitations/preview",
    response_model=InvitationPreview,
    summary="未登录也可查看有限邀请信息",
)
async def preview(
    body: InvitationToken,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    response.headers["Cache-Control"] = "no-store"
    digest = hashlib.sha256(
        (request.client.host if request.client else "unknown").encode()
    ).hexdigest()
    try:
        count = await redis.eval(
            LOGIN_RATE_SCRIPT, 1, f"{settings.redis_key_prefix}invite-preview:{digest}"
        )
    except RedisError:
        raise ServiceError(503, "邀请服务暂时不可用，请稍后重试") from None
    if count > 60:
        raise ServiceError(429, "查看邀请过于频繁，请一分钟后重试")
    return await SharedBookService(session, settings).preview(body.token.get_secret_value())


@router.post(
    "/book-invitations/accept", response_model=BookResponse, summary="登录并明确接受邀请后加入"
)
async def accept(body: AcceptInvitation, service: Shared):
    return await service.accept(body.token.get_secret_value(), body.name)


@router.delete(
    "/books/{book_id}/members/{user_id}", response_model=BookResponse, summary="创建者移出普通成员"
)
async def remove_member(
    book_id: UUID, user_id: UUID, service: Shared, version: Annotated[int, Query(ge=1)]
):
    return await service.remove(book_id, user_id, version)


@router.post("/books/{book_id}/leave", status_code=204, summary="普通成员退出，保留历史账单")
async def leave(book_id: UUID, body: BookVersion, service: Shared):
    await service.leave(book_id, body.version)


@router.post(
    "/books/{book_id}/transfer", response_model=BookResponse, summary="创建者将账本转让给有效成员"
)
async def transfer(book_id: UUID, body: TransferBook, service: Shared):
    return await service.transfer(book_id, body.user_id, body.version)


@router.post(
    "/books/{book_id}/close", status_code=204, summary="创建者解散账本，不物理删除历史记录"
)
async def close(book_id: UUID, body: BookVersion, service: Shared):
    await service.close(book_id, body.version)


@router.get(
    "/books/{book_id}/activities",
    response_model=list[ActivityResponse],
    summary="按游标读取最近操作与账单变更",
)
async def activities(book_id: UUID, service: Shared, before: UUID | None = None):
    return await service.activities(book_id, before)
