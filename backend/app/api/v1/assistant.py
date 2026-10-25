import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.api.deps import get_redis, get_settings
from app.api.v1.ledger import Ledger
from app.core.config import Settings
from app.schemas.assistant import ParseRequest, ParseResponse
from app.schemas.chat import (
    ConversationResponse,
    ConversationSummary,
    CreateConversation,
    SendMessage,
    TurnResponse,
)
from app.services.assistant import AssistantService
from app.services.chat import ChatService

router = APIRouter(prefix="/assistant", tags=["墨记助手"])


@router.post(
    "/parse",
    response_model=ParseResponse,
    response_model_exclude_none=True,
    summary="一句话识别记账草稿，确认前不写入账单",
)
async def parse(
    body: ParseRequest,
    request: Request,
    ledger: Ledger,
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    return await AssistantService(ledger, request.app.state.llm, redis, settings).parse(body)


def get_chat_service(
    request: Request,
    ledger: Ledger,
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatService:
    return ChatService(ledger, request.app.state.llm, redis, settings)


Chat = Annotated[ChatService, Depends(get_chat_service)]


@router.post(
    "/conversations", response_model=ConversationSummary, summary="创建按账本隔离的分析会话"
)
async def create_conversation(body: CreateConversation, service: Chat):
    return await service.create(body)


@router.get(
    "/conversations", response_model=list[ConversationSummary], summary="最近二十个已有问答的会话"
)
async def list_conversations(
    service: Chat,
    book_id: Annotated[UUID, Query(alias="bookId")],
    month: Annotated[str, Query(pattern=r"^(20\d{2}|2100)-(0[1-9]|1[0-2])$")],
):
    return await service.list(book_id, month)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    summary="读取会话，按序号向前分页",
)
async def read_conversation(
    conversation_id: UUID, service: Chat, before: Annotated[int | None, Query(ge=1)] = None
):
    return await service.read(conversation_id, before)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=TurnResponse,
    response_model_exclude_none=True,
    summary="分析云端账单并保存完整问答，相同消息ID可安全重试",
)
async def send_message(conversation_id: UUID, body: SendMessage, service: Chat):
    return await service.send(conversation_id, body)


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="流式分析账单，逐步返回统计和解释，完成后保存整轮问答",
)
async def stream_message(conversation_id: UUID, body: SendMessage, service: Chat):
    # 首包之前校验访问权限；之后的失败通过流内 error 事件返回。
    await service.require_conversation(conversation_id)
    await service.ledger.session.rollback()

    async def encode():
        async for event in service.events(conversation_id, body):
            data = json.dumps(event["data"], ensure_ascii=True, separators=(",", ":"))
            yield f"event: {event['event']}\ndata: {data}\n\n"

    return StreamingResponse(
        encode(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
