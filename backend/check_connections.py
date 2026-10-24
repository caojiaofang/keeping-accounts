"""手动验证开发库与 Redis；只写入一个短期 Redis 探测 key 并清理。"""

import asyncio
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.connections import check_connections
from app.main import create_app


async def main() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        result = await check_connections(app)
        settings = app.state.settings
        key = f"{settings.redis_key_prefix}connection-check:{uuid4().hex}"
        redis = app.state.redis
        try:
            await redis.set(key, "ok", ex=30)
            if await redis.get(key) != "ok":
                raise RuntimeError("Redis 临时数据读写检查失败")
        finally:
            await redis.delete(key)
        print(f"PostgreSQL 连接成功：{result['database']}，账号：{result['database_user']}")
        print("Redis PING 与临时数据读写成功，探测 key 已清理")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (RuntimeError, OSError, RedisError):
        print("连接检查失败：请检查远程服务的网络连通性及 backend/.env 认证配置。")
        raise SystemExit(1) from None
