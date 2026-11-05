"""验证模型 HTTP 生命周期、超时、取消、协议异常与输出预算，不发送真实请求。"""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from test_analysis_v2_queries import data as data
from test_chat_v2_context_store import context_data as context_data
from test_chat_v2_context_store import create
from test_chat_v2_loop import (
    ANSWER,
    FILTERS,
    PLAN,
    Script,
    call_body,
    configured,
    execute,
    tool,
)

from app.core.errors import ServiceError
from app.integrations.chat_v2_llm import ChatModelV2
from app.integrations.llm import LlmClient
from app.models.chat import ChatConversation, ChatTurn
from app.schemas.chat_v2 import SendMessageV2
from app.services.chat_v2_loop import ChatLoopV2


class ControlledStream(httpx.AsyncByteStream):
    def __init__(self, *, block=False, disconnect=False, oversized=False):
        self.block, self.disconnect, self.oversized = block, disconnect, oversized
        self.entered, self.closed = asyncio.Event(), False

    async def __aiter__(self):
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        if self.oversized:
            yield b"x" * 8193
        else:
            yield b'{"private-response":'
        if self.disconnect:
            raise httpx.ReadError("private-upstream-disconnect")

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("phase", ["plan", "after_tool"])
def test_external_cancel_closes_http_and_does_not_save_or_continue(context_data, phase):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        data.store.settings = settings
        stream = ControlledStream(block=True)
        steps = (
            [httpx.Response(200, stream=stream)]
            if phase == "plan"
            else [
                PLAN,
                tool(),
                httpx.Response(200, stream=stream),
            ]
        )
        script = Script(settings, steps)
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
            task = asyncio.create_task(
                loop.run(state.conversation_id, SendMessageV2(id=uuid4(), text="查询"))
            )
            await asyncio.wait_for(stream.entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stream.closed and len(script.requests) == (1 if phase == "plan" else 3)
        assert (await data.store.load(state.conversation_id)).version == 0
        assert data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0

    asyncio.run(exercise())


def test_shared_deadline_includes_plan_and_cleans_active_http(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        # 缩短仅本测试的时钟预算，真实配置仍强制至少十秒。
        settings = configured(data, chat_timeout_seconds=0.03)
        data.store.settings = settings
        stream = ControlledStream(block=True)
        script = Script(settings, [httpx.Response(200, stream=stream)])
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))
            with pytest.raises(ServiceError, match="本轮分析超时") as error:
                await loop.run(state.conversation_id, SendMessageV2(id=uuid4(), text="查询"))
        assert error.value.status_code == 504 and stream.closed and len(script.requests) == 1
        assert (await data.store.load(state.conversation_id)).version == 0

    asyncio.run(exercise())


def test_cancel_during_tool_query_propagates_without_next_model_request(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        settings = configured(data)
        data.store.settings = settings
        script = Script(settings, [PLAN, tool()])
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(data.store, ChatModelV2(LlmClient(settings, client)))

            async def slow_tool(*args):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            loop.tools.execute = slow_tool
            task = asyncio.create_task(
                loop.run(state.conversation_id, SendMessageV2(id=uuid4(), text="查询"))
            )
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cleaned.is_set() and len(script.requests) == 2
        assert (await data.store.load(state.conversation_id)).version == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["disconnect", "oversized"])
def test_bounded_http_stream_discards_incomplete_response_and_closes(context_data, kind):
    async def exercise():
        stream = ControlledStream(disconnect=kind == "disconnect", oversized=kind == "oversized")
        with pytest.raises(ServiceError) as error:
            await execute(
                context_data, [httpx.Response(200, stream=stream)], chat_v2_max_response_bytes=8192
            )
        assert error.value.status_code == 502 and stream.closed
        assert "private" not in error.value.detail and "没有记录" not in error.value.detail

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["result", "total", "input"])
def test_size_budgets_stop_before_sending_oversized_data(context_data, kind):
    async def exercise():
        updates = (
            {"chat_v2_max_tool_result_chars": 4000}
            if kind == "result"
            else (
                {"chat_v2_max_tool_output_chars": 5000}
                if kind == "total"
                else {"chat_v2_max_input_chars": 20000}
            )
        )
        steps = (
            [PLAN, tool("search_entries", filters=FILTERS)]
            if kind == "result"
            else [
                PLAN,
                tool(),
                tool("search_entries", filters=FILTERS),
                ANSWER,
            ]
        )
        if kind == "input":
            response = call_body("responses", tool(), 2)
            response["output"][0]["encrypted_content"] = "opaque" * 4000
            steps = [PLAN, httpx.Response(200, json=response)]
        if kind == "result":
            with pytest.raises(ServiceError, match="结果过多"):
                await execute(context_data, steps, **updates)
        else:
            outcome, script, _ = await execute(context_data, steps, **updates)
            assert outcome.action == "partial" and outcome.missing
            if kind == "total":
                assert "结果过多" in outcome.missing[0] and len(outcome.tool_results) == 1
                assert len(script.requests) == 3
            else:
                assert "内容过多" in outcome.missing[0]
                assert len(script.requests) < 4

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("kind", ["incomplete", "parallel", "bad_json", "refusal"])
def test_invalid_upstream_call_is_not_executed_or_retried(context_data, mode, kind):
    async def exercise():
        value = call_body(mode, tool(), 2)
        if mode == "responses":
            if kind == "incomplete":
                value["status"] = "incomplete"
            elif kind == "parallel":
                value["output"].append({**value["output"][-1], "call_id": "another"})
            elif kind == "bad_json":
                value["output"][-1]["arguments"] = "{private-malformed"
            else:
                value["output"].append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "private-refusal"}],
                    }
                )
        else:
            choice = value["choices"][0]
            if kind == "incomplete":
                choice["finish_reason"] = "length"
            elif kind == "parallel":
                choice["message"]["tool_calls"].append(
                    {**choice["message"]["tool_calls"][0], "id": "another"}
                )
            elif kind == "bad_json":
                choice["message"]["tool_calls"][0]["function"]["arguments"] = "{private-malformed"
            else:
                choice["message"]["refusal"] = "private-refusal"
        with pytest.raises(ServiceError, match="步骤不完整") as error:
            await execute(context_data, [PLAN, httpx.Response(200, json=value)], mode=mode)
        assert "private" not in error.value.detail
        assert context_data.session.scalar(select(func.count()).select_from(ChatTurn)) == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_reused_native_call_id_cannot_mismatch_tool_result(context_data, mode):
    async def exercise():
        first = call_body(mode, tool(), 2, call_id="same-id")
        second = call_body(mode, tool("search_entries", filters=FILTERS), 3, call_id="same-id")
        outcome, script, _ = await execute(
            context_data,
            [
                PLAN,
                httpx.Response(200, json=first),
                httpx.Response(200, json=second),
            ],
            mode=mode,
        )
        assert outcome.action == "partial" and outcome.tool_calls == 1 and len(script.requests) == 3

    asyncio.run(exercise())


def test_empty_result_is_verified_zero_but_unavailable_is_error(context_data):
    async def exercise():
        missing = {**FILTERS, "keywords": {"include": ["没有这条记录"]}}
        outcome, _, _ = await execute(context_data, [PLAN, tool(filters=missing), ANSWER])
        assert outcome.action == "answer"
        assert (
            next(f.value for f in outcome.tool_results[0].fact_refs if f.id == "q1.expense.total")
            == 0
        )
        with pytest.raises(ServiceError, match="暂时不可用"):
            await execute(context_data, [PLAN, httpx.Response(503, json={"private": "body"})])

    asyncio.run(exercise())


def test_account_access_is_checked_before_paid_request(context_data):
    async def exercise():
        state = await create(context_data)
        settings = configured(context_data)
        script = Script(settings, [])
        async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
            loop = ChatLoopV2(context_data.store, ChatModelV2(LlmClient(settings, client)))
            with pytest.raises(ServiceError):
                await loop.run(uuid4(), SendMessageV2(id=uuid4(), text="查询"))
        assert (
            not script.requests
            and (await context_data.store.load(state.conversation_id)).version == 0
        )

    asyncio.run(exercise())


def test_concurrent_success_causes_conflict_instead_of_returning_stale_answer(context_data):
    async def change_version(_payload):
        context_data.session.execute(update(ChatConversation).values(version=1))
        context_data.session.commit()
        return ANSWER

    async def exercise():
        with pytest.raises(ServiceError, match="其他请求"):
            await execute(context_data, [PLAN, tool(), change_version])

    asyncio.run(exercise())


def test_database_transaction_is_released_before_every_model_request(context_data):
    async def step(value):
        assert not context_data.session.in_transaction()
        return value

    async def exercise():
        outcome, _, _ = await execute(
            context_data,
            [
                lambda _p: step(PLAN),
                lambda _p: step(tool()),
                lambda _p: step(ANSWER),
            ],
        )
        assert outcome.action == "answer"

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["network", "timeout", "unsupported"])
def test_upstream_failures_are_sanitized_and_do_not_switch_protocol(context_data, failure):
    async def exercise():
        step = {
            "network": httpx.ConnectError("private-connection-details"),
            "timeout": httpx.ReadTimeout("private-request-details"),
            "unsupported": httpx.Response(400, json={"error": "private-rejected-tool-schema"}),
        }[failure]
        with pytest.raises(ServiceError) as error:
            await execute(context_data, [step])
        assert error.value.status_code == (504 if failure == "timeout" else 502)
        assert "private" not in error.value.detail

    asyncio.run(exercise())
