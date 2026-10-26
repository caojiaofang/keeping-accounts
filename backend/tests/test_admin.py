"""验证管理员边界、密码轮换、Cookie、限流、退出和敏感参数不回显。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.api.admin import get_admin_service
from app.api.admin_deps import ADMIN_COOKIE
from app.api.deps import get_redis
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.services.admin_auth import AdminAuth, password_hasher
from app.services.sessions import SessionStore

PASSWORD = "a-test-admin-password"
PASSWORD_HASH = password_hasher.hash(PASSWORD)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.rate = 0

    async def set(self, key, value, **kwargs):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return self.values.pop(key, None) is not None

    async def eval(self, script, numkeys, key):
        self.rate += 1
        return self.rate


def settings(**kwargs):
    return Settings(_env_file=None, app_env="test", admin_password_hash=PASSWORD_HASH, **kwargs)


@pytest.fixture
def api():
    config = settings()
    redis = MemoryRedis()
    service = SimpleNamespace(users=AsyncMock(), books=AsyncMock(), overview=AsyncMock())
    app = create_app(config)
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_admin_service] = lambda: service
    with TestClient(app) as client:
        yield client, redis, service, config


def login(client, **kwargs):
    return client.post(
        "/prod-api/auth/login", json={"username": "admin", "password": PASSWORD}, **kwargs
    )


def test_login_cookie_restore_and_logout_revoke(api):
    client, redis, _, _ = api
    result = login(client)
    assert result.status_code == 200
    assert result.json() == {"username": "admin"}
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/prod-api" in cookie and "Max-Age=28800" in cookie
    token = client.cookies.get(ADMIN_COOKIE)
    assert token not in repr(redis.values)
    assert client.get("/prod-api/auth/me").json() == {"username": "admin"}
    assert client.post("/prod-api/auth/logout").status_code == 204
    assert client.get("/prod-api/auth/me").status_code == 401
    assert redis.values == {}
    client.cookies.set(ADMIN_COOKIE, token)
    assert client.get("/prod-api/auth/me").status_code == 401


@pytest.mark.parametrize(
    "path", ["/overview", "/users", "/books", "/users/" + str(uuid4()), "/books/" + str(uuid4())]
)
def test_anonymous_and_wechat_bearer_cannot_access_admin(api, path):
    client, redis, service, config = api
    token = asyncio.run(SessionStore(redis, config).issue(uuid4()))
    response = client.get("/prod-api" + path, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    service.users.assert_not_awaited()
    client.cookies.set(ADMIN_COOKIE, token)
    assert client.get("/prod-api" + path).status_code == 401


def test_wrong_credentials_rate_limit_and_no_session(api):
    client, redis, _, _ = api
    for _ in range(5):
        result = client.post(
            "/prod-api/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert result.status_code == 401
        assert ADMIN_COOKIE not in client.cookies
    response = login(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert not redis.values


def test_wrong_username_does_not_issue_session(api):
    client, redis, _, _ = api
    result = client.post("/prod-api/auth/login", json={"username": "other", "password": PASSWORD})
    assert result.status_code == 401 and not redis.values


@pytest.mark.parametrize(
    "body",
    [
        {"username": "admin", "password": PASSWORD, "role": "admin"},
        {"username": "admin", "password": "sensitive" * 100},
        {},
    ],
)
def test_bad_input_does_not_echo_password(api, body):
    result = api[0].post("/prod-api/auth/login", json=body)
    assert result.status_code == 422
    assert "sensitive" not in result.text and PASSWORD not in result.text


def test_cross_site_login_and_logout_are_rejected(api):
    client, _, _, config = api
    assert login(client, headers={"Origin": "https://evil.example"}).status_code == 403
    assert login(client, headers={"Origin": "null"}).status_code == 403
    assert login(client, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert login(client, headers={"Origin": config.admin_origin}).status_code == 200
    assert (
        client.post("/prod-api/auth/logout", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/prod-api/auth/me").status_code == 200


def test_production_cookie_is_secure():
    config = Settings(
        _env_file=None,
        app_env="production",
        redis_key_prefix="moji:prod:",
        admin_password_hash=PASSWORD_HASH,
        admin_origin="https://mjzs.zf-yun.cn",
    )
    app = create_app(config)
    redis = MemoryRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app, base_url="https://mjzs.zf-yun.cn") as client:
        result = login(client)
        assert "Secure" in result.headers["set-cookie"]
        assert client.get("/prod-api/auth/me").status_code == 200
        assert "Secure" in client.post("/prod-api/auth/logout").headers["set-cookie"]


def test_password_rotation_and_expiry_invalidate_existing_sessions():
    redis = MemoryRedis()
    auth = AdminAuth(redis, settings())
    token = asyncio.run(auth.login("admin", PASSWORD, "127.0.0.1"))
    changed = AdminAuth(
        redis,
        settings().model_copy(
            update={
                "admin_password_hash": Settings(
                    _env_file=None, admin_password_hash=password_hasher.hash("a-new-password")
                ).admin_password_hash,
            }
        ),
    )
    with pytest.raises(ServiceError) as error:
        asyncio.run(changed.authenticate(token))
    assert error.value.status_code == 401
    redis.values.clear()
    with pytest.raises(ServiceError) as error:
        asyncio.run(auth.authenticate(token))
    assert error.value.status_code == 401


@pytest.mark.parametrize("hash_value", [None, "not-a-valid-hash"])
def test_unconfigured_admin_fails_closed(hash_value):
    config = Settings(_env_file=None, admin_password_hash=hash_value)
    auth = AdminAuth(MemoryRedis(), config)
    with pytest.raises(ServiceError) as error:
        asyncio.run(auth.login("admin", PASSWORD, "127.0.0.1"))
    assert error.value.status_code == 503


def test_redis_failure_fails_closed():
    redis = SimpleNamespace(eval=AsyncMock(side_effect=RedisError("private-redis-password")))
    with pytest.raises(ServiceError) as error:
        asyncio.run(AdminAuth(redis, settings()).login("admin", PASSWORD, "127.0.0.1"))
    assert error.value.status_code == 503
    assert "private-redis-password" not in str(error.value)


def test_filters_validation_and_business_mutations_are_unavailable(api):
    client, _, service, _ = api
    assert login(client).status_code == 200
    for path in (
        "/users?page=0",
        "/users?page_size=101",
        "/books?kind=invalid",
        "/books?status=deleted",
        "/books?owner_id=not-a-uuid",
    ):
        assert client.get("/prod-api" + path).status_code == 422
    service.books.assert_not_awaited()
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/prod-api/books").status_code == 405
        assert getattr(client, method)("/prod-api/users").status_code == 405
