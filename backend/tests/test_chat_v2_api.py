"""通过真实 FastAPI、内存 SQL 和模型 HTTP 模拟验证 V2 API/SSE 生命周期。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID, uuid4

import httpx
import pytest
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from test_analysis_v2_queries import data as data
from test_chat_v2_answer import SCOPED_PLAN
from test_chat_v2_context_store import StoreSession, create, save
from test_chat_v2_context_store import context_data as context_data
from test_chat_v2_loop import ANSWER, FILTERS, Script, configured, tool

from app.api.deps import get_redis
from app.api.v1.ledger import get_ledger_service
from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.main import create_app
from app.models.chat import ChatConversation, ChatTurn
from app.models.shared import BookMember
from app.schemas.chat_v2 import AnswerPreviewV2, ContextPatch, ReferenceRequest, SendMessageV2
from app.services.chat import UNLOCK, ChatService
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.ledger import LedgerService

BASE = "/api/v1/assistant/v2"


class MemoryRedis:
    def __init__(self):
        self.locks, self.rates = {}, {}
        self.set_calls = 0
        self.failure = False

    async def set(self, key, value, *, ex, nx):
        if self.failure:
            raise RedisError("private-redis-error")
        self.set_calls += 1
        assert nx and ex >= 60
        if key in self.locks:
            return False
        self.locks[key] = value
        return True

    async def eval(self, script, _num, key, *args):
        if self.failure:
            raise RedisError("private-redis-error")
        if script == UNLOCK:
            if self.locks.get(key) == args[0]:
                del self.locks[key]
                return 1
            return 0
        self.rates[key] = self.rates.get(key, 0) + 1
        return self.rates[key]


def events(response):
    result = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        kind = next(line[7:] for line in lines if line.startswith("event: "))
        value = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        result.append({"event": kind, "data": value})
    return result


@asynccontextmanager
async def scenario(data, steps=None, *, enabled=True):
    settings = configured(data, chat_v2_enabled=enabled)
    script = Script(settings, steps or [])
    redis = MemoryRedis()
    app = create_app(settings)
    app.dependency_overrides[get_ledger_service] = lambda: data.ledger
    app.dependency_overrides[get_redis] = lambda: redis
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as model_client:
        app.state.llm = LlmClient(settings, model_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, script, redis, settings


async def new_conversation(client, data, month="2026-10", *, cid=None, book="shared"):
    body = {"id": str(cid or uuid4()), "bookId": str(data.books[book]), "initialMonth": month}
    result = await client.post(BASE + "/conversations", json=body)
    assert result.status_code == 200, result.text
    return result.json()["id"]


def message(text="最近七天花多少", *, mid=None):
    return {"id": str(mid or uuid4()), "text": text}


def test_capabilities_and_disabled_business_do_not_initialize_model(context_data):
    async def exercise():
        async with scenario(context_data, enabled=False) as (client, script, _, _):
            result = await client.get(BASE + "/capabilities")
            assert result.json() == {"schemaVersions": [2], "enabled": False}
            assert result.headers["cache-control"] == "no-store"
            body = {
                "id": str(uuid4()),
                "bookId": str(context_data.books["shared"]),
                "initialMonth": "2026-10",
            }
            result = await client.post(BASE + "/conversations", json=body)
            assert result.status_code == 404 and not script.requests

    asyncio.run(exercise())


def test_enabled_capability_and_routes_require_authentication():
    async def exercise():
        app = create_app(Settings(_env_file=None, chat_v2_enabled=True))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in (BASE + "/capabilities", BASE + "/conversations?bookId=" + str(uuid4())):
                result = await client.get(path)
                assert result.status_code == 401

    asyncio.run(exercise())


def test_api_complete_stream_saves_before_done_and_retries_replay_without_calls(context_data):
    async def exercise():
        async with scenario(context_data, [SCOPED_PLAN, tool(), ANSWER]) as (
            client,
            script,
            redis,
            _,
        ):
            cid = await new_conversation(client, context_data)
            body = message()
            result = await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=body)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
            assert result.headers["x-accel-buffering"] == "no"
            frames = events(result)
            assert frames[-1]["event"] == "done" and frames[-1]["data"]["id"] == body["id"]
            assert frames[-1]["data"]["completeness"] == "complete"
            snapshots = [e["data"] for e in frames if e["event"] == "snapshot"]
            assert len(snapshots) == 2 and snapshots[0]["answer"] == ""
            assert snapshots[-1]["answer"] == "".join(
                e["data"]["text"] for e in frames if e["event"] == "delta"
            )
            assert context_data.session.scalar(select(func.count()).select_from(ChatTurn)) == 1
            assert not redis.locks and redis.set_calls == 1 and len(script.requests) == 3
            # 模拟客户端丢失 done；同 ID 只能重放已保存结果，不重新调用模型/限流/锁。
            retry = events(
                await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=body)
            )
            assert [e["event"] for e in retry] == ["status", "done"]
            assert retry[-1] == frames[-1] and redis.set_calls == 1 and len(script.requests) == 3
            history = (await client.get(f"{BASE}/conversations/{cid}")).json()
            assert (
                history["turns"][0]["id"] == body["id"] and history["context"]["schemaVersion"] == 2
            )
            listed = (
                await client.get(
                    BASE + "/conversations", params={"bookId": str(context_data.books["shared"])}
                )
            ).json()
            assert [row["id"] for row in listed] == [cid]

    asyncio.run(exercise())


def test_duplicate_with_changed_text_or_suggestion_is_rejected(context_data):
    async def exercise():
        async with scenario(context_data, [SCOPED_PLAN, tool(), ANSWER]) as (client, script, _, _):
            cid = await new_conversation(client, context_data)
            body = message()
            await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=body)
            for changed in ({**body, "text": "改变问题"}, {**body, "suggestionId": str(uuid4())}):
                result = events(
                    await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=changed)
                )[-1]
                assert result["event"] == "error" and result["data"]["status"] == 409
            assert len(script.requests) == 3

    asyncio.run(exercise())


def test_partial_message_is_saved_but_not_inherited_or_automatically_referenced(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        first = await save(
            data, state.conversation_id, [{"field": "accounts", "op": "replace", "value": ["微信"]}]
        )
        cid = state.conversation_id
        async with scenario(
            data,
            [
                SCOPED_PLAN,
                tool("search_entries", filters=FILTERS),
                httpx.Response(503, json={"error": "private"}),
            ],
        ) as (client, script, _, settings):
            body = message()
            frames = events(
                await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=body)
            )
            assert frames[-1]["event"] == "done"
            partial = frames[-1]["data"]
            assert (
                partial["completeness"] == "partial"
                and partial["missing"]
                and not partial["suggestions"]
            )
            assert partial["sequence"] == 2
            restored = await data.store.load(cid)
            assert restored.version == 2 and restored.context == first.context
            prompt = await data.store.for_model(cid)
            assert all(turn["id"] != body["id"] for turn in prompt["recentTurns"])
            assert restored.context.summary_through_sequence == 1
            with pytest.raises(ServiceError, match="部分结果"):
                await data.store.resolve_reference(cid, ReferenceRequest(turn_id=body["id"]))
            with pytest.raises(ServiceError, match="部分结果"):
                await data.store.revise(
                    cid,
                    ContextPatch.model_validate(
                        {
                            "edits": [
                                {
                                    "field": "accounts",
                                    "op": "restore",
                                    "sourceTurnId": body["id"],
                                }
                            ]
                        }
                    ),
                )
            retry = events(
                await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=body)
            )
            assert retry[-1] == frames[-1] and len(script.requests) == 3
            # 下一轮完整成功从旧的成功条件继续，序号跨过已保存的部分轮次。
            store = ContextStoreV2(data.ledger, settings, today=date(2026, 10, 27))
            draft = await store.revise(cid, ContextPatch())
            third = await store.save_success(
                draft,
                SendMessageV2(id=uuid4(), text="继续"),
                AnswerPreviewV2(answer="后续完整结果", completeness="complete"),
            )
            assert third.sequence == 3 and third.context.filters.accounts == ["微信"]

    asyncio.run(exercise())


def test_history_paging_keeps_authoritative_current_context_and_lists_across_months(context_data):
    async def exercise():
        data = context_data
        first = await create(data)
        for index in range(25):
            await save(
                data,
                first.conversation_id,
                [{"field": "accounts", "op": "replace", "value": [f"账户{index}"]}],
            )
        async with scenario(data) as (client, _, _, _):
            second = await new_conversation(client, data, "2026-09")
            await save(data, UUID(second))
            current = (await client.get(f"{BASE}/conversations/{first.conversation_id}")).json()
            assert current["hasMore"] and [t["sequence"] for t in current["turns"]] == list(
                range(6, 26)
            )
            earlier = (
                await client.get(
                    f"{BASE}/conversations/{first.conversation_id}", params={"before": 6}
                )
            ).json()
            assert not earlier["hasMore"] and [t["sequence"] for t in earlier["turns"]] == list(
                range(1, 6)
            )
            assert earlier["context"]["filters"]["accounts"] == ["账户24"]
            rows = (
                await client.get(
                    BASE + "/conversations", params={"bookId": str(data.books["shared"])}
                )
            ).json()
            assert {r["initialMonth"] for r in rows} == {"2026-09", "2026-10"}

    asyncio.run(exercise())


def test_legacy_readonly_fork_and_protocol_isolation(context_data):
    async def exercise():
        data = context_data
        legacy = uuid4()
        data.session.add(
            ChatConversation(
                id=legacy,
                owner_id=data.users["owner"],
                book_id=data.books["shared"],
                month="2026-10",
                version=0,
                context={"month": "2026-09", "categoryId": "food"},
            )
        )
        data.session.commit()
        async with scenario(data) as (client, script, _, _):
            assert (await client.get(f"{BASE}/conversations/{legacy}")).status_code == 409
            old = await client.get(f"{BASE}/legacy/conversations/{legacy}")
            assert old.json()["readonly"] and old.json()["sourceSchemaVersion"] == 1
            new = uuid4()
            body = {"id": str(new), "bookId": str(data.books["shared"]), "initialMonth": "2026-10"}
            assert (
                await client.post(f"{BASE}/legacy/conversations/{legacy}/fork", json=body)
            ).status_code == 200
            copied = (await client.get(f"{BASE}/conversations/{new}")).json()
            assert copied["context"]["filters"]["dateRange"]["start"] == "2026-09-01"
            assert copied["context"]["filters"]["categoryIds"] == ["food"]
            assert not copied["turns"] and not script.requests
            service = ChatService(data.ledger, None, None, data.settings)
            with pytest.raises(ServiceError):
                await service.require_conversation(new)
            assert (await data.store.load(legacy, allow_legacy=True)).version == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["lock", "rate", "redis"])
def test_lock_rate_and_redis_failure_stop_before_model(context_data, failure):
    async def exercise():
        async with scenario(context_data) as (client, script, redis, settings):
            cid = await new_conversation(client, context_data)
            if failure == "lock":
                redis.locks[f"{settings.redis_key_prefix}assistant:chat-lock:{cid}"] = "other-task"
            elif failure == "rate":
                redis.rates[
                    f"{settings.redis_key_prefix}assistant:chat-rate:{context_data.users['owner']}"
                ] = 10
            else:
                redis.failure = True
            result = events(
                await client.post(f"{BASE}/conversations/{cid}/messages/stream", json=message())
            )[-1]
            assert (
                result["event"] == "error"
                and result["data"]["status"] == {"lock": 409, "rate": 429, "redis": 503}[failure]
            )
            assert not script.requests and "private" not in result["data"]["detail"]

    asyncio.run(exercise())


def test_account_isolation_and_removed_member_block_reads_and_first_stream_packet(context_data):
    async def exercise():
        data = context_data
        state = await create(data)
        foreign = LedgerService(StoreSession(data.session), data.users["member"])
        async with scenario(data) as (client, script, _, _):
            app = client._transport.app
            app.dependency_overrides[get_ledger_service] = lambda: foreign
            assert (
                await client.get(f"{BASE}/conversations/{state.conversation_id}")
            ).status_code == 404
            assert (
                await client.post(
                    f"{BASE}/conversations/{state.conversation_id}/messages/stream", json=message()
                )
            ).status_code == 404
            app.dependency_overrides[get_ledger_service] = lambda: data.ledger
            data.session.execute(
                update(BookMember)
                .where(
                    BookMember.book_id == data.books["shared"],
                    BookMember.user_id == data.users["owner"],
                )
                .values(status="removed")
            )
            data.session.commit()
            result = await client.post(
                f"{BASE}/conversations/{state.conversation_id}/messages/stream", json=message()
            )
            assert result.status_code == 404 and "text/event-stream" not in result.headers.get(
                "content-type", ""
            )
            assert not script.requests

    asyncio.run(exercise())


@pytest.mark.parametrize("extra", ["filters", "bookId", "userId", "tool", "factRefs"])
def test_message_api_rejects_untrusted_extra_fields(context_data, extra):
    async def exercise():
        async with scenario(context_data) as (client, script, _, _):
            cid = await new_conversation(client, context_data)
            result = await client.post(
                f"{BASE}/conversations/{cid}/messages/stream",
                json={**message(), extra: "private-input"},
            )
            assert (
                result.status_code == 422
                and "private-input" not in result.text
                and not script.requests
            )

    asyncio.run(exercise())
