from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.auth_deps import get_access_token, get_auth_service, get_session_store
from app.schemas.auth import AuthErrorResponse, LoginResponse, UserResponse, WeChatLoginRequest
from app.services.auth import AuthService
from app.services.sessions import SessionStore

router = APIRouter(
    prefix="/auth",
    tags=["微信登录"],
    responses={code: {"model": AuthErrorResponse} for code in (401, 403, 422, 429, 502, 503, 504)},
)


@router.post("/wechat/login", response_model=LoginResponse, summary="使用 wx.login code 登录")
async def wechat_login(
    body: WeChatLoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> LoginResponse:
    response.headers["Cache-Control"] = "no-store"
    client_ip = request.client.host if request.client else "unknown"
    return await service.login(body.code.get_secret_value(), client_ip)


@router.get("/me", response_model=UserResponse, summary="获取当前登录用户")
async def current_user(
    response: Response,
    token: Annotated[str, Depends(get_access_token)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.current_user(token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="注销当前登录凭证")
async def logout(
    response: Response,
    token: Annotated[str, Depends(get_access_token)],
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> None:
    response.headers["Cache-Control"] = "no-store"
    await sessions.revoke(token)
