"""真实开发库与 Redis 验证账号迁移、改密、日志和跨会话撤销。"""

import asyncio
import os
from typing import Annotated
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.admin_deps import get_admin_auth
from app.api.deps import get_db
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.models.admin import AdminAccount, AdminAuditEvent
from app.repositories.admin import AdminRepository, AuditContext
from app.services.admin_auth import AdminAuth, password_hasher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_database_admin_account_roundtrip():
    asyncio.run(roundtrip())


async def roundtrip():
    config = Settings()
    if (
        config.app_env not in {"test", "development"}
        or not config.database_url
        or not config.redis_url
    ):
        raise RuntimeError("只允许使用开发数据库和开发 Redis")
    config = config.model_copy(update={"admin_login_rate_limit": 30})
    engine = create_async_engine(config.database_url.get_secret_value())
    redis = Redis.from_url(config.redis_url.get_secret_value(), decode_responses=True)
    prefix = f"{config.redis_key_prefix}admin:v2:test:{uuid4().hex}:"
    username = "test-" + uuid4().hex
    password = "initial-integration-password"
    new_password = "updated-integration-password"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    repository = AdminRepository(session)
                    assert await repository.bootstrap(username, password_hasher.hash(password))
                    assert not await repository.bootstrap(
                        username, password_hasher.hash("different-password")
                    )
                    account = await repository.by_username(username)
                    assert password_hasher.verify(password, account.password_hash)

                async def database():
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        yield session

                def auth(session: Annotated[AsyncSession, Depends(get_db)]):
                    value = AdminAuth(redis, config, AdminRepository(session))
                    value.sessions.prefix = prefix
                    return value

                app = create_app(config)
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_admin_auth] = auth
                transport = httpx.ASGITransport(app=app)
                async with (
                    httpx.AsyncClient(transport=transport, base_url="http://test") as one,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as two,
                ):

                    async def login(client, password):
                        return await client.post(
                            "/prod-api/auth/login",
                            json={"username": username, "password": password},
                        )

                    assert (await login(one, password)).status_code == 200
                    assert (await login(two, password)).status_code == 200
                    assert (await one.get("/prod-api/account")).json()[
                        "password_changed_at"
                    ] is None
                    bad = await one.post(
                        "/prod-api/account/password",
                        json={"current_password": "wrong", "new_password": new_password},
                    )
                    assert bad.status_code == 400
                    assert (await one.get("/prod-api/auth/me")).status_code == 200
                    result = await one.post(
                        "/prod-api/account/password",
                        json={"current_password": password, "new_password": new_password},
                    )
                    assert result.status_code == 204
                    assert (await two.get("/prod-api/auth/me")).status_code == 401
                    assert (await login(one, password)).status_code == 401
                    assert (await login(one, new_password)).status_code == 200
                    assert (await login(two, new_password)).status_code == 200
                    info = (await one.get("/prod-api/account")).json()
                    assert (
                        info["password_changed_at"]
                        and "password_hash" not in info
                        and "session_version" not in info
                    )
                    logs = (await one.get("/prod-api/account/logs?action=password_change")).json()
                    own = [row for row in logs["items"] if row["username"] == username]
                    assert len(own) == 2 and {row["outcome"] for row in own} == {
                        "success",
                        "failure",
                    }
                    assert password not in str(logs) and new_password not in str(logs)
                    assert (await one.post("/prod-api/account/logout-all")).status_code == 204
                    assert (await two.get("/prod-api/auth/me")).status_code == 401
                    assert (await login(one, new_password)).status_code == 200
                    assert (await one.post("/prod-api/auth/logout")).status_code == 204
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        repository = AdminRepository(session)
                        assert not await repository.bootstrap(
                            username, password_hasher.hash(password)
                        )
                        account = await repository.by_username(username)
                        assert password_hasher.verify(new_password, account.password_hash)
                        assert account.session_version == 3
                        rows = (
                            await session.scalars(
                                select(AdminAuditEvent).where(AdminAuditEvent.username == username)
                            )
                        ).all()
                        assert {
                            "bootstrap",
                            "login",
                            "logout",
                            "password_change",
                            "logout_all",
                        } <= {row.action for row in rows}
            finally:
                await transaction.rollback()
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(AdminAccount)
                    .where(AdminAccount.username == username)
                )
                == 0
            )
    finally:
        keys = [key async for key in redis.scan_iter(match=prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


def test_stale_password_change_cannot_overwrite_new_credentials():
    asyncio.run(stale_change())


async def stale_change():
    config = Settings()
    if (
        config.app_env not in {"test", "development"}
        or not config.database_url
        or not config.redis_url
    ):
        raise RuntimeError("只允许开发环境")
    engine = create_async_engine(config.database_url.get_secret_value())
    redis = Redis.from_url(config.redis_url.get_secret_value(), decode_responses=True)
    username = "race-" + uuid4().hex
    account_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as seed:
            repo = AdminRepository(seed)
            await repo.bootstrap(username, password_hasher.hash("initial-test-password"))
            account_id = (await repo.by_username(username)).id
        async with (
            AsyncSession(engine, expire_on_commit=False) as one,
            AsyncSession(engine, expire_on_commit=False) as two,
        ):
            first = AdminAuth(redis, config, AdminRepository(one))
            second = AdminAuth(redis, config, AdminRepository(two))
            current = await first.repository.by_id(account_id)
            stale = await second.repository.by_id(account_id)
            await first.change_password(
                current,
                "initial-test-password",
                "first-new-test-password",
                AuditContext("127.0.0.1"),
            )
            with pytest.raises(ServiceError) as error:
                await second.change_password(
                    stale,
                    "initial-test-password",
                    "stale-new-test-password",
                    AuditContext("127.0.0.1"),
                )
            assert error.value.status_code == 401
            await two.rollback()
        async with AsyncSession(engine) as session:
            result = await session.get(AdminAccount, account_id)
            assert password_hasher.verify("first-new-test-password", result.password_hash)
    finally:
        if account_id:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(AdminAuditEvent).where(AdminAuditEvent.admin_id == account_id)
                )
                await connection.execute(delete(AdminAccount).where(AdminAccount.id == account_id))
        await redis.aclose()
        await engine.dispose()
