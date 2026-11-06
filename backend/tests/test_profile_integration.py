"""在本机临时 PostgreSQL 中验证昵称迁移、真实提交、重新登录和账号隔离。"""

import asyncio
import os
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from alembic import command
from app.api.auth_deps import get_session_store, get_wechat_client
from app.api.deps import get_db
from app.core.config import BACKEND_DIR, Settings
from app.main import create_app
from app.services.admin import AdminService
from app.services.sessions import unauthorized

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用本机昵称集成测试"
    ),
]


def test_nickname_migration_and_account_persistence():
    asyncio.run(roundtrip())


async def roundtrip():
    value = os.getenv("MOJI_PROFILE_TEST_URL")
    if not value:
        pytest.skip("未提供本机昵称测试库")
    url = make_url(value)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.database != "moji_profile_test"
    ):
        raise RuntimeError("昵称验证仅允许本机 moji_profile_test，不使用开发或正式库")
    schema = "profile_test_" + uuid4().hex
    engine = create_async_engine(value, connect_args={"timeout": 10, "command_timeout": 30})
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                config = Config(str(BACKEND_DIR / "alembic.ini"), stdout=StringIO())
                config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))

                def migrate(sync_connection, target, *, downgrade=False):
                    config.attributes["connection"] = sync_connection
                    action = command.downgrade if downgrade else command.upgrade
                    action(config, target)

                await connection.run_sync(migrate, "0006_budget")
                settings = Settings(
                    _env_file=None, app_env="test", database_url=None, redis_url=None
                )
                users = [uuid4() for _ in range(3)]
                for index, user_id in enumerate(users):
                    await connection.execute(
                        text(
                            "INSERT INTO users (id, wechat_app_id, openid) "
                            "VALUES (:id, :app_id, :openid)"
                        ),
                        {
                            "id": user_id,
                            "app_id": settings.wechat_app_id if index < 2 else "other-app",
                            "openid": str(user_id),
                        },
                    )
                await connection.run_sync(migrate, "head")
                assert await connection.scalar(text("SELECT nickname FROM users LIMIT 1")) is None
                tokens = {chr(97 + index) * 43: user_id for index, user_id in enumerate(users)}

                async def resolve(token):
                    if token not in tokens:
                        raise unauthorized()
                    return tokens[token]

                async def issue(user_id):
                    token = "d" * 43
                    tokens[token] = user_id
                    return token

                async def database():
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        yield session

                app = create_app(settings)
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_session_store] = lambda: SimpleNamespace(
                    resolve=resolve, issue=issue, check_login_rate=AsyncMock()
                )
                app.dependency_overrides[get_wechat_client] = lambda: SimpleNamespace(
                    exchange_code=AsyncMock(return_value=SimpleNamespace(openid=str(users[0])))
                )
                header = {"Authorization": "Bearer " + "a" * 43}
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    saved = await client.put(
                        "/api/v1/auth/me",
                        headers=header,
                        json={"nickname": "  一二三四五六七八九十甲乙  "},
                    )
                    assert saved.status_code == 200, saved.text
                    assert saved.json()["nickname"] == "一二三四五六七八九十甲乙"
                    assert saved.headers["Cache-Control"] == "no-store"
                    # 新请求使用新的数据库会话，确认读取的是已提交的昵称。
                    restored = await client.get("/api/v1/auth/me", headers=header)
                    assert restored.json()["nickname"] == saved.json()["nickname"]
                    assert (
                        await connection.scalar(
                            text("SELECT nickname FROM users WHERE id = :id"), {"id": users[0]}
                        )
                        == saved.json()["nickname"]
                    )
                    second = await client.get(
                        "/api/v1/auth/me", headers={"Authorization": "Bearer " + "b" * 43}
                    )
                    assert second.json()["nickname"] is None
                    for token in ("c" * 43, "z" * 43):
                        rejected = await client.put(
                            "/api/v1/auth/me",
                            headers={"Authorization": "Bearer " + token},
                            json={"nickname": "不能越权修改"},
                        )
                        assert rejected.status_code == 401
                    relogin = await client.post(
                        "/api/v1/auth/wechat/login", json={"code": "test-code"}
                    )
                    assert relogin.status_code == 200, relogin.text
                    assert relogin.json()["is_new_user"] is False
                    assert relogin.json()["user"]["nickname"] == saved.json()["nickname"]
                    assert relogin.json()["user"]["id"] == str(users[0])

                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    admin = AdminService(session)
                    detail = await admin.user(users[0])
                    listing = await admin.users(str(users[0]), 1, 20)
                    assert detail.nickname == saved.json()["nickname"]
                    assert listing.items[0].nickname == detail.nickname
                    assert (await admin.user(users[1])).nickname is None

                await connection.run_sync(migrate, "0006_budget", downgrade=True)
                assert await connection.scalar(text("SELECT count(*) FROM users")) == 3
                await connection.run_sync(migrate, "head")
                assert await connection.scalar(text("SELECT count(*) FROM users")) == 3
                assert await connection.scalar(text("SELECT nickname FROM users LIMIT 1")) is None
            finally:
                await transaction.rollback()
            assert not await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"),
                {"schema": schema},
            )
    finally:
        await engine.dispose()
