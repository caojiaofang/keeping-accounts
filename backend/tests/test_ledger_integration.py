"""真实开发库和 Redis 的业务闭环，测试用户及账本最终全部回滚。"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.auth_deps import get_session_store
from app.api.deps import get_db
from app.core.config import Settings
from app.main import create_app
from app.models.ledger import Book
from app.models.user import User
from app.services.sessions import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库集成测试",
    ),
]


def test_cloud_ledger_roundtrip_and_permissions():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("集成测试只允许开发数据库")
    engine = create_async_engine(settings.database_url.get_secret_value())
    app = create_app(settings)
    user_ids = [uuid4(), uuid4()]
    try:
        async with app.router.lifespan_context(app):
            store = SessionStore(app.state.redis, settings)
            store.prefix += f"ledger-integration:{uuid4().hex}:"
            try:
                async with engine.connect() as connection:
                    transaction = await connection.begin()
                    try:
                        for user_id in user_ids:
                            await connection.execute(
                                insert(User).values(
                                    id=user_id,
                                    wechat_app_id=settings.wechat_app_id,
                                    openid=f"ledger-test-{user_id.hex}",
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
                            {"Authorization": "Bearer " + await store.issue(user_id)}
                            for user_id in user_ids
                        ]
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app), base_url="http://test"
                        ) as client:

                            async def request(method, path, body=None, who=0, expected=200):
                                response = await client.request(
                                    method, "/api/v1" + path, json=body, headers=headers[who]
                                )
                                assert response.status_code == expected, response.text
                                assert response.headers["Cache-Control"] == "no-store"
                                return response.json() if response.content else None

                            first = await request("GET", "/ledger")
                            again = await request("GET", "/ledger")
                            assert len(first["books"]) == 1 and not first["entries"]
                            assert first == again
                            other = await request("GET", "/ledger", who=1)
                            book = first["books"][0]
                            foreign_id = other["books"][0]["id"]
                            assert book["id"] != foreign_id

                            payload = {"id": str(uuid4()), "name": "  测试旅行  "}
                            new_book = await request("POST", "/books", payload)
                            assert new_book["name"] == "测试旅行"
                            assert new_book == await request("POST", "/books", payload)
                            await request("POST", "/books", payload, who=1, expected=404)

                            budget = {"budget": 123456, "version": book["version"]}
                            changed_book = await request(
                                "PUT", f"/books/{book['id']}/budget", budget
                            )
                            assert changed_book["budget"] == 123456
                            await request(
                                "PUT", f"/books/{book['id']}/budget", budget, expected=409
                            )
                            await request(
                                "PUT", f"/books/{foreign_id}/budget", budget, expected=404
                            )

                            entry = {
                                "id": str(uuid4()),
                                "bookId": book["id"],
                                "type": "expense",
                                "categoryId": "food",
                                "amount": 800,
                                "date": "2026-10-24",
                                "time": "08:00",
                                "account": "微信",
                                "note": "早餐",
                            }
                            saved = await request("POST", "/entries", entry)
                            assert saved["createdBy"] == str(user_ids[0])
                            assert saved["createdAt"] > 0 and saved["amount"] == 800
                            assert saved == await request("POST", "/entries", entry)
                            await request(
                                "POST", "/entries", {**entry, "amount": 900}, expected=409
                            )
                            await request(
                                "POST",
                                "/entries",
                                {**entry, "id": str(uuid4()), "bookId": foreign_id},
                                expected=404,
                            )
                            await request(
                                "POST",
                                "/entries",
                                {**entry, "createdBy": str(user_ids[1])},
                                expected=422,
                            )
                            data = await request("GET", "/ledger")
                            assert len(data["entries"]) == 1
                            assert not (await request("GET", "/ledger", who=1))["entries"]

                            update = {key: value for key, value in entry.items() if key != "id"}
                            update.update(amount=1250, version=1, time=None, bookId=new_book["id"])
                            await request(
                                "PUT", f"/entries/{saved['id']}", update, who=1, expected=404
                            )
                            modified = await request("PUT", f"/entries/{saved['id']}", update)
                            assert modified["version"] == 2 and modified["amount"] == 1250
                            assert modified["bookId"] == new_book["id"] and "time" not in modified
                            assert modified["createdBy"] == saved["createdBy"]
                            assert modified["createdAt"] == saved["createdAt"]
                            await request("PUT", f"/entries/{saved['id']}", update, expected=409)
                            await request(
                                "DELETE", f"/entries/{saved['id']}?version=1", expected=409
                            )
                            await request(
                                "DELETE", f"/entries/{saved['id']}?version=2", who=1, expected=404
                            )
                            await request(
                                "DELETE", f"/entries/{saved['id']}?version=2", expected=204
                            )
                            await request(
                                "DELETE", f"/entries/{saved['id']}?version=2", expected=204
                            )
                            assert not (await request("GET", "/ledger"))["entries"]
                            await request("POST", "/entries", entry, expected=409)

                            if os.getenv("MOJI_RUN_LLM_TESTS") != "1":
                                app.state.llm = SimpleNamespace(
                                    structured=AsyncMock(
                                        return_value={
                                            "drafts": [
                                                {
                                                    "type": "expense",
                                                    "amount": 800,
                                                    "category_id": "food",
                                                    "date": "2026-10-24",
                                                    "time": "08:00",
                                                    "note": "早餐",
                                                    "account": None,
                                                }
                                            ],
                                            "clarification": None,
                                        }
                                    )
                                )
                            await request(
                                "POST",
                                "/assistant/parse",
                                {
                                    "text": "今天早上八点吃早餐8块钱",
                                    "bookId": foreign_id,
                                },
                                expected=404,
                            )
                            parsed = await request(
                                "POST",
                                "/assistant/parse",
                                {
                                    "text": "今天早上八点吃早餐8块钱",
                                    "bookId": book["id"],
                                },
                            )
                            assert len(parsed["drafts"]) == 1
                            draft = parsed["drafts"][0]
                            assert draft["amount"] == 800 and draft["time"] == "08:00"
                            assert draft["accountDefaulted"] is True
                            assert not (await request("GET", "/ledger"))["entries"]
                            confirmation = {
                                key: value
                                for key, value in draft.items()
                                if key not in {"key", "status", "accountDefaulted"}
                            }
                            confirmation["id"] = draft["key"]
                            saved_draft = await request("POST", "/entries", confirmation)
                            assert saved_draft == await request("POST", "/entries", confirmation)
                            assert len((await request("GET", "/ledger"))["entries"]) == 1

                            token = headers[0]["Authorization"].split()[1]
                            await store.revoke(token)
                            await request("GET", "/ledger", expected=401)
                    finally:
                        await transaction.rollback()
            finally:
                keys = [key async for key in store.redis.scan_iter(match=store.prefix + "*")]
                if keys:
                    await store.redis.delete(*keys)
                await store.redis.delete(
                    *[
                        f"{settings.redis_key_prefix}assistant:parse:{user_id}"
                        for user_id in user_ids
                    ]
                )
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count()).select_from(Book).where(Book.owner_id.in_(user_ids))
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count()).select_from(User).where(User.id.in_(user_ids))
                )
                == 0
            )
    finally:
        await engine.dispose()
