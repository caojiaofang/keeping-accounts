"""管理连接资源，可选启动检查，不自动建表或迁移。"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.connections import check_connections
from app.integrations.wechat import WeChatClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.session_factory = None
    app.state.redis = None
    app.state.wechat = None

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                timeout=httpx.Timeout(settings.wechat_timeout_seconds, connect=5),
                trust_env=False,
            )
        )
        app.state.wechat = WeChatClient(settings, client)
        if settings.database_url:
            engine = create_async_engine(
                settings.database_url.get_secret_value(),
                pool_pre_ping=True,
                connect_args={"timeout": 10},
            )
            stack.push_async_callback(engine.dispose)
            app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)

        if settings.redis_url:
            redis = Redis.from_url(
                settings.redis_url.get_secret_value(),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                protocol=2,
                health_check_interval=30,
            )
            stack.push_async_callback(redis.aclose)
            app.state.redis = redis

        try:
            if settings.check_connections_on_startup:
                result = await check_connections(app)
                logger.info("PostgreSQL 已连接：%s；Redis 已连接", result["database"])
            logger.info("应用启动，环境：%s", settings.app_env)
            yield
        finally:
            app.state.session_factory = None
            app.state.redis = None
            app.state.wechat = None
            logger.info("应用关闭，释放连接资源")
