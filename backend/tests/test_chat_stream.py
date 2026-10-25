"""流式正文、统计先行、解释降级、断连取消和首包权限校验。"""

import asyncio
import json
import socket
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.assistant import get_chat_service
from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.main import create_app
from app.schemas.chat import AnalysisCard, QueryPlan, SendMessage
from app.services import chat as chat_module
from app.services.chat import ChatService, explanation_prefix


def frame(data):
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\r\n\r\n").encode()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("failure", [None, "truncated", "refusal", "limited", "malformed"])
def test_upstream_stream_protocol_and_incomplete_output(mode, failure):
    async def run():
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                if mode == "responses":
                    yield frame({"type": "response.reasoning_text.delta", "delta": "私有推理"})
                    for delta in ['{"text":"', "餐饮。", '"}']:
                        yield frame({"type": "response.output_text.delta", "delta": delta})
                    if failure == "refusal":
                        yield frame({"type": "response.refusal.delta", "delta": "拒绝"})
                    elif failure is None:
                        yield frame(
                            {"type": "response.completed", "response": {"status": "completed"}}
                        )
                else:
                    for delta in ['{"text":"', "餐饮。", '"}']:
                        yield frame({"choices": [{"index": 0, "delta": {"content": delta}}]})
                    if failure == "refusal":
                        yield frame({"choices": [{"delta": {"refusal": "拒绝"}}]})
                    elif failure is None:
                        yield frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                if failure == "malformed":
                    yield b"data: {bad-json}\n\n"
                yield b"data: [DONE]\n\n"

        def handler(request):
            payload = json.loads(request.content)
            assert payload["stream"] is True
            assert request.extensions["timeout"]["read"] == 240
            return httpx.Response(429 if failure == "limited" else 200, stream=Chunks())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = LlmClient(
                Settings(_env_file=None, llm_api_mode=mode, llm_api_key=SecretStr("key")), client
            )
            chunks = []

            async def consume():
                async for delta in llm.structured_stream("说明", "问题", {}):
                    chunks.append(delta)

            if failure:
                with pytest.raises(ServiceError) as error:
                    await consume()
                assert error.value.status_code == (429 if failure == "limited" else 502)
            else:
                await consume()
                assert chunks == ['{"text":"', "餐饮。", '"}']

    asyncio.run(run())


def make_service(monkeypatch, explanation_failure=None):
    conversation = SimpleNamespace(
        version=0, book_id=uuid4(), month="2026-10", title="新对话", context=None
    )
    session = SimpleNamespace(
        rollback=AsyncMock(),
        scalars=AsyncMock(return_value=[]),
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=Mock(),
    )
    session.add.side_effect = lambda turn: setattr(turn, "created_at", datetime.now(UTC))
    ledger = SimpleNamespace(user_id=uuid4(), session=session)
    redis = SimpleNamespace(set=AsyncMock(return_value=True), eval=AsyncMock(return_value=1))
    waiting, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Model:
        calls = 0

        async def structured_stream(self, instructions, text, schema):
            self.calls += 1
            if "action" in schema["properties"]:
                plan = QueryPlan(
                    action="overview",
                    month=None,
                    category_mode="all",
                    category_id=None,
                    entry_type=None,
                    threshold=None,
                    comparison=None,
                    clarification=None,
                )
                yield plan.model_dump_json()
                return
            try:
                yield '{"text":"共 {{count}} 笔。'
                waiting.set()
                await release.wait()
                if explanation_failure == "timeout":
                    raise ServiceError(504, "模型超时")
                if explanation_failure == "invalid":
                    yield '错误金额999元。"}'
                else:
                    yield '可以先核对主要分类。"}'
            finally:
                closed.set()

    card = AnalysisCard(
        title="支出分布",
        text="账单统计。",
        total=2050,
        count=2,
        entry_type="expense",
        scope="2026-10",
        rows=[],
        footnote="已记录账单",
    )
    monkeypatch.setattr(
        chat_module, "AnalysisQueries", lambda _: SimpleNamespace(run=AsyncMock(return_value=card))
    )
    service = ChatService(ledger, Model(), redis, Settings(_env_file=None))
    service.require_conversation = AsyncMock(return_value=conversation)
    service.existing = AsyncMock(return_value=None)
    return service, waiting, release, closed


@pytest.mark.parametrize("failure", [None, "timeout", "invalid", "deadline"])
def test_card_and_sentences_arrive_before_completion_and_fallback_is_saved(monkeypatch, failure):
    async def run():
        service, waiting, release, _ = make_service(monkeypatch, failure)
        if failure == "deadline":
            service.settings.chat_timeout_seconds = 0.05
        events = []

        async def consume():
            async for event in service.events(uuid4(), SendMessage(id=uuid4(), text="支出分布")):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        await asyncio.sleep(0)
        assert not task.done()
        assert [e["event"] for e in events] == ["status", "status", "snapshot", "status", "delta"]
        assert events[2]["data"]["result"]["total"] == 2050
        assert events[4]["data"]["text"] == "\n\n共 2 笔。"
        service.ledger.session.commit.assert_not_awaited()
        if failure != "deadline":
            release.set()
        await asyncio.wait_for(task, timeout=2)
        assert events[-1]["event"] == "done"
        answer = events[-1]["data"]["answer"]
        if failure:
            assert "已保留账单统计结果" in answer
            assert "999" not in answer
            assert "999" not in json.dumps(events, ensure_ascii=False)
        else:
            assert answer == "账单统计。\n\n共 2 笔。可以先核对主要分类。"
        assert events[-1]["data"]["result"]["text"] == answer
        service.ledger.session.commit.assert_awaited_once()
        assert service.redis.set.call_args.kwargs["ex"] >= 60

    asyncio.run(run())


def test_disconnect_cancels_model_releases_lock_and_does_not_save(monkeypatch):
    async def run():
        service, waiting, _, closed = make_service(monkeypatch)
        stream = service.events(uuid4(), SendMessage(id=uuid4(), text="看看支出"))
        while True:
            event = await anext(stream)
            if event["event"] == "delta":
                break
        await waiting.wait()
        await stream.aclose()
        assert closed.is_set()
        service.ledger.session.commit.assert_not_awaited()
        assert service.redis.eval.call_args.args[0] == chat_module.UNLOCK

    asyncio.run(run())


def test_heartbeat_while_model_is_still_waiting(monkeypatch):
    async def run():
        service, _, _, _ = make_service(monkeypatch)
        monkeypatch.setattr(chat_module, "STREAM_HEARTBEAT_SECONDS", 0.01)
        stream = service.events(uuid4(), SendMessage(id=uuid4(), text="看看"))
        while (await anext(stream))["event"] != "delta":
            pass
        assert (await asyncio.wait_for(anext(stream), timeout=1))["event"] == "ping"
        await stream.aclose()

    asyncio.run(run())


def test_real_http_stream_first_content_and_disconnect_cleanup(monkeypatch):
    """通过本机真实 HTTP 验证首包不等待整轮完成，关闭连接会清理生成任务。"""

    async def run():
        service, _, _, closed = make_service(monkeypatch)
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_chat_service] = lambda: service
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                async with client.stream(
                    "POST",
                    f"/api/v1/assistant/conversations/{uuid4()}/messages/stream",
                    json={"id": str(uuid4()), "text": "看看"},
                ) as response:
                    assert response.headers["x-accel-buffering"] == "no"
                    async for line in response.aiter_lines():
                        if line == "event: delta":
                            break
                    service.ledger.session.commit.assert_not_awaited()
                await asyncio.wait_for(closed.wait(), timeout=2)
                # 模型关闭之后的 finally 还需要释放 Redis 锁。
                async with asyncio.timeout(2):
                    while service.redis.eval.call_args.args[0] != chat_module.UNLOCK:
                        await asyncio.sleep(0.01)
                service.ledger.session.commit.assert_not_awaited()
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
            listener.close()

    asyncio.run(run())


def test_completed_duplicate_only_replays_done(monkeypatch):
    async def run():
        service, _, _, _ = make_service(monkeypatch)
        service.existing.return_value = SimpleNamespace(model_dump=lambda **_: {"id": "saved"})
        events = [
            event async for event in service.events(uuid4(), SendMessage(id=uuid4(), text="看看"))
        ]
        assert [event["event"] for event in events] == ["status", "done"]
        assert service.llm.calls == 0
        service.redis.set.assert_not_awaited()

    asyncio.run(run())


def test_permission_revoked_midstream_stops_new_content(monkeypatch):
    async def run():
        service, _, release, _ = make_service(monkeypatch)
        stream = service.events(uuid4(), SendMessage(id=uuid4(), text="看看"))
        while (await anext(stream))["event"] != "delta":
            pass
        service.require_conversation.side_effect = ServiceError(404, "无权访问")
        release.set()
        remaining = [event async for event in stream]
        assert remaining[-1] == {"event": "error", "data": {"status": 404, "detail": "无权访问"}}
        assert not any(event["event"] == "delta" for event in remaining)
        service.ledger.session.commit.assert_not_awaited()

    asyncio.run(run())


def test_partial_json_never_emits_incomplete_sentence_or_placeholder():
    assert explanation_prefix('{"text":"共 {{cou') == ""
    assert explanation_prefix('{"text":"共 {{count}} 笔。建议') == "共 {{count}} 笔。"
    assert explanation_prefix('{"text":"核对\\n再看\\u') == "核对\n"


def test_stream_http_headers_and_access_check():
    service = SimpleNamespace(
        require_conversation=AsyncMock(),
        ledger=SimpleNamespace(session=SimpleNamespace(rollback=AsyncMock())),
    )

    async def events(*_):
        yield {"event": "status", "data": {"text": "正在分析…"}}
        yield {"event": "done", "data": {"answer": "完成"}}

    service.events = events
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_chat_service] = lambda: service
    with TestClient(app) as client:
        path = f"/api/v1/assistant/conversations/{uuid4()}/messages/stream"
        body = {"id": str(uuid4()), "text": "看看"}
        response = client.post(path, json=body)
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-store"
        assert "event: status\n" in response.text and "event: done\n" in response.text
        service.require_conversation.side_effect = ServiceError(404, "无权访问")
        response = client.post(path, json=body)
        assert response.status_code == 404
        assert response.json() == {"detail": "无权访问"}
