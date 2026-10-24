"""为后续业务接口提供配置、数据库会话和 Redis 客户端。"""

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.session_factory
    if session_factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "尚未配置数据库连接")

    # 会话退出时释放资源，未提交的事务回滚；写入由业务服务显式管理事务。
    async with session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    redis = request.app.state.redis
    if redis is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "尚未配置 Redis 连接")
    return redis
