"""真实开发存储的多人权限闭环，临时用户及业务记录最终回滚。"""

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
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
from app.models.chat import ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookInvitation, BookMember
from app.models.user import User
from app.services.sessions import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库集成测试",
    ),
]


def test_shared_book_permissions_invites_and_lifecycle():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or not settings.database_url:
        raise RuntimeError("共享集成测试只允许开发库")
    engine = create_async_engine(settings.database_url.get_secret_value())
    app = create_app(settings)
    users = [uuid4() for _ in range(3)]
    book_id, entries = uuid4(), [uuid4(), uuid4()]
    client_ip = "198.51.100.89"
    preview_key = (
        settings.redis_key_prefix
        + "invite-preview:"
        + hashlib.sha256(client_ip.encode()).hexdigest()
    )
    try:
        async with app.router.lifespan_context(app):
            sessions = SessionStore(app.state.redis, settings)
            sessions.prefix += f"shared-test:{uuid4().hex}:"
            remove_during_explanation = None

            async def model(instructions, text, schema):
                nonlocal remove_during_explanation
                if "action" in schema["properties"]:
                    return {
                        "action": "overview",
                        "month": None,
                        "category_mode": "all",
                        "category_id": None,
                        "entry_type": "expense",
                        "threshold": None,
                        "comparison": None,
                        "clarification": None,
                    }
                if remove_during_explanation:
                    action, remove_during_explanation = remove_during_explanation, None
                    await action()
                return {"text": "共同收支可根据记录逐项核对。"}

            llm = SimpleNamespace(structured=AsyncMock(side_effect=model))
            app.state.llm = llm
            try:
                async with engine.connect() as connection:
                    outer = await connection.begin()
                    try:
                        for user in users:
                            await connection.execute(
                                insert(User).values(
                                    id=user,
                                    wechat_app_id=settings.wechat_app_id,
                                    openid=f"shared-test-{user}",
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
                        app.dependency_overrides[get_session_store] = lambda: sessions
                        headers = [
                            {"Authorization": "Bearer " + await sessions.issue(user)}
                            for user in users
                        ]
                        transport = httpx.ASGITransport(app=app, client=(client_ip, 12345))
                        async with httpx.AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as client:

                            async def request(method, path, body=None, who=0, expected=200):
                                response = await client.request(
                                    method,
                                    "/api/v1" + path,
                                    json=body,
                                    headers=headers[who] if who is not None else {},
                                )
                                assert response.status_code == expected, (
                                    path,
                                    response.status_code,
                                    response.text,
                                )
                                assert response.headers["Cache-Control"] == "no-store"
                                return response.json() if response.content else None

                            async def current():
                                return await request("GET", f"/books/{book_id}")

                            async def invite(replace=False):
                                return await request(
                                    "POST",
                                    f"/books/{book_id}/invitations",
                                    {"id": str(uuid4()), "replace": replace},
                                )

                            async def preview(token, expected=200):
                                return await request(
                                    "POST",
                                    "/book-invitations/preview",
                                    {"token": token},
                                    who=None,
                                    expected=expected,
                                )

                            personal = [
                                (await request("GET", "/ledger", who=who))["books"][0]["id"]
                                for who in range(3)
                            ]
                            create = {
                                "id": str(book_id),
                                "name": "测试家庭",
                                "kind": "shared",
                                "memberName": "小明",
                            }
                            book = await request("POST", "/books", create)
                            assert book == await request("POST", "/books", create)
                            assert book["kind"] == "shared" and book["members"][0]["userId"] == str(
                                users[0]
                            )
                            await request("GET", f"/books/{book_id}", who=1, expected=404)
                            await request("GET", f"/books/{personal[0]}", who=1, expected=404)

                            first = await invite()
                            assert first == await request(
                                "POST", f"/books/{book_id}/invitations", {"id": first["id"]}
                            )
                            assert first == await invite()
                            limited = await preview(first["token"])
                            assert set(limited) == {
                                "bookId",
                                "bookName",
                                "inviterName",
                                "expiresAt",
                            }
                            assert limited["inviterName"] == "小明"
                            assert not any(
                                b["id"] == str(book_id)
                                for b in (await request("GET", "/ledger", who=1))["books"]
                            )
                            await preview("demo-old-invite", 410)
                            await preview(
                                first["token"][:-1] + ("a" if first["token"][-1] != "a" else "b"),
                                410,
                            )
                            digest = await connection.scalar(
                                select(BookInvitation.token_hash).where(
                                    BookInvitation.id == first["id"]
                                )
                            )
                            assert digest == hashlib.sha256(first["token"].encode()).hexdigest()
                            joined = await request(
                                "POST",
                                "/book-invitations/accept",
                                {"token": first["token"], "name": "小林"},
                                who=1,
                            )
                            assert joined["currentUserId"] == str(users[1])
                            assert joined == await request(
                                "POST",
                                "/book-invitations/accept",
                                {"token": first["token"], "name": "小林"},
                                who=1,
                            )
                            assert len(joined["members"]) == 2

                            payloads = [
                                {
                                    "id": str(entries[who]),
                                    "bookId": str(book_id),
                                    "type": "expense",
                                    "categoryId": "food",
                                    "amount": amount,
                                    "date": "2026-10-01",
                                    "account": "微信",
                                    "note": "共同早餐",
                                }
                                for who, amount in enumerate((800, 1250))
                            ]
                            for who in (0, 1):
                                saved = await request("POST", "/entries", payloads[who], who=who)
                                assert saved["createdBy"] == str(users[who])
                                assert saved == await request(
                                    "POST", "/entries", payloads[who], who=who
                                )
                            visible = (await request("GET", "/ledger", who=1))["entries"]
                            assert {e["id"] for e in visible} == {str(value) for value in entries}
                            await request(
                                "POST",
                                "/entries",
                                {**payloads[0], "id": str(uuid4())},
                                who=2,
                                expected=404,
                            )
                            owner_input = {k: v for k, v in payloads[0].items() if k != "id"}
                            await request(
                                "PUT",
                                f"/entries/{entries[0]}",
                                {**owner_input, "version": 1},
                                who=1,
                                expected=403,
                            )
                            await request(
                                "DELETE", f"/entries/{entries[0]}?version=1", who=1, expected=403
                            )
                            await request(
                                "PUT",
                                f"/books/{book_id}/budget",
                                {"version": (await current())["version"], "budget": 200000},
                                who=1,
                                expected=403,
                            )
                            await request(
                                "POST",
                                f"/books/{book_id}/invitations",
                                {"id": str(uuid4())},
                                who=1,
                                expected=403,
                            )
                            await request(
                                "DELETE",
                                f"/books/{book_id}/members/{users[0]}?version=1",
                                expected=403,
                            )
                            await request(
                                "POST",
                                f"/books/{book_id}/transfer",
                                {"version": (await current())["version"], "userId": str(users[2])},
                                expected=422,
                            )
                            member_input = {k: v for k, v in payloads[1].items() if k != "id"}
                            modified = await request(
                                "PUT",
                                f"/entries/{entries[1]}",
                                {**member_input, "version": 1, "amount": 1600},
                            )
                            assert modified["createdBy"] == str(users[1]) and modified[
                                "updatedBy"
                            ] == str(users[0])
                            await request(
                                "PUT",
                                f"/entries/{entries[1]}",
                                {**member_input, "version": 1, "amount": 1700},
                                who=1,
                                expected=409,
                            )
                            modified = await request(
                                "PUT",
                                f"/entries/{entries[1]}",
                                {**member_input, "version": 2, "amount": 1800},
                                who=1,
                            )
                            assert modified["version"] == 3 and modified["updatedBy"] == str(
                                users[1]
                            )
                            activities = await request("GET", f"/books/{book_id}/activities", who=1)
                            edits = [
                                a
                                for a in activities
                                if a["action"] == "update_entry" and a["actorId"] == str(users[0])
                            ]
                            assert edits[0]["payload"]["before"]["amount"] == 1250
                            assert edits[0]["payload"]["after"]["amount"] == 1600
                            await request(
                                "PUT",
                                f"/entries/{entries[0]}",
                                {**owner_input, "bookId": personal[0], "version": 1},
                                expected=422,
                            )
                            await request(
                                "POST",
                                "/entries",
                                {**payloads[1], "id": str(uuid4()), "createdBy": str(users[0])},
                                who=1,
                                expected=422,
                            )

                            conversation = str(uuid4())
                            await request(
                                "POST",
                                "/assistant/conversations",
                                {"id": conversation, "bookId": str(book_id), "month": "2026-10"},
                                who=1,
                            )
                            analysis = await request(
                                "POST",
                                f"/assistant/conversations/{conversation}/messages",
                                {"id": str(uuid4()), "text": "共同支出分布"},
                                who=1,
                            )
                            assert (
                                analysis["result"]["total"] == 2600
                                and analysis["result"]["count"] == 2
                            )
                            await request(
                                "GET", f"/assistant/conversations/{conversation}", expected=404
                            )
                            second = await invite(True)
                            await preview(first["token"], 410)
                            await connection.execute(
                                update(BookInvitation)
                                .where(BookInvitation.id == second["id"])
                                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                            )
                            await preview(second["token"], 410)
                            third = await invite(True)
                            await request(
                                "POST",
                                "/book-invitations/accept",
                                {"token": third["token"], "name": "家人"},
                                who=2,
                            )

                            async def remove_member_while_model_waits():
                                version = (await current())["version"]
                                await request(
                                    "DELETE",
                                    f"/books/{book_id}/members/{users[1]}?version={version}",
                                )

                            remove_during_explanation = remove_member_while_model_waits
                            await request(
                                "POST",
                                f"/assistant/conversations/{conversation}/messages",
                                {"id": str(uuid4()), "text": "再分析一下"},
                                who=1,
                                expected=404,
                            )
                            assert (
                                await connection.scalar(
                                    select(func.count())
                                    .select_from(ChatTurn)
                                    .where(ChatTurn.conversation_id == conversation)
                                )
                                == 1
                            )
                            for path in (
                                f"/books/{book_id}",
                                f"/books/{book_id}/members",
                                f"/books/{book_id}/activities",
                                f"/assistant/conversations/{conversation}",
                            ):
                                await request("GET", path, who=1, expected=404)
                            await request(
                                "POST",
                                "/entries",
                                {**payloads[1], "id": str(uuid4())},
                                who=1,
                                expected=404,
                            )
                            await request(
                                "PUT",
                                f"/entries/{entries[1]}",
                                {**member_input, "version": 3},
                                who=1,
                                expected=404,
                            )
                            await request(
                                "POST",
                                "/assistant/parse",
                                {"text": "早餐8元", "bookId": str(book_id)},
                                who=1,
                                expected=404,
                            )
                            await preview(third["token"], 410)
                            assert not any(
                                b["id"] == str(book_id)
                                for b in (await request("GET", "/ledger", who=1))["books"]
                            )
                            assert len((await request("GET", "/ledger"))["entries"]) == 2
                            fourth = await invite(True)
                            await request(
                                "POST",
                                "/book-invitations/accept",
                                {"token": fourth["token"], "name": "小林"},
                                who=1,
                            )
                            assert (
                                await connection.scalar(
                                    select(func.count())
                                    .select_from(BookMember)
                                    .where(BookMember.book_id == book_id)
                                )
                                == 3
                            )
                            version = (await current())["version"]
                            await request(
                                "POST",
                                f"/books/{book_id}/leave",
                                {"version": version},
                                who=1,
                                expected=204,
                            )
                            await request(
                                "POST",
                                f"/books/{book_id}/leave",
                                {"version": version},
                                who=1,
                                expected=204,
                            )
                            await preview(fourth["token"], 410)
                            transfer = await request(
                                "POST",
                                f"/books/{book_id}/transfer",
                                {"version": (await current())["version"], "userId": str(users[2])},
                            )
                            assert transfer["ownerId"] == str(users[2])
                            await request(
                                "POST",
                                f"/books/{book_id}/invitations",
                                {"id": str(uuid4())},
                                expected=403,
                            )
                            await request(
                                "POST",
                                f"/books/{book_id}/leave",
                                {"version": transfer["version"]},
                                expected=204,
                            )
                            await request("GET", f"/books/{book_id}", expected=404)
                            final_book = await request("GET", f"/books/{book_id}", who=2)
                            await request(
                                "POST",
                                f"/books/{book_id}/close",
                                {"version": final_book["version"]},
                                who=2,
                                expected=204,
                            )
                            await request(
                                "POST",
                                f"/books/{book_id}/close",
                                {"version": final_book["version"]},
                                who=2,
                                expected=204,
                            )
                            await request("GET", f"/books/{book_id}", who=2, expected=404)
                            assert (
                                await connection.scalar(
                                    select(Book.status).where(Book.id == book_id)
                                )
                                == "closed"
                            )
                            assert (
                                await connection.scalar(
                                    select(func.count())
                                    .select_from(LedgerEntry)
                                    .where(LedgerEntry.book_id == book_id)
                                )
                                == 2
                            )
                    finally:
                        await outer.rollback()
            finally:
                keys = [key async for key in sessions.redis.scan_iter(match=sessions.prefix + "*")]
                keys += [
                    preview_key,
                    settings.redis_key_prefix
                    + "assistant:chat-lock:"
                    + locals().get("conversation", ""),
                    f"{settings.redis_key_prefix}assistant:chat-rate:{users[1]}",
                ]
                await sessions.redis.delete(*keys)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count()).select_from(Book).where(Book.id == book_id)
                )
                == 0
            )
    finally:
        await engine.dispose()
