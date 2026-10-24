from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.wechat import WeChatClient
from app.repositories.users import find_user, upsert_wechat_user
from app.schemas.auth import LoginResponse, UserResponse
from app.services.sessions import SessionStore, unauthorized


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        sessions: SessionStore,
        wechat: WeChatClient,
        settings: Settings,
    ):
        self.session = session
        self.sessions = sessions
        self.wechat = wechat
        self.settings = settings

    async def login(self, code: str, client_ip: str) -> LoginResponse:
        await self.sessions.check_login_rate(client_ip)
        identity = await self.wechat.exchange_code(code)
        try:
            async with self.session.begin():
                user, is_new_user = await upsert_wechat_user(
                    self.session, self.settings.wechat_app_id, identity.openid
                )
        except SQLAlchemyError:
            raise ServiceError(503, "用户信息保存失败，请重新获取 code 后重试") from None
        token = await self.sessions.issue(user.id)
        return LoginResponse(
            access_token=token,
            expires_in=self.settings.auth_session_ttl_seconds,
            is_new_user=is_new_user,
            user=UserResponse.model_validate(user),
        )

    async def current_user(self, token: str) -> UserResponse:
        user_id = await self.sessions.resolve(token)
        try:
            user = await find_user(self.session, user_id, self.settings.wechat_app_id)
        except SQLAlchemyError:
            raise ServiceError(503, "用户信息暂时不可用，请稍后重试") from None
        if user is None:
            raise unauthorized()
        return UserResponse.model_validate(user)
