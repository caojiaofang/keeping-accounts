import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.services.sessions import SessionStore


def fake_redis():
    return SimpleNamespace(
        set=AsyncMock(return_value=True),
        get=AsyncMock(return_value=None),
        delete=AsyncMock(return_value=1),
        eval=AsyncMock(return_value=1),
    )


def test_token_is_opaque_hashed_namespaced_and_has_ttl():
    redis = fake_redis()
    settings = Settings(_env_file=None, app_env="test")
    store = SessionStore(redis, settings)
    user_id = uuid4()
    token = asyncio.run(store.issue(user_id))

    digest = hashlib.sha256(token.encode()).hexdigest()
    key = f"moji:dev:auth:{settings.wechat_app_id}:session:{digest}"
    assert len(token) == 43
    assert token not in key
    redis.set.assert_awaited_once_with(
        key, str(user_id), ex=settings.auth_session_ttl_seconds, nx=True
    )
    redis.get.return_value = str(user_id)
    assert asyncio.run(store.resolve(token)) == user_id
    asyncio.run(store.revoke(token))
    redis.delete.assert_awaited_once_with(key)


@pytest.mark.parametrize("value", [None, "invalid-user-id"])
def test_expired_or_corrupt_sessions_are_rejected(value):
    redis = fake_redis()
    redis.get.return_value = value
    with pytest.raises(ServiceError) as error:
        asyncio.run(SessionStore(redis, Settings(_env_file=None)).resolve("a" * 43))
    assert error.value.status_code == 401
    assert error.value.headers["WWW-Authenticate"] == "Bearer"


def test_malformed_token_is_rejected_before_accessing_redis():
    redis = fake_redis()
    with pytest.raises(ServiceError) as error:
        asyncio.run(SessionStore(redis, Settings(_env_file=None)).resolve("forged-openid"))
    assert error.value.status_code == 401
    redis.get.assert_not_awaited()


def test_login_rate_limit_is_enforced():
    redis = fake_redis()
    redis.eval.return_value = 31
    with pytest.raises(ServiceError) as error:
        asyncio.run(SessionStore(redis, Settings(_env_file=None)).check_login_rate("198.51.100.77"))
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "60"


@pytest.mark.parametrize("operation", ["issue", "resolve", "revoke", "check_login_rate"])
def test_redis_failure_does_not_authenticate_or_expose_errors(operation):
    redis = fake_redis()
    method = {"issue": "set", "resolve": "get", "revoke": "delete", "check_login_rate": "eval"}
    getattr(redis, method[operation]).side_effect = RedisError("private-redis-details")
    store = SessionStore(redis, Settings(_env_file=None))
    argument = uuid4() if operation == "issue" else "a" * 43
    with pytest.raises(ServiceError) as error:
        asyncio.run(getattr(store, operation)(argument))
    assert error.value.status_code == 503
    assert "private-redis-details" not in str(error.value)
