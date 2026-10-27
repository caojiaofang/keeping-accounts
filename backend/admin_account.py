"""显式初始化或交互恢复数据库管理员；不接受命令行明文密码。"""

import argparse
import asyncio
from datetime import UTC, datetime
from getpass import getpass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.repositories.admin import AdminRepository, AuditContext
from app.services.admin_auth import password_hasher


async def run(mode: str, username: str | None, password: str | None) -> None:
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("请先配置数据库连接并执行 alembic upgrade head")
    username = username or settings.admin_username
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            repository = AdminRepository(session)
            account = await repository.by_username(username)
            if mode == "bootstrap":
                if account:
                    print("管理员已存在，保留数据库中的密码和会话版本。")
                    return
                if not settings.admin_password_hash:
                    raise SystemExit(
                        "首次初始化需配置 ADMIN_PASSWORD_HASH，已有账号不使用该配置登录"
                    )
                encoded = settings.admin_password_hash.get_secret_value()
                password_hasher.verify("bootstrap-validation", encoded)
                created = await repository.bootstrap(username, encoded)
                print("管理员初始化完成。" if created else "管理员已存在，未覆盖现有密码。")
            else:
                if account is None:
                    raise SystemExit("管理员不存在，请先初始化")
                account = await repository.by_id(account.id, lock=True)
                account.password_hash = password_hasher.hash(password)
                account.password_changed_at = datetime.now(UTC)
                account.session_version += 1
                repository.add_event(
                    username,
                    "password_reset",
                    "success",
                    AuditContext("console"),
                    admin_id=account.id,
                )
                await repository.commit()
                print("管理员密码已重置，所有旧会话已失效。")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="管理员初始化与服务端密码恢复")
    parser.add_argument("mode", choices=["bootstrap", "reset-password"])
    parser.add_argument("--username")
    args = parser.parse_args()
    password = None
    if args.mode == "reset-password":
        password = getpass("设置新密码（12–256 字符）：")
        if not 12 <= len(password) <= 256 or password != getpass("再次输入新密码："):
            raise SystemExit("密码长度不正确或两次输入不一致")
    asyncio.run(run(args.mode, args.username, password))


if __name__ == "__main__":
    main()
