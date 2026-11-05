"""V2 消息生命周期：鉴权范围、幂等、共用限流/会话锁、SSE 与原子保存。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import anyio
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ServiceError
from app.integrations.chat_v2_llm import ChatModelV2
from app.schemas.chat_v2 import SendMessageV2
from app.services.chat import ChatService, business_today
from app.services.chat_v2_answer import AnswerServiceV2
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.chat_v2_loop import ChatLoopV2
from app.services.sessions import LOGIN_RATE_SCRIPT

logger = logging.getLogger(__name__)
STREAM_HEARTBEAT_SECONDS = 10


class ChatServiceV2:
    def __init__(self, ledger, llm, redis, settings, *, today=None):
        self.ledger, self.settings, self.redis = ledger, settings, redis
        self.store = ContextStoreV2(ledger, settings, today=today or business_today())
        self.guard = ChatService(ledger, llm, redis, settings)
        self.answer = AnswerServiceV2(ChatLoopV2(self.store, ChatModelV2(llm)))

    async def require_conversation(self, conversation_id):
        return await self.store.load(conversation_id)

    async def create(self, body):
        await self.store.create(body)
        return await self.store.summary(body.id)

    async def fork_legacy(self, conversation_id, body):
        await self.store.fork_legacy(conversation_id, body)
        return await self.store.summary(body.id)

    async def _rate_limit(self):
        key = f"{self.settings.redis_key_prefix}assistant:chat-rate:{self.ledger.user_id}"
        try:
            count = await self.redis.eval(LOGIN_RATE_SCRIPT, 1, key)
        except RedisError:
            raise ServiceError(503, "聊天服务暂时不可用，请稍后重试") from None
        if count > 10:
            raise ServiceError(429, "提问次数过多，请一分钟后重试", {"Retry-After": "60"})

    async def send(
        self,
        conversation_id,
        body: SendMessageV2,
        emit: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        body = SendMessageV2.model_validate(body.model_dump())
        deadline = asyncio.get_running_loop().time() + self.settings.chat_timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                cached = await self.store.existing(conversation_id, body)
                if cached:
                    return cached
                # 与 V1 共用同一个锁和账号限流键，换协议不能绕过限额。
                async with self.guard.lock(conversation_id):
                    cached = await self.store.existing(conversation_id, body)
                    if cached:
                        return cached
                    state = await self.store.load(conversation_id)
                    if state.version >= 100:
                        raise ServiceError(409, "此会话已有一百轮问答，请开始新对话")
                    await self._rate_limit()
                    if emit:
                        await emit("status", {"text": "正在查询并核对账单…"})
                    ready = await self.answer.run(conversation_id, body)
                    if ready.preview.completeness == "partial":
                        ready.preview.suggestions = []
                    if emit:
                        await self.answer.loop._current(
                            conversation_id, ready.draft.expected_version
                        )
                        preview = ready.preview.model_dump(mode="json", by_alias=True)
                        # 快照是完整替换；正文仅发送已经验证完毕的完整段落。
                        await emit("snapshot", {**preview, "answer": ""})
                        paragraphs = ready.preview.answer.splitlines(keepends=True)
                        for paragraph in paragraphs:
                            await self.answer.loop._current(
                                conversation_id, ready.draft.expected_version
                            )
                            await emit("delta", {"text": paragraph})
                        await self.answer.loop._current(
                            conversation_id, ready.draft.expected_version
                        )
                        await emit("snapshot", preview)
                        await emit("status", {"text": "正在保存本轮回答…"})
                    async with asyncio.timeout_at(min(deadline, ready.deadline)):
                        return await self.store.save_turn(ready.draft, body, ready.preview)
        except TimeoutError:
            raise ServiceError(504, "本轮分析超时，请缩小范围后重试") from None
        except SQLAlchemyError:
            raise ServiceError(503, "对话保存或读取暂时不可用，请稍后重试") from None

    async def events(self, conversation_id, body):
        queue = asyncio.Queue(maxsize=16)

        async def emit(kind, data):
            await queue.put((kind, data))

        async def produce():
            try:
                result = await self.send(conversation_id, body, emit)
                await emit("done", result.model_dump(mode="json", by_alias=True))
            except ServiceError as error:
                await emit("error", {"status": error.status_code, "detail": error.detail})
            except Exception as error:
                # 不输出 SQL 参数、模型返回原文或账单备注。
                logger.warning("V2 流式分析失败 kind=%s", type(error).__name__)
                await emit("error", {"status": 500, "detail": "分析暂时不可用，请稍后重试"})

        task = asyncio.create_task(produce())
        try:
            yield {"event": "status", "data": {"text": "正在理解你的问题…"}}
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), STREAM_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"event": "ping", "data": {}}
                    continue
                yield {"event": kind, "data": data}
                if kind in {"done", "error"}:
                    break
        finally:
            # 等待模型连接、事务及锁清理结束，依赖会话才可以退出。
            with anyio.CancelScope(shield=True):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
