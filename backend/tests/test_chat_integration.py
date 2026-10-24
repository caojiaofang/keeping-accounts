"""开发库会话闭环；临时用户、账单和问答最终回滚，Redis 使用独立测试身份。"""

import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.auth_deps import get_session_store
from app.api.deps import get_db
from app.core.config import Settings
from app.main import create_app
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.user import User
from app.schemas.chat import QueryPlan
from app.services import chat as chat_module
from app.services.sessions import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库集成测试",
    ),
]


def test_chat_roundtrip_isolation_queries_and_retries(monkeypatch):
    monkeypatch.setattr(chat_module, "business_today", lambda: date(2026, 10, 24))
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("仅允许使用开发数据库")
    engine = create_async_engine(settings.database_url.get_secret_value())
    app = create_app(settings)
    users, books = [uuid4(), uuid4()], [uuid4(), uuid4(), uuid4()]
    conversations = []
    rate_key = f"{settings.redis_key_prefix}assistant:chat-rate:{users[0]}"
    try:
        async with app.router.lifespan_context(app):
            store = SessionStore(app.state.redis, settings)
            store.prefix += f"chat-integration:{uuid4().hex}:"
            real_llm = app.state.llm
            current_plan, explanation = {}, "可以先核对主要分类中的记录，再决定是否调整消费。"

            async def model(instructions, text, schema):
                if "action" in schema["properties"]:
                    return current_plan
                return {"text": explanation}

            mock_llm = SimpleNamespace(structured=AsyncMock(side_effect=model))
            app.state.llm = mock_llm
            try:
                async with engine.connect() as connection:
                    outer = await connection.begin()
                    try:
                        for user_id in users:
                            await connection.execute(
                                insert(User).values(
                                    id=user_id,
                                    wechat_app_id=settings.wechat_app_id,
                                    openid=f"chat-test-{user_id}",
                                )
                            )
                        for index, book_id in enumerate(books):
                            await connection.execute(
                                insert(Book).values(
                                    id=book_id,
                                    owner_id=users[1] if index == 1 else users[0],
                                    name="测试账本",
                                )
                            )

                        async def database():
                            async with AsyncSession(
                                bind=connection,
                                expire_on_commit=False,
                                join_transaction_mode="create_savepoint",
                            ) as session:
                                yield session

                        app.dependency_overrides[get_db] = database
                        app.dependency_overrides[get_session_store] = lambda: store
                        headers = [
                            {"Authorization": "Bearer " + await store.issue(user)} for user in users
                        ]

                        async def seed(book, category, amount, day, deleted=False):
                            await connection.execute(
                                insert(LedgerEntry).values(
                                    id=uuid4(),
                                    book_id=books[book],
                                    created_by=users[1] if book == 1 else users[0],
                                    type="income" if category == "salary" else "expense",
                                    category_id=category,
                                    amount=amount,
                                    date=date.fromisoformat(day),
                                    account="微信",
                                    note="测试记录",
                                    is_deleted=deleted,
                                )
                            )

                        for args in [
                            (0, "food", 800, "2026-10-01"),
                            (0, "food", 1250, "2026-10-02"),
                            (0, "transport", 20000, "2026-10-03"),
                            (0, "shopping", 20001, "2026-10-04"),
                            (0, "food", 50000, "2026-10-25"),
                            (0, "salary", 999900, "2026-10-03"),
                            (0, "food", 99999, "2026-10-01", True),
                            (0, "food", 1000, "2026-09-01"),
                            (0, "food", 9000, "2026-09-30"),
                            (1, "food", 77777, "2026-10-01"),
                            (2, "food", 88888, "2026-10-01"),
                        ]:
                            await seed(*args)

                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app), base_url="http://test"
                        ) as client:

                            async def request(method, path, body=None, who=0, expected=200):
                                response = await client.request(
                                    method,
                                    "/api/v1/assistant" + path,
                                    json=body,
                                    headers=headers[who],
                                )
                                assert response.status_code == expected, response.text
                                assert response.headers["Cache-Control"] == "no-store"
                                return response.json()

                            async def create(book=0):
                                cid = uuid4()
                                conversations.append(cid)
                                payload = {
                                    "id": str(cid),
                                    "bookId": str(books[book]),
                                    "month": "2026-10",
                                }
                                result = await request("POST", "/conversations", payload)
                                assert result == await request("POST", "/conversations", payload)
                                return cid

                            cid = await create()
                            assert not await request(
                                "GET", f"/conversations?bookId={books[0]}&month=2026-10"
                            )
                            foreign = await request(
                                "POST",
                                "/conversations",
                                {"id": str(uuid4()), "bookId": str(books[1]), "month": "2026-10"},
                                expected=404,
                            )
                            assert "detail" in foreign
                            await request("GET", f"/conversations/{cid}", who=1, expected=404)
                            await request(
                                "POST",
                                f"/conversations/{cid}/messages",
                                {"id": str(uuid4()), "text": "看看"},
                                who=1,
                                expected=404,
                            )
                            mock_llm.structured.assert_not_awaited()

                            async def send(action="overview", question="这个月花费分布", **changes):
                                nonlocal current_plan
                                current_plan = QueryPlan(
                                    action=action,
                                    month=None,
                                    category_mode=changes.pop("category_mode", "all"),
                                    category_id=changes.pop("category_id", None),
                                    entry_type=changes.pop("entry_type", "expense"),
                                    threshold=changes.pop("threshold", None),
                                    comparison=changes.pop("comparison", None),
                                    clarification=None,
                                    **changes,
                                ).model_dump()
                                await store.redis.delete(rate_key)
                                payload = {"id": str(uuid4()), "text": question}
                                return await request(
                                    "POST", f"/conversations/{cid}/messages", payload
                                ), payload

                            overview, payload = await send()
                            assert (
                                overview["result"]["total"] == 42051
                                and overview["result"]["count"] == 4
                            )
                            assert len(overview["result"]["rows"]) == 3
                            assert sum(row["amount"] for row in overview["result"]["rows"]) == 42051
                            calls = mock_llm.structured.await_count
                            assert overview == await request(
                                "POST", f"/conversations/{cid}/messages", payload
                            )
                            assert mock_llm.structured.await_count == calls
                            await request(
                                "POST",
                                f"/conversations/{cid}/messages",
                                {**payload, "text": "不同问题"},
                                expected=409,
                            )

                            details, _ = await send(
                                "details", "餐饮明细", category_mode="selected", category_id="food"
                            )
                            assert (
                                details["result"]["total"] == 2050
                                and details["result"]["count"] == 2
                            )
                            assert [row["amount"] for row in details["result"]["rows"]] == [
                                1250,
                                800,
                            ]
                            compare, _ = await send(
                                "compare",
                                "这一类和上月相比呢",
                                category_mode="inherit",
                                entry_type=None,
                            )
                            assert compare["context"]["categoryId"] == "food"
                            assert [row["amount"] for row in compare["result"]["rows"]] == [
                                2050,
                                1000,
                            ]
                            assert "2026-09-24" in compare["result"]["footnote"]
                            full, _ = await send(
                                "compare",
                                "和上月全月相比",
                                category_mode="inherit",
                                comparison="full_month",
                            )
                            assert [row["amount"] for row in full["result"]["rows"]] == [
                                2050,
                                10000,
                            ]
                            assert "减少 ¥79.50" in full["answer"]
                            large, _ = await send("large", "超过200元的支出", threshold=20000)
                            assert (
                                large["result"]["total"] == 20001 and large["result"]["count"] == 1
                            )
                            income, _ = await send("overview", "我的收入呢", entry_type="income")
                            assert (
                                income["result"]["total"] == 999900
                                and income["result"]["entryType"] == "income"
                            )
                            empty, _ = await send(
                                "details",
                                "医疗支出",
                                category_mode="selected",
                                category_id="health",
                            )
                            assert empty["result"]["total"] == 0 and not empty["result"]["rows"]
                            record, _ = await send("record", "帮我删除所有账单")
                            assert "不会修改" in record["answer"] and "result" not in record
                            assert (
                                await connection.scalar(
                                    select(func.count())
                                    .select_from(LedgerEntry)
                                    .where(LedgerEntry.book_id == books[0])
                                )
                                == 9
                            )

                            explanation = "错误金额999元"
                            pending = {"id": str(uuid4()), "text": "重试分析"}
                            current_plan = {
                                **current_plan,
                                "action": "overview",
                                "category_mode": "all",
                            }
                            await store.redis.delete(rate_key)
                            await request(
                                "POST", f"/conversations/{cid}/messages", pending, expected=502
                            )
                            before_retry = await request("GET", f"/conversations/{cid}")
                            assert len(before_retry["turns"]) == 8
                            lock_key = f"{settings.redis_key_prefix}assistant:chat-lock:{cid}"
                            assert await store.redis.get(lock_key) is None
                            explanation = "先从记录较多的分类开始核对。"
                            retried = await request(
                                "POST", f"/conversations/{cid}/messages", pending
                            )
                            assert retried["sequence"] == 9
                            await store.redis.set(lock_key, "another-request", ex=120)
                            await request(
                                "POST",
                                f"/conversations/{cid}/messages",
                                {"id": str(uuid4()), "text": "并发"},
                                expected=409,
                            )
                            assert await store.redis.get(lock_key) == "another-request"
                            await store.redis.delete(lock_key)
                            await store.redis.set(rate_key, 10, ex=60)
                            calls = mock_llm.structured.await_count
                            await request(
                                "POST",
                                f"/conversations/{cid}/messages",
                                {"id": str(uuid4()), "text": "限流"},
                                expected=429,
                            )
                            assert mock_llm.structured.await_count == calls

                            history_id = await create()
                            await connection.execute(
                                insert(ChatTurn),
                                [
                                    {
                                        "id": uuid4(),
                                        "conversation_id": history_id,
                                        "sequence": i,
                                        "text": "历史问题",
                                        "answer": "历史回答",
                                        "result": None,
                                        "context": {"month": "2026-10", "entryType": "expense"},
                                    }
                                    for i in range(1, 26)
                                ],
                            )
                            await connection.execute(
                                update(ChatConversation)
                                .where(ChatConversation.id == history_id)
                                .values(version=25)
                            )
                            latest = await request("GET", f"/conversations/{history_id}")
                            assert latest["hasMore"] and [
                                t["sequence"] for t in latest["turns"]
                            ] == list(range(6, 26))
                            earlier = await request("GET", f"/conversations/{history_id}?before=6")
                            assert not earlier["hasMore"] and len(earlier["turns"]) == 5

                            if os.getenv("MOJI_RUN_LLM_TESTS") == "1":
                                app.state.llm = real_llm
                                cid = await create()
                                await store.redis.delete(rate_key)
                                real = await request(
                                    "POST",
                                    f"/conversations/{cid}/messages",
                                    {"id": str(uuid4()), "text": "2026年10月餐饮具体花在哪了？"},
                                )
                                assert (
                                    real["context"]["categoryId"] == "food"
                                    and real["result"]["total"] == 2050
                                )
                                following = await request(
                                    "POST",
                                    f"/conversations/{cid}/messages",
                                    {"id": str(uuid4()), "text": "这一类和上个月同期相比呢？"},
                                )
                                assert following["context"]["categoryId"] == "food"
                                assert [r["amount"] for r in following["result"]["rows"]] == [
                                    2050,
                                    1000,
                                ]
                    finally:
                        await outer.rollback()
            finally:
                keys = [key async for key in store.redis.scan_iter(match=store.prefix + "*")]
                keys.extend(
                    f"{settings.redis_key_prefix}assistant:chat-lock:{cid}" for cid in conversations
                )
                keys.append(rate_key)
                await store.redis.delete(*keys)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(ChatConversation)
                    .where(ChatConversation.id.in_(conversations))
                )
                == 0
            )
    finally:
        await engine.dispose()
