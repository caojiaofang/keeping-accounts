"""SSE 真实 TCP 断连、心跳、并发锁及保存边界，不调用真实模型或 Redis。"""

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select, update
from test_analysis_v2_queries import data as data
from test_chat_v2_api import BASE, MemoryRedis
from test_chat_v2_context_store import context_data as context_data
from test_chat_v2_context_store import create
from test_chat_v2_loop import PLAN, Script, configured

from app.api.v1.assistant_v2 import get_chat_v2_service
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.main import create_app
from app.models.chat import ChatTurn
from app.models.shared import BookMember
from app.schemas.chat_v2 import AnswerPreviewV2, ContextPatch, SendMessageV2
from app.services import chat_v2 as module
from app.services.chat_v2 import ChatServiceV2


class BlockedModelStream(httpx.AsyncByteStream):
    def __init__(self):
        self.entered, self.closed = asyncio.Event(), asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Event().wait()
        yield b""

    async def aclose(self):
        self.closed.set()


def test_real_tcp_disconnect_cancels_model_releases_lock_and_does_not_save(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data, chat_v2_enabled=True)
        stream = BlockedModelStream()
        script = Script(settings, [httpx.Response(200, stream=stream)])
        redis = MemoryRedis()
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as model_client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, model_client),
                redis,
                settings,
                today=date(2026, 10, 27),
            )
            app = create_app(settings)
            app.dependency_overrides[get_chat_v2_service] = lambda: service
            app.router.lifespan_context = _empty_lifespan
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(3):
                    while not server.started:
                        await asyncio.sleep(0.01)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                    async with client.stream(
                        "POST",
                        f"{BASE}/conversations/{state.conversation_id}/messages/stream",
                        json={"id": str(uuid4()), "text": "查询"},
                    ) as response:
                        assert response.status_code == 200
                        async for line in response.aiter_lines():
                            if line == "event: status":
                                break
                        await asyncio.wait_for(stream.entered.wait(), 2)
                    await asyncio.wait_for(stream.closed.wait(), 2)
                    async with asyncio.timeout(2):
                        while redis.locks:
                            await asyncio.sleep(0.01)
                assert len(script.requests) == 1
                assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 5)
                listener.close()

    asyncio.run(exercise())


@asynccontextmanager
async def _empty_lifespan(_app):
    yield


def test_heartbeat_and_second_request_lock_conflict_while_model_is_waiting(
    context_data, monkeypatch
):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        stream = BlockedModelStream()
        script = Script(settings, [httpx.Response(200, stream=stream)])
        redis = MemoryRedis()
        monkeypatch.setattr(module, "STREAM_HEARTBEAT_SECONDS", 0.02)
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            service = ChatServiceV2(
                data.ledger, LlmClient(settings, client), redis, settings, today=date(2026, 10, 27)
            )
            events = service.events(state.conversation_id, SendMessageV2(id=uuid4(), text="查询"))
            try:
                while (await anext(events))["event"] != "ping":
                    pass
                await asyncio.wait_for(stream.entered.wait(), 2)
                with pytest.raises(ServiceError, match="正在回答中"):
                    await service.send(
                        state.conversation_id, SendMessageV2(id=uuid4(), text="同时查询")
                    )
                assert len(script.requests) == 1
            finally:
                await events.aclose()
            assert stream.closed.is_set() and not redis.locks

    asyncio.run(exercise())


def test_cancel_after_snapshot_keeps_unconfirmed_preview_without_saved_turn(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        async with httpx.AsyncClient() as client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, client),
                MemoryRedis(),
                settings,
                today=date(2026, 10, 27),
            )
            body = SendMessageV2(id=uuid4(), text="查询")
            draft = await service.store.revise(state.conversation_id, ContextPatch())

            async def prepared(*_args):
                return SimpleNamespace(
                    draft=draft,
                    preview=AnswerPreviewV2(answer="已核对的事实。", completeness="complete"),
                    deadline=asyncio.get_running_loop().time() + 5,
                )

            service.answer.run = prepared
            reached = asyncio.Event()

            async def emit(kind, _data):
                if kind == "snapshot":
                    reached.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(service.send(state.conversation_id, body, emit))
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not service.redis.locks
            assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0

    asyncio.run(exercise())


def test_save_failure_emits_error_instead_of_done_and_rolls_back(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        script = Script(settings, [PLAN, {"action": "clarify", "text": "你想查询哪段日期？"}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, client),
                MemoryRedis(),
                settings,
                today=date(2026, 10, 27),
            )

            async def fail():
                raise RuntimeError("private-save-exception")

            data.ledger.session.flush = fail
            result = [
                event
                async for event in service.events(
                    state.conversation_id, SendMessageV2(id=uuid4(), text="查询")
                )
            ]
            assert result[-1]["event"] == "error" and result[-1]["data"]["status"] == 500
            assert all(event["event"] != "done" for event in result)
            assert "private-save" not in str(result)
            assert (await service.store.load(state.conversation_id)).version == 0
            assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0
            assert not service.redis.locks

    asyncio.run(exercise())


def test_revoked_permission_during_delta_prevents_further_content_and_save(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        async with httpx.AsyncClient() as client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, client),
                MemoryRedis(),
                settings,
                today=date(2026, 10, 27),
            )
            draft = await service.store.revise(state.conversation_id, ContextPatch())

            async def prepared(*_args):
                return SimpleNamespace(
                    draft=draft,
                    preview=AnswerPreviewV2(
                        answer="已验证第一段。\n已验证第二段。", completeness="complete"
                    ),
                    deadline=asyncio.get_running_loop().time() + 5,
                )

            service.answer.run = prepared
            sent = []

            async def emit(kind, value):
                sent.append((kind, value))
                if kind == "delta":
                    data.session.execute(
                        update(BookMember)
                        .where(
                            BookMember.book_id == data.books["shared"],
                            BookMember.user_id == data.users["owner"],
                        )
                        .values(status="removed")
                    )
                    data.session.commit()

            with pytest.raises(ServiceError, match="不存在或无权访问"):
                await service.send(
                    state.conversation_id, SendMessageV2(id=uuid4(), text="查询"), emit
                )
            assert len([item for item in sent if item[0] == "delta"]) == 1
            assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0
            assert not service.redis.locks

    asyncio.run(exercise())


def test_committed_message_with_lost_done_is_recovered_without_another_model_call(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        script = Script(settings, [PLAN, {"action": "clarify", "text": "你想查哪段日期？"}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, client),
                MemoryRedis(),
                settings,
                today=date(2026, 10, 27),
            )
            body = SendMessageV2(id=uuid4(), text="查询")
            original_save = service.store.save_turn
            committed = asyncio.Event()

            async def save_then_lose_done(*args):
                await original_save(*args)
                committed.set()
                await asyncio.Event().wait()

            service.store.save_turn = save_then_lose_done
            stream = service.events(state.conversation_id, body)
            try:
                while True:
                    event = await anext(stream)
                    if event["event"] == "status" and "保存" in event["data"]["text"]:
                        break
                await asyncio.wait_for(committed.wait(), 2)
            finally:
                await stream.aclose()
            assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 1
            retry = [event async for event in service.events(state.conversation_id, body)]
            assert [event["event"] for event in retry] == ["status", "done"]
            assert retry[-1]["data"]["id"] == str(body.id) and len(script.requests) == 2
            assert not service.redis.locks

    asyncio.run(exercise())


def test_whole_service_deadline_cancels_http_and_emits_error_without_save(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data, chat_timeout_seconds=0.03)
        stream = BlockedModelStream()
        script = Script(settings, [httpx.Response(200, stream=stream)])
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            service = ChatServiceV2(
                data.ledger,
                LlmClient(settings, client),
                MemoryRedis(),
                settings,
                today=date(2026, 10, 27),
            )
            result = [
                event
                async for event in service.events(
                    state.conversation_id, SendMessageV2(id=uuid4(), text="查询")
                )
            ]
            assert result[-1]["event"] == "error" and result[-1]["data"]["status"] == 504
            assert not service.redis.locks and stream.closed.is_set()
            assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0

    asyncio.run(exercise())
