"""探测真实连接，仅执行只读检查。"""

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url


async def check_connections(app: FastAPI) -> dict[str, str]:
    session_factory = app.state.session_factory
    redis = app.state.redis
    if session_factory is None or redis is None:
        raise RuntimeError("请先同时配置 DATABASE_URL 和 REDIS_URL")

    try:
        async with session_factory() as session:
            row = (await session.execute(text("SELECT current_database(), current_user"))).one()
    except Exception:
        raise RuntimeError("PostgreSQL 连接检查失败，请检查远程服务地址、网络和认证配置") from None

    expected_database = make_url(app.state.settings.database_url.get_secret_value()).database
    if row[0] != expected_database:
        raise RuntimeError("PostgreSQL 实际连接的数据库与配置不一致")

    try:
        if not await redis.ping():
            raise RuntimeError("PING 未返回成功")
    except Exception:
        raise RuntimeError("Redis 连接检查失败，请检查远程服务地址、网络和认证配置") from None

    return {"database": row[0], "database_user": row[1], "redis": "ok"}
