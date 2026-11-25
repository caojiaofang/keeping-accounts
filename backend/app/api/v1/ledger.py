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
    CategoryResponse,
    CreateBook,
    CreateCategory,
    CreateEntry,
    EntryResponse,
    LedgerResponse,
    UpdateBudget,
    UpdateCategory,
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
    except ServiceError:
        await session.rollback()
        raise


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


@router.post(
    "/books/{book_id}/categories",
    response_model=CategoryResponse,
    summary="新增账本专属分类，同一 ID 防重复",
)
async def create_category(book_id: UUID, body: CreateCategory, service: Ledger):
    from app.services.categories import CategoryService

    return await CategoryService(service).create(book_id, body)


@router.put(
    "/books/{book_id}/categories/{category_id}",
    response_model=CategoryResponse,
    summary="修改账本专属分类名称",
)
async def update_category(book_id: UUID, category_id: str, body: UpdateCategory, service: Ledger):
    from app.services.categories import CategoryService

    return await CategoryService(service).change(book_id, category_id, body.version, name=body.name)


@router.delete(
    "/books/{book_id}/categories/{category_id}",
    response_model=CategoryResponse,
    summary="删除专属分类，保留历史账单",
)
async def delete_category(
    book_id: UUID, category_id: str, service: Ledger, version: Annotated[int, Query(ge=1)]
):
    from app.services.categories import CategoryService

    return await CategoryService(service).change(book_id, category_id, version)
