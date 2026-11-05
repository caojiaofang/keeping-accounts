"""真实开发 PostgreSQL/Redis、认证和 V2 SSE；模型模拟，测试数据精确回滚清理。"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_chat_v2_answer import SCOPED_PLAN
from test_chat_v2_api import BASE, events
from test_chat_v2_loop import ANSWER, FILTERS, Script, tool

from app.api.deps import get_db
from app.core.config import Settings
from app.integrations.llm import LlmClient
from app.main import create_app
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.services import chat_v2 as service_module
from app.services.sessions import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库和 Redis 集成测试",
    ),
]


def test_authenticated_v2_api_stream_partial_replay_and_isolation(monkeypatch):
    monkeypatch.setattr(service_module, "business_today", lambda: date(2026, 10, 27))
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if (
        settings.app_env not in {"development", "test"}
        or not settings.database_url
        or not settings.redis_url
    ):
        raise RuntimeError("V2 接口集成仅允许已配置开发数据库和 Redis 的开发环境")
    prefix = settings.redis_key_prefix + f"v2-api-test:{uuid4().hex}:"
    settings = settings.model_copy(
        update={
            "chat_v2_enabled": True,
            "chat_v2_tool_mode": "native",
            "llm_api_key": SecretStr("private-test-key"),
            "llm_api_mode": "responses",
            "llm_model": "gpt-6-luna",
            "redis_key_prefix": prefix,
        }
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 10}
    )
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    owner, member, book, cid = (uuid4() for _ in range(4))
    eids = [uuid4(), uuid4()]
    mids = [uuid4() for _ in range(3)]
    sessions = SessionStore(redis, settings)
    tokens, keys = [], []
    script = Script(
        settings,
        [
            SCOPED_PLAN,
            tool(),
            ANSWER,
            tool(
                "compare_periods",
                filters=FILTERS,
                previousRange={"start": "2026-10-14", "end": "2026-10-20"},
            ),
            {"action": "answer", "text": "差额 {{q1.expense.difference}}。"},
            SCOPED_PLAN,
            tool("search_entries", filters=FILTERS),
            httpx.Response(503, json={"private": "failure"}),
        ],
    )
    try:
        await redis.ping()
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(
                    insert(User),
                    [
                        {
                            "id": uid,
                            "openid": f"v2-api-{uid}",
                            "wechat_app_id": settings.wechat_app_id,
                        }
                        for uid in (owner, member)
                    ],
                )
                await connection.execute(
                    insert(Book).values(id=book, owner_id=owner, name="V2 接口测试", kind="shared")
                )
                await connection.execute(
                    insert(BookMember),
                    [
                        {
                            "book_id": book,
                            "user_id": uid,
                            "name": "接口测试人",
                            "status": "active",
                            "role": role,
                        }
                        for uid, role in ((owner, "owner"), (member, "member"))
                    ],
                )
                await connection.execute(
                    insert(LedgerEntry),
                    [
                        dict(
                            id=eid,
                            book_id=book,
                            created_by=owner,
                            type="expense",
                            category_id="coffee",
                            amount=amount,
                            date=day,
                            note="接口测试奶茶",
                            account="微信",
                            is_deleted=False,
                        )
                        for eid, amount, day in (
                            (eids[0], 1500, date(2026, 10, 26)),
                            (eids[1], 500, date(2026, 10, 14)),
                        )
                    ],
                )
                app = create_app(settings)
                app.state.redis, app.state.wechat = redis, None

                async def database():
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        yield session

                app.dependency_overrides[get_db] = database
                for uid in (owner, member):
                    token = await sessions.issue(uid)
                    tokens.append(token)
                    keys.append(sessions.session_key(token))
                headers = [{"Authorization": "Bearer " + token} for token in tokens]
                keys += [
                    f"{prefix}assistant:chat-rate:{owner}",
                    f"{prefix}assistant:chat-lock:{cid}",
                ]
                async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as model_client:
                    app.state.llm = LlmClient(settings, model_client)
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        capability = await client.get(BASE + "/capabilities", headers=headers[0])
                        assert capability.json() == {"schemaVersions": [2], "enabled": True}
                        assert (await client.get(BASE + "/capabilities")).status_code == 401
                        created = await client.post(
                            BASE + "/conversations",
                            headers=headers[0],
                            json={"id": str(cid), "bookId": str(book), "initialMonth": "2026-10"},
                        )
                        assert created.status_code == 200, created.text

                        async def send(body):
                            response = await client.post(
                                f"{BASE}/conversations/{cid}/messages/stream",
                                headers=headers[0],
                                json=body,
                            )
                            assert response.status_code == 200, response.text
                            return events(response)

                        first_body = {"id": str(mids[0]), "text": "最近七天花多少"}
                        first = (await send(first_body))[-1]
                        assert first["event"] == "done" and "¥15.00" in first["data"]["answer"]
                        assert (await send(first_body))[-1] == first and len(script.requests) == 3
                        assert await redis.get(f"{prefix}assistant:chat-lock:{cid}") is None
                        suggestion = next(
                            s for s in first["data"]["suggestions"] if "等长" in s["text"]
                        )
                        compared = (
                            await send(
                                {
                                    "id": str(mids[1]),
                                    "text": suggestion["text"],
                                    "suggestionId": suggestion["id"],
                                }
                            )
                        )[-1]
                        assert compared["event"] == "done" and compared["data"]["sequence"] == 2
                        assert "¥10.00" in compared["data"]["answer"] and len(script.requests) == 5
                        partial_body = {"id": str(mids[2]), "text": "继续查明细"}
                        partial = (await send(partial_body))[-1]
                        assert (
                            partial["event"] == "done"
                            and partial["data"]["completeness"] == "partial"
                        )
                        assert partial["data"]["missing"] and not partial["data"]["suggestions"]
                        assert partial["data"]["context"] == compared["data"]["context"]
                        assert (await send(partial_body))[-1] == partial and len(
                            script.requests
                        ) == 8
                        history = await client.get(
                            f"{BASE}/conversations/{cid}", headers=headers[0]
                        )
                        assert [t["sequence"] for t in history.json()["turns"]] == [1, 2, 3]
                        assert history.json()["context"] == compared["data"]["context"]
                        listed = await client.get(
                            BASE + "/conversations",
                            headers=headers[0],
                            params={"bookId": str(book)},
                        )
                        assert [row["id"] for row in listed.json()] == [str(cid)]
                        assert (
                            await client.get(f"{BASE}/conversations/{cid}", headers=headers[1])
                        ).status_code == 404
                        await connection.execute(
                            update(BookMember)
                            .where(BookMember.book_id == book, BookMember.user_id == owner)
                            .values(status="removed")
                        )
                        assert (
                            await client.get(f"{BASE}/conversations/{cid}", headers=headers[0])
                        ).status_code == 404
            finally:
                await outer.rollback()
            for model, condition in (
                (User, User.id.in_([owner, member])),
                (Book, Book.id == book),
                (LedgerEntry, LedgerEntry.id.in_(eids)),
                (ChatConversation, ChatConversation.id == cid),
                (ChatTurn, ChatTurn.id.in_(mids)),
            ):
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(model).where(condition)
                    )
                    == 0
                )
    finally:
        try:
            if keys:
                assert all(key.startswith(prefix) for key in keys)
                await redis.delete(*keys)
                assert await redis.exists(*keys) == 0
        finally:
            await redis.aclose()
            await engine.dispose()
