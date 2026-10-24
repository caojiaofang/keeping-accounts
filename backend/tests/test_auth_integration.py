"""显式启用的真实存储测试；微信响应模拟，用户事务回滚，Redis key 单独清理。"""

import asyncio
import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.auth_deps import get_session_store
from app.api.deps import get_db
from app.core.config import Settings
from app.integrations.wechat import WeChatClient
from app.main import create_app
from app.models.user import User
from app.services.sessions import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_login_persistence_session_and_logout_with_real_services():
    asyncio.run(run_roundtrip())


async def run_roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("集成测试只允许配置完整的开发环境")
    if settings.wechat_app_secret is None:
        raise RuntimeError("请先配置微信凭据，测试使用模拟响应，不访问微信服务")
    app = create_app(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    openid = f"integration-{uuid4().hex}"
    client_ip = "198.51.100.77"

    try:
        async with app.router.lifespan_context(app):
            store = SessionStore(app.state.redis, settings)
            store.prefix += f"integration:{uuid4().hex}:"
            rate_key = store.prefix + "login-rate:" + hashlib.sha256(client_ip.encode()).hexdigest()
            calls = []

            def wechat_response(request):
                calls.append(request)
                if request.url.params["js_code"] == "invalid-code":
                    return httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
                return httpx.Response(200, json={"openid": openid, "session_key": "test-only-key"})

            try:
                async with engine.connect() as connection:
                    transaction = await connection.begin()
                    try:

                        async def session_override():
                            async with AsyncSession(
                                bind=connection,
                                expire_on_commit=False,
                                join_transaction_mode="create_savepoint",
                            ) as session:
                                yield session

                        app.dependency_overrides[get_db] = session_override
                        app.dependency_overrides[get_session_store] = lambda: store
                        async with httpx.AsyncClient(
                            transport=httpx.MockTransport(wechat_response)
                        ) as mock_wechat:
                            app.state.wechat = WeChatClient(settings, mock_wechat)
                            transport = httpx.ASGITransport(app=app, client=(client_ip, 12345))
                            async with httpx.AsyncClient(
                                transport=transport, base_url="http://integration"
                            ) as client:
                                first = await client.post(
                                    "/api/v1/auth/wechat/login", json={"code": "first-code"}
                                )
                                assert first.status_code == 200
                                first = first.json()
                                second = await client.post(
                                    "/api/v1/auth/wechat/login", json={"code": "second-code"}
                                )
                                assert second.status_code == 200
                                second = second.json()
                                assert first["is_new_user"] is True
                                assert second["is_new_user"] is False
                                assert first["user"]["id"] == second["user"]["id"]
                                assert first["user"]["openid"] == openid
                                assert first["access_token"] != second["access_token"]
                                assert (
                                    0
                                    < await store.redis.ttl(
                                        store.session_key(first["access_token"])
                                    )
                                    <= settings.auth_session_ttl_seconds
                                )

                                first_header = {"Authorization": "Bearer " + first["access_token"]}
                                second_header = {
                                    "Authorization": "Bearer " + second["access_token"]
                                }
                                me = await client.get("/api/v1/auth/me", headers=first_header)
                                assert me.status_code == 200
                                assert me.json()["openid"] == openid
                                assert (
                                    await client.post("/api/v1/auth/logout", headers=first_header)
                                ).status_code == 204
                                assert (
                                    await client.get("/api/v1/auth/me", headers=first_header)
                                ).status_code == 401
                                assert (
                                    await client.get("/api/v1/auth/me", headers=second_header)
                                ).status_code == 200

                                bad_code = await client.post(
                                    "/api/v1/auth/wechat/login", json={"code": "invalid-code"}
                                )
                                assert bad_code.status_code == 401
                                assert 0 < await store.redis.ttl(rate_key) <= 60
                                store.settings = settings.model_copy(
                                    update={"auth_login_rate_limit": 3}
                                )
                                limited = await client.post(
                                    "/api/v1/auth/wechat/login", json={"code": "rate-limited-code"}
                                )
                                assert limited.status_code == 429
                                assert len(calls) == 3
                                assert (
                                    await client.post("/api/v1/auth/logout", headers=second_header)
                                ).status_code == 204

                        count = await connection.scalar(
                            select(func.count()).select_from(User).where(User.openid == openid)
                        )
                        assert count == 1
                    finally:
                        await transaction.rollback()
            finally:
                keys = [key async for key in store.redis.scan_iter(match=store.prefix + "*")]
                if keys:
                    await store.redis.delete(*keys)

        async with engine.connect() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(User).where(User.openid == openid)
            )
            assert count == 0
    finally:
        await engine.dispose()
