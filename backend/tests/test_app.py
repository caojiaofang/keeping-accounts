"""框架冒烟检查，隔离本地环境配置，不连接外部服务。"""

from unittest.mock import AsyncMock

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_db, get_redis
from app.core.config import Settings
from app.main import create_app


def test_app_starts_without_external_services():
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        redis_url=None,
        cors_origins=[],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) == {
            "/api/v1/health",
            "/api/v1/auth/wechat/login",
            "/api/v1/auth/me",
            "/api/v1/auth/logout",
            "/api/v1/ledger",
            "/api/v1/books",
            "/api/v1/books/{book_id}/budget",
            "/api/v1/entries",
            "/api/v1/entries/{entry_id}",
            "/api/v1/assistant/parse",
            "/api/v1/assistant/conversations",
            "/api/v1/assistant/conversations/{conversation_id}",
            "/api/v1/assistant/conversations/{conversation_id}/messages",
            "/api/v1/assistant/conversations/{conversation_id}/messages/stream",
            "/api/v1/books/{book_id}",
            "/api/v1/books/{book_id}/members",
            "/api/v1/books/{book_id}/invitations",
            "/api/v1/books/{book_id}/invitations/{invitation_id}",
            "/api/v1/books/{book_id}/members/{user_id}",
            "/api/v1/books/{book_id}/leave",
            "/api/v1/books/{book_id}/transfer",
            "/api/v1/books/{book_id}/close",
            "/api/v1/books/{book_id}/activities",
            "/api/v1/book-invitations/preview",
            "/api/v1/book-invitations/accept",
            "/prod-api/auth/login",
            "/prod-api/auth/me",
            "/prod-api/auth/logout",
            "/prod-api/overview",
            "/prod-api/users",
            "/prod-api/users/{user_id}",
            "/prod-api/books",
            "/prod-api/books/{book_id}",
        }
        assert app.state.session_factory is None
        assert app.state.redis is None


def test_configured_clients_are_lazy_and_released():
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@127.0.0.1:1/keeping_accounts_dev",
        redis_url="redis://127.0.0.1:1/0",
        cors_origins=[],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert app.state.session_factory is not None
        assert app.state.redis is not None
    assert app.state.session_factory is None
    assert app.state.redis is None


def test_unconfigured_dependencies_return_503():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))

    @app.get("/_test/db", dependencies=[Depends(get_db)])
    async def database_probe():
        return {}

    @app.get("/_test/redis", dependencies=[Depends(get_redis)])
    async def redis_probe():
        return {}

    with TestClient(app) as client:
        assert client.get("/_test/db").status_code == 503
        assert client.get("/_test/redis").status_code == 503


def test_failed_startup_probe_releases_resources(monkeypatch):
    probe = AsyncMock(side_effect=RuntimeError("连接检查失败"))
    dispose = AsyncMock()
    close_redis = AsyncMock()
    monkeypatch.setattr("app.core.lifespan.check_connections", probe)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(Redis, "aclose", close_redis)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@127.0.0.1:1/keeping_accounts_dev",
        redis_url="redis://127.0.0.1:1/1",
        check_connections_on_startup=True,
    )
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="连接检查失败"), TestClient(app):
        pytest.fail("连接检查失败时不应进入应用运行阶段")

    probe.assert_awaited_once_with(app)
    dispose.assert_awaited_once()
    close_redis.assert_awaited_once()
    assert app.state.session_factory is None
    assert app.state.redis is None
