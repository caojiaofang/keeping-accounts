"""验证管理员边界、密码轮换、Cookie、限流、退出和敏感参数不回显。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.api.admin import get_admin_service
from app.api.admin_deps import ADMIN_COOKIE, get_admin_auth
from app.api.deps import get_redis
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.models.admin import AdminAccount
from app.repositories.admin import AuditContext
from app.services.admin_auth import AdminAuth, password_hasher
from app.services.sessions import SessionStore

PASSWORD = "a-test-admin-password"
PASSWORD_HASH = password_hasher.hash(PASSWORD)


class MemoryRepository:
    def __init__(self, present=True):
        self.account = (
            AdminAccount(
                id=uuid4(),
                username="admin",
                password_hash=PASSWORD_HASH,
                is_active=True,
                session_version=1,
                created_at=datetime.now(UTC),
                last_login_at=None,
                password_changed_at=None,
            )
            if present
            else None
        )
        self.events = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def by_username(self, username):
        return self.account if self.account and self.account.username == username else None

    async def by_id(self, admin_id, *, lock=False):
        return self.account if self.account and self.account.id == admin_id else None

    def add_event(self, username, action, outcome, context, *, admin_id=None, reason=""):
        self.events.append(
            dict(
                id=uuid4(),
                username=username,
                action=action,
                outcome=outcome,
                reason=reason,
                ip=context.ip,
                user_agent=context.user_agent[:256],
                created_at=datetime.now(UTC),
            )
        )

    async def logs(self, action, outcome, page, page_size):
        rows = [
            e
            for e in reversed(self.events)
            if (not action or e["action"] == action) and (not outcome or e["outcome"] == outcome)
        ]
        return rows[(page - 1) * page_size : page * page_size], len(rows)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.rate = {}

    async def set(self, key, value, **kwargs):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return self.values.pop(key, None) is not None

    async def eval(self, script, numkeys, key):
        self.rate[key] = self.rate.get(key, 0) + 1
        return self.rate[key]


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
    repository = MemoryRepository()
    app.state.test_repository = repository
    app.dependency_overrides[get_admin_auth] = lambda: AdminAuth(redis, config, repository)
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
    repository = MemoryRepository()
    app.dependency_overrides[get_admin_auth] = lambda: AdminAuth(redis, config, repository)
    with TestClient(app, base_url="https://mjzs.zf-yun.cn") as client:
        result = login(client)
        assert "Secure" in result.headers["set-cookie"]
        assert client.get("/prod-api/auth/me").status_code == 200
        assert "Secure" in client.post("/prod-api/auth/logout").headers["set-cookie"]


def test_password_rotation_and_expiry_invalidate_existing_sessions():
    redis = MemoryRedis()
    repository = MemoryRepository()
    auth = AdminAuth(redis, settings(), repository)
    context = AuditContext("127.0.0.1")
    token, account = asyncio.run(auth.login("admin", PASSWORD, context))
    asyncio.run(auth.change_password(account, PASSWORD, "a-new-password", context))
    with pytest.raises(ServiceError) as error:
        asyncio.run(auth.authenticate(token))
    assert error.value.status_code == 401
    assert repository.account.session_version == 2
    assert password_hasher.verify("a-new-password", repository.account.password_hash)
    redis.values.clear()
    with pytest.raises(ServiceError) as error:
        asyncio.run(auth.authenticate(token))
    assert error.value.status_code == 401


@pytest.mark.parametrize("hash_value", [None, "not-a-valid-hash"])
def test_unconfigured_admin_fails_closed(hash_value):
    config = Settings(_env_file=None, admin_password_hash=hash_value)
    auth = AdminAuth(MemoryRedis(), config, MemoryRepository(present=False))
    with pytest.raises(ServiceError) as error:
        asyncio.run(auth.login("admin", PASSWORD, AuditContext("127.0.0.1")))
    assert error.value.status_code == 401


def test_redis_failure_fails_closed():
    redis = SimpleNamespace(eval=AsyncMock(side_effect=RedisError("private-redis-password")))
    with pytest.raises(ServiceError) as error:
        asyncio.run(
            AdminAuth(redis, settings(), MemoryRepository()).login(
                "admin", PASSWORD, AuditContext("127.0.0.1")
            )
        )
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


def test_account_password_change_invalidates_old_password_and_all_tokens(api):
    client, _, _, _ = api
    assert login(client).status_code == 200
    first = client.cookies.get(ADMIN_COOKIE)
    assert login(client).status_code == 200
    second = client.cookies.get(ADMIN_COOKIE)
    info = client.get("/prod-api/account").json()
    assert info["username"] == "admin" and "password_hash" not in info
    new = "a-strong-new-password"
    response = client.post(
        "/prod-api/account/password", json={"current_password": "wrong", "new_password": new}
    )
    assert response.status_code == 400
    assert client.get("/prod-api/auth/me").status_code == 200
    assert (
        client.post(
            "/prod-api/account/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/prod-api/account/password",
            json={"current_password": PASSWORD, "new_password": "short"},
        ).status_code
        == 422
    )
    response = client.post(
        "/prod-api/account/password", json={"current_password": PASSWORD, "new_password": new}
    )
    assert response.status_code == 204 and "Max-Age=0" in response.headers["set-cookie"]
    for token in (first, second):
        assert (
            client.get(
                "/prod-api/auth/me", headers={"Cookie": f"{ADMIN_COOKIE}={token}"}
            ).status_code
            == 401
        )
    assert login(client).status_code == 401
    assert (
        client.post("/prod-api/auth/login", json={"username": "admin", "password": new}).status_code
        == 200
    )
    logs = client.get("/prod-api/account/logs?action=password_change").json()
    assert logs["total"] == 3
    assert {e["outcome"] for e in logs["items"]} == {"success", "failure"}
    assert new not in str(logs) and PASSWORD not in str(logs)
    assert client.get("/prod-api/account/logs?outcome=failure&page_size=1").json()["page_size"] == 1


def test_logout_all_revokes_two_sessions_and_writes_log(api):
    client, _, _, _ = api
    assert login(client).status_code == 200
    first = client.cookies.get(ADMIN_COOKIE)
    assert login(client).status_code == 200
    second = client.cookies.get(ADMIN_COOKIE)
    assert client.post("/prod-api/account/logout-all").status_code == 204
    for token in (first, second):
        assert (
            client.get(
                "/prod-api/auth/me", headers={"Cookie": f"{ADMIN_COOKIE}={token}"}
            ).status_code
            == 401
        )
    assert login(client).status_code == 200
    assert client.get("/prod-api/account/logs?action=logout_all").json()["total"] == 1


def test_new_endpoints_require_auth_and_trusted_origin(api):
    client, _, _, _ = api
    for path in ("/account", "/account/logs"):
        assert client.get("/prod-api" + path).status_code == 401
    assert client.post("/prod-api/account/logout-all").status_code == 401
    assert login(client).status_code == 200
    for path in ("/account/password", "/account/logout-all"):
        assert (
            client.post(
                "/prod-api" + path, headers={"Origin": "https://evil.example"}, json={}
            ).status_code
            == 403
        )
    assert client.get("/prod-api/account/logs?action=unknown").status_code == 422
    assert client.get("/prod-api/account/logs?page_size=101").status_code == 422


def test_login_failure_and_throttling_are_audited(api):
    client, _, _, _ = api
    for _ in range(5):
        client.post("/prod-api/auth/login", json={"username": "admin", "password": "wrong"})
    assert login(client).status_code == 429
    events = client.app.state.test_repository.events
    assert len(events) == 6 and events[-1]["reason"] == "rate_limited"


def test_database_commit_failure_never_returns_a_valid_login(api):
    client, redis, _, _ = api
    repository = client.app.state.test_repository
    repository.commit.side_effect = RuntimeError("private-database-connection")
    response = login(client)
    assert response.status_code == 503 and "private-database" not in response.text
    assert ADMIN_COOKIE not in client.cookies and not redis.values


def test_legacy_env_credentials_cannot_override_database_account():
    repository = MemoryRepository()
    config = settings().model_copy(update={"admin_username": "other", "admin_password_hash": None})
    auth = AdminAuth(MemoryRedis(), config, repository)
    token, account = asyncio.run(auth.login("admin", PASSWORD, AuditContext("127.0.0.1")))
    assert account.username == "admin" and token
