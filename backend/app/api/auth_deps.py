from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, get_settings
from app.core.config import Settings
from app.integrations.wechat import WeChatClient
from app.services.auth import AuthService
from app.services.sessions import SessionStore, unauthorized

bearer = HTTPBearer(auto_error=False)


def get_access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None:
        raise unauthorized()
    return credentials.credentials


def get_wechat_client(request: Request) -> WeChatClient:
    return request.app.state.wechat


def get_session_store(
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionStore:
    return SessionStore(redis, settings)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[SessionStore, Depends(get_session_store)],
    wechat: Annotated[WeChatClient, Depends(get_wechat_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(session, sessions, wechat, settings)
