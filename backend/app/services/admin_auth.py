"""管理员使用独立 Redis 命名空间；改密后旧登录态自动失效。"""

import hashlib
import hmac
from uuid import NAMESPACE_URL, uuid5

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from redis.asyncio import Redis
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import ServiceError
from app.services.sessions import SessionStore, unauthorized

password_hasher = PasswordHash.recommended()
# 未配置账号时也执行密码校验，避免通过耗时判断账号是否存在。
dummy_hash = password_hasher.hash("moji-unconfigured-admin")


class AdminAuth(SessionStore):
    def __init__(self, redis: Redis, settings: Settings):
        super().__init__(
            redis,
            settings.model_copy(
                update={
                    "auth_session_ttl_seconds": settings.admin_session_ttl_seconds,
                    "auth_login_rate_limit": settings.admin_login_rate_limit,
                }
            ),
        )
        self.prefix = f"{settings.redis_key_prefix}admin:"
        self.username = settings.admin_username
        self.password_hash = (
            settings.admin_password_hash.get_secret_value() if settings.admin_password_hash else ""
        )
        fingerprint = hashlib.sha256(self.password_hash.encode()).hexdigest()
        self.identity = uuid5(NAMESPACE_URL, f"moji-admin:{self.username}:{fingerprint}")

    async def login(self, username: str, password: str, client_ip: str) -> str:
        await self.check_login_rate(client_ip)
        try:
            valid = await run_in_threadpool(
                password_hasher.verify, password, self.password_hash or dummy_hash
            )
        except (UnknownHashError, ValueError):
            raise ServiceError(503, "管理员账号配置无效，请检查密码摘要配置") from None
        if not self.password_hash:
            raise ServiceError(503, "管理员账号尚未初始化，请先完成服务端配置")
        if not hmac.compare_digest(username.encode(), self.username.encode()) or not valid:
            raise ServiceError(401, "管理员账号或密码不正确")
        return await self.issue(self.identity)

    async def authenticate(self, token: str) -> str:
        if not self.password_hash or await self.resolve(token) != self.identity:
            raise unauthorized()
        return self.username
