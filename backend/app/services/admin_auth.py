"""数据库管理员鉴权；会话版本变更撤销全部旧凭证。"""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from uuid import UUID

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.admin import AdminAccount
from app.repositories.admin import AdminRepository, AuditContext
from app.services.sessions import LOGIN_RATE_SCRIPT, SessionStore, unauthorized

password_hasher = PasswordHash.recommended()
dummy_hash = password_hasher.hash("moji-unconfigured-admin")


class AdminSessions(SessionStore):
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
        # 旧环境配置登录态不能进入数据库管理员体系。
        self.prefix = f"{settings.redis_key_prefix}admin:v2:"

    async def issue_account(self, account: AdminAccount) -> str:
        try:
            for _ in range(3):
                token = secrets.token_urlsafe(32)
                if await self.redis.set(
                    self.session_key(token),
                    json.dumps(
                        {
                            "admin_id": str(account.id),
                            "version": account.session_version,
                        }
                    ),
                    ex=self.settings.admin_session_ttl_seconds,
                    nx=True,
                ):
                    return token
        except RedisError:
            raise ServiceError(503, "管理员登录服务暂时不可用，请稍后重试") from None
        raise ServiceError(503, "管理员登录态生成失败，请重试")

    async def resolve_account(self, token: str) -> tuple[UUID, int]:
        key = self.session_key(token)
        try:
            value = await self.redis.get(key)
        except RedisError:
            raise ServiceError(503, "管理员登录服务暂时不可用，请稍后重试") from None
        try:
            data = json.loads(value)
            if (
                set(data) != {"admin_id", "version"}
                or type(data["version"]) is not int
                or data["version"] < 1
            ):
                raise ValueError
            return UUID(data["admin_id"]), data["version"]
        except (TypeError, ValueError, KeyError, AttributeError):
            raise unauthorized() from None

    async def security_rate(self, admin_id: UUID) -> None:
        digest = hashlib.sha256(str(admin_id).encode()).hexdigest()
        try:
            count = await self.redis.eval(
                LOGIN_RATE_SCRIPT, 1, f"{self.prefix}security-rate:{digest}"
            )
        except RedisError:
            raise ServiceError(503, "账号服务暂时不可用，请稍后重试") from None
        if count > self.settings.admin_login_rate_limit:
            raise ServiceError(429, "操作过于频繁，请一分钟后重试", {"Retry-After": "60"})


class AdminAuth:
    def __init__(self, redis: Redis, settings: Settings, repository: AdminRepository):
        self.settings = settings
        self.sessions = AdminSessions(redis, settings)
        self.repository = repository

    async def _commit(self) -> None:
        try:
            await self.repository.commit()
        except Exception:
            await self.repository.rollback()
            raise ServiceError(503, "账号服务暂时不可用，请稍后重试") from None

    async def _failure(
        self,
        username: str,
        action: str,
        reason: str,
        context: AuditContext,
        admin_id: UUID | None = None,
    ) -> None:
        self.repository.add_event(
            username, action, "failure", context, admin_id=admin_id, reason=reason
        )
        await self._commit()

    async def _verify(self, password: str, encoded: str) -> bool:
        try:
            return await run_in_threadpool(password_hasher.verify, password, encoded)
        except (UnknownHashError, ValueError):
            raise ServiceError(503, "管理员账号配置无效，请联系服务维护人员") from None

    async def login(
        self, username: str, password: str, context: AuditContext
    ) -> tuple[str, AdminAccount]:
        try:
            await self.sessions.check_login_rate(context.ip)
        except ServiceError as error:
            await self._failure(
                username,
                "login",
                "rate_limited" if error.status_code == 429 else "service_unavailable",
                context,
            )
            raise
        account = await self.repository.by_username(username)
        valid = await self._verify(password, account.password_hash if account else dummy_hash)
        if account is None or not account.is_active or not valid:
            await self._failure(
                username, "login", "invalid_credentials", context, account.id if account else None
            )
            raise ServiceError(401, "管理员账号或密码不正确")
        expected_version, encoded = account.session_version, account.password_hash
        account = await self.repository.by_id(account.id, lock=True)
        if (
            account is None
            or not account.is_active
            or account.session_version != expected_version
            or account.password_hash != encoded
        ):
            await self._failure(username, "login", "account_changed", context)
            raise ServiceError(401, "账号状态已更新，请重新登录")
        try:
            token = await self.sessions.issue_account(account)
        except ServiceError:
            await self._failure(username, "login", "service_unavailable", context, account.id)
            raise
        account.last_login_at = datetime.now(UTC)
        self.repository.add_event(username, "login", "success", context, admin_id=account.id)
        try:
            await self._commit()
        except ServiceError:
            try:
                await self.sessions.revoke(token)
            except ServiceError:
                pass
            raise
        return token, account

    async def authenticate(self, token: str) -> AdminAccount:
        admin_id, version = await self.sessions.resolve_account(token)
        account = await self.repository.by_id(admin_id)
        if account is None or not account.is_active or account.session_version != version:
            raise unauthorized()
        return account

    async def logout(self, account: AdminAccount, token: str, context: AuditContext) -> None:
        await self.sessions.revoke(token)
        self.repository.add_event(
            account.username, "logout", "success", context, admin_id=account.id
        )
        await self._commit()

    async def _locked_current(self, account: AdminAccount, expected_version: int) -> AdminAccount:
        locked = await self.repository.by_id(account.id, lock=True)
        if locked is None or not locked.is_active or locked.session_version != expected_version:
            raise unauthorized()
        return locked

    async def change_password(
        self, account: AdminAccount, current_password: str, new_password: str, context: AuditContext
    ) -> None:
        await self.sessions.security_rate(account.id)
        expected_version, encoded = account.session_version, account.password_hash
        if not await self._verify(current_password, encoded):
            await self._failure(
                account.username, "password_change", "current_password_invalid", context, account.id
            )
            raise ServiceError(400, "原密码不正确，请重新输入")
        if current_password == new_password:
            await self._failure(
                account.username, "password_change", "same_password", context, account.id
            )
            raise ServiceError(400, "新密码不能与原密码相同")
        if not 12 <= len(new_password) <= 256:
            raise ServiceError(422, "新密码长度必须为 12–256 个字符")
        new_hash = await run_in_threadpool(password_hasher.hash, new_password)
        locked = await self._locked_current(account, expected_version)
        locked.password_hash = new_hash
        locked.password_changed_at = datetime.now(UTC)
        locked.session_version += 1
        self.repository.add_event(
            locked.username, "password_change", "success", context, admin_id=locked.id
        )
        await self._commit()

    async def logout_all(self, account: AdminAccount, context: AuditContext) -> None:
        await self.sessions.security_rate(account.id)
        locked = await self._locked_current(account, account.session_version)
        locked.session_version += 1
        self.repository.add_event(
            locked.username, "logout_all", "success", context, admin_id=locked.id
        )
        await self._commit()
