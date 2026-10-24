"""Redis 登录态；只保存随机 token 的摘要，按固定 TTL 到期。"""

import hashlib
import re
import secrets
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import ServiceError

LOGIN_RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], 60)
end
return count
"""


def unauthorized() -> ServiceError:
    return ServiceError(401, "登录态无效或已过期，请重新登录", {"WWW-Authenticate": "Bearer"})


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings):
        self.redis = redis
        self.settings = settings
        self.prefix = f"{settings.redis_key_prefix}auth:{settings.wechat_app_id}:"

    def session_key(self, token: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise unauthorized()
        digest = hashlib.sha256(token.encode()).hexdigest()
        return f"{self.prefix}session:{digest}"

    async def check_login_rate(self, client_ip: str) -> None:
        digest = hashlib.sha256(client_ip.encode()).hexdigest()
        try:
            count = await self.redis.eval(LOGIN_RATE_SCRIPT, 1, f"{self.prefix}login-rate:{digest}")
        except RedisError:
            raise ServiceError(503, "登录服务暂时不可用，请稍后重试") from None
        if count > self.settings.auth_login_rate_limit:
            raise ServiceError(429, "登录请求过于频繁，请一分钟后重试", {"Retry-After": "60"})

    async def issue(self, user_id: UUID) -> str:
        try:
            for _ in range(3):
                token = secrets.token_urlsafe(32)
                if await self.redis.set(
                    self.session_key(token),
                    str(user_id),
                    ex=self.settings.auth_session_ttl_seconds,
                    nx=True,
                ):
                    return token
        except RedisError:
            raise ServiceError(503, "登录态保存失败，请重新获取 code 后重试") from None
        raise ServiceError(503, "登录态生成失败，请重试")

    async def resolve(self, token: str) -> UUID:
        key = self.session_key(token)
        try:
            value = await self.redis.get(key)
        except RedisError:
            raise ServiceError(503, "登录服务暂时不可用，请稍后重试") from None
        if not value:
            raise unauthorized()
        try:
            return UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise unauthorized() from None

    async def revoke(self, token: str) -> None:
        key = self.session_key(token)
        try:
            await self.redis.delete(key)
        except RedisError:
            raise ServiceError(503, "退出登录失败，请稍后重试") from None
