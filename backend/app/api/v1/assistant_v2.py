"""独立 V2 路由；能力查询需登录，业务默认关闭，V1 不自动重发。"""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.api.deps import get_redis, get_settings
from app.api.v1.ledger import Ledger
from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.chat_v2 import (
    CapabilitiesV2,
    ConversationResponseV2,
    ConversationSummaryV2,
    CreateConversationV2,
    LegacyConversationResponseV2,
    SendMessageV2,
)
from app.services.chat_v2 import ChatServiceV2


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/assistant/v2", tags=["聊聊花费 V2"], dependencies=[Depends(no_store)])


def require_v2_enabled(settings: Annotated[Settings, Depends(get_settings)]):
    if not settings.chat_v2_enabled:
        raise ServiceError(404, "新版聊聊花费尚未启用")


def get_chat_v2_service(
    enabled: Annotated[None, Depends(require_v2_enabled)],
    request: Request,
    ledger: Ledger,
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatServiceV2:
    return ChatServiceV2(ledger, request.app.state.llm, redis, settings)


ChatV2 = Annotated[ChatServiceV2, Depends(get_chat_v2_service)]


@router.get("/capabilities", response_model=CapabilitiesV2, summary="已认证用户查询 V2 是否启用")
async def capabilities(ledger: Ledger, settings: Annotated[Settings, Depends(get_settings)]):
    await ledger.session.rollback()
    return CapabilitiesV2(enabled=settings.chat_v2_enabled)


@router.post("/conversations", response_model=ConversationSummaryV2, summary="创建 V2 分析会话")
async def create_conversation(body: CreateConversationV2, service: ChatV2):
    return await service.create(body)


@router.get(
    "/conversations",
    response_model=list[ConversationSummaryV2],
    summary="当前账本最近二十个 V2 会话",
)
async def list_conversations(service: ChatV2, book_id: Annotated[UUID, Query(alias="bookId")]):
    return await service.store.list(book_id)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponseV2,
    summary="读取 V2 历史和当前成功上下文，按序号向前分页",
)
async def read_conversation(
    conversation_id: UUID, service: ChatV2, before: Annotated[int | None, Query(ge=1)] = None
):
    return await service.store.read(conversation_id, before)


@router.get(
    "/legacy/conversations/{conversation_id}",
    response_model=LegacyConversationResponseV2,
    summary="只读适配当前账本的旧会话，不改写原记录",
)
async def read_legacy(
    conversation_id: UUID, service: ChatV2, before: Annotated[int | None, Query(ge=1)] = None
):
    return await service.store.read(conversation_id, before, legacy=True)


@router.post(
    "/legacy/conversations/{conversation_id}/fork",
    response_model=ConversationSummaryV2,
    summary="基于旧会话的筛选开始同账本 V2 新会话",
)
async def fork_legacy(conversation_id: UUID, body: CreateConversationV2, service: ChatV2):
    return await service.fork_legacy(conversation_id, body)


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="V2 可信分析流，持久化后才发送 done",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE 事件流，done 表示轮次已保存",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_message(conversation_id: UUID, body: SendMessageV2, service: ChatV2):
    await service.require_conversation(conversation_id)

    async def encode():
        async for event in service.events(conversation_id, body):
            value = json.dumps(event["data"], ensure_ascii=True, separators=(",", ":"))
            yield f"event: {event['event']}\ndata: {value}\n\n"

    return StreamingResponse(
        encode(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
