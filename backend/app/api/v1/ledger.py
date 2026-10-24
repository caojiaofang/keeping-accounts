from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import get_access_token, get_auth_service
from app.api.deps import get_db
from app.core.errors import ServiceError
from app.schemas.ledger import (
    BookResponse,
    CreateBook,
    CreateEntry,
    EntryResponse,
    LedgerResponse,
    UpdateBudget,
    UpdateEntry,
)
from app.services.auth import AuthService
from app.services.ledger import LedgerService

router = APIRouter(tags=["个人记账"])


async def get_ledger_service(
    response: Response,
    token: Annotated[str, Depends(get_access_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[LedgerService]:
    response.headers["Cache-Control"] = "no-store"
    user = await auth.current_user(token)
    try:
        yield LedgerService(session, user.id)
    except SQLAlchemyError:
        await session.rollback()
        raise ServiceError(503, "账本服务暂时不可用，请稍后重试") from None


Ledger = Annotated[LedgerService, Depends(get_ledger_service)]


@router.get(
    "/ledger",
    response_model=LedgerResponse,
    response_model_exclude_none=True,
    summary="读取个人账本及全部有效账单，首次访问创建空的日常账本",
)
async def snapshot(service: Ledger):
    return await service.snapshot()


@router.post("/books", response_model=BookResponse, summary="创建个人账本，同一 ID 防重复")
async def create_book(body: CreateBook, service: Ledger):
    return await service.create_book(body)


@router.put("/books/{book_id}/budget", response_model=BookResponse, summary="设置账本月预算")
async def update_budget(book_id: UUID, body: UpdateBudget, service: Ledger):
    return await service.update_budget(book_id, body)


@router.post(
    "/entries",
    response_model=EntryResponse,
    response_model_exclude_none=True,
    summary="创建账单，同一 ID 防重复",
)
async def create_entry(body: CreateEntry, service: Ledger):
    return await service.create_entry(body)


@router.put(
    "/entries/{entry_id}",
    response_model=EntryResponse,
    response_model_exclude_none=True,
    summary="编辑账单，使用版本号防止覆盖其他设备的更新",
)
async def update_entry(entry_id: UUID, body: UpdateEntry, service: Ledger):
    return await service.update_entry(entry_id, body)


@router.delete("/entries/{entry_id}", status_code=204, summary="删除账单，可安全重试")
async def delete_entry(entry_id: UUID, service: Ledger, version: Annotated[int, Query(ge=1)]):
    await service.delete_entry(entry_id, version)
