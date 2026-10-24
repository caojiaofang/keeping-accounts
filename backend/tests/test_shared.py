"""邀请签名与公开预览契约的离线检查；权限和事务由开发库集成测试覆盖。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api.deps import get_db, get_redis
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.schemas.shared import AcceptInvitation, InvitationToken, TransferBook
from app.services.shared import SharedBookService


def test_signed_invitation_is_stable_secret_and_environment_scoped():
    identifier = uuid4()
    settings = Settings(_env_file=None, book_invite_secret=SecretStr("x" * 48))
    service = SharedBookService(None, settings)
    token = service.token(identifier)
    assert token == service.token(identifier)
    assert len(token) == 100 and token.startswith("s1_")
    assert "x" * 48 not in token
    assert token != SharedBookService(
        None, settings.model_copy(update={"app_env": "production"})
    ).token(identifier)
    assert token != SharedBookService(
        None, settings.model_copy(update={"book_invite_secret": SecretStr("y" * 48)})
    ).token(identifier)
    with pytest.raises(ServiceError):
        SharedBookService(None, Settings(_env_file=None)).token(identifier)


def test_invitation_input_hides_token_and_refuses_actor_override():
    body = InvitationToken(token="private-invite")
    assert "private-invite" not in repr(body)
    with pytest.raises(ValidationError):
        AcceptInvitation(token="private-invite", name=" ")
    with pytest.raises(ValidationError):
        AcceptInvitation(token="private-invite", name="小林", userId=str(uuid4()))
    with pytest.raises(ValidationError):
        TransferBook(user_id=uuid4(), version=1.5)


def test_public_preview_rate_limit_and_no_token_echo():
    settings = Settings(_env_file=None, database_url=None, redis_url=None)
    app = create_app(settings)
    redis = SimpleNamespace(eval=AsyncMock(return_value=61))
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        response = client.post("/api/v1/book-invitations/preview", json={"token": "private-invite"})
        assert response.status_code == 429 and "private-invite" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        invalid = client.post(
            "/api/v1/book-invitations/preview", json={"token": "private-invite", "userId": "forged"}
        )
        assert invalid.status_code == 422 and "private-invite" not in invalid.text
