"""FastAPI 应用工厂。"""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.lifespan import lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = FastAPI(
        title=settings.app_name,
        description="墨记后端，支持微信登录、个人账本、账单及预算管理。",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers={"Cache-Control": "no-store", **error.headers},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/v1/auth/"):
            # 验证错误不能原样回显请求体，避免一次性 code 或误传的密钥进入响应。
            return JSONResponse(
                status_code=422,
                content={"detail": "请求参数不正确，请检查 code 和接口允许的字段"},
                headers={"Cache-Control": "no-store"},
            )
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse(
                status_code=422,
                content={"detail": "请求参数不正确，请检查金额、日期、分类及允许的字段"},
                headers={"Cache-Control": "no-store"},
            )
        return await request_validation_exception_handler(request, error)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(api_router)
    return app
