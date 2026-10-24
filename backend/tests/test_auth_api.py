from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.auth_deps import get_auth_service, get_session_store
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.schemas.auth import LoginResponse, UserResponse


@pytest.fixture
def api():
    user = UserResponse(
        id=uuid4(),
        openid="trusted-openid",
        created_at=datetime.now(UTC),
        last_login_at=datetime.now(UTC),
    )
    service = SimpleNamespace(
        login=AsyncMock(
            return_value=LoginResponse(
                access_token="a" * 43, expires_in=604800, is_new_user=True, user=user
            )
        ),
        current_user=AsyncMock(return_value=user),
    )
    sessions = SimpleNamespace(revoke=AsyncMock())
    app = create_app(Settings(_env_file=None, app_env="test", database_url=None, redis_url=None))
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_session_store] = lambda: sessions
    with TestClient(app) as client:
        yield client, service, sessions


def test_code_login_contract_and_private_fields(api):
    client, service, _ = api
    response = client.post("/api/v1/auth/wechat/login", json={"code": " temporary-code "})
    assert response.status_code == 200
    service.login.assert_awaited_once_with("temporary-code", "testclient")
    payload = response.json()
    assert payload["user"]["openid"] == "trusted-openid"
    assert payload["token_type"] == "bearer"
    assert response.headers["Cache-Control"] == "no-store"
    assert not {"session_key", "secret", "code"}.intersection(payload)


@pytest.mark.parametrize(
    "body", [{}, {"code": " "}, {"code": "a" * 257}, {"code": "valid", "openid": "forged"}]
)
def test_invalid_payloads_and_client_supplied_openid_are_rejected(api, body):
    client, service, _ = api
    assert client.post("/api/v1/auth/wechat/login", json=body).status_code == 422
    service.login.assert_not_awaited()


def test_current_user_requires_bearer_token(api):
    client, service, _ = api
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    service.current_user.assert_not_awaited()
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + "a" * 43})
    assert response.status_code == 200
    assert response.json()["openid"] == "trusted-openid"


def test_logout_revokes_current_token(api):
    client, _, sessions = api
    response = client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer " + "a" * 43})
    assert response.status_code == 204
    assert response.content == b""
    sessions.revoke.assert_awaited_once_with("a" * 43)


def test_login_errors_are_returned_without_internal_details(api):
    client, service, _ = api
    service.login.side_effect = ServiceError(429, "登录请求过于频繁", {"Retry-After": "60"})
    response = client.post("/api/v1/auth/wechat/login", json={"code": "temporary-code"})
    assert response.status_code == 429
    assert response.json() == {"detail": "登录请求过于频繁"}
    assert response.headers["Retry-After"] == "60"
    assert response.headers["Cache-Control"] == "no-store"


def test_validation_error_does_not_echo_sensitive_inputs(api):
    client, service, _ = api
    response = client.post(
        "/api/v1/auth/wechat/login",
        json={"code": "private-temporary-code", "secret": "mistakenly-supplied-secret"},
    )
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert "private-temporary-code" not in response.text
    assert "mistakenly-supplied-secret" not in response.text
    service.login.assert_not_awaited()
