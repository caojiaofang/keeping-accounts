"""微信 code2Session 适配，不保存或暴露 session_key。"""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.errors import ServiceError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeChatIdentity:
    openid: str


class WeChatClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        # HTTPX 的 INFO/DEBUG 日志含完整 URL，微信接口的 secret 位于查询参数中。
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self.settings = settings
        self.client = client

    async def exchange_code(self, code: str) -> WeChatIdentity:
        if not self.settings.wechat_app_secret:
            raise ServiceError(503, "尚未配置微信登录凭据")
        try:
            response = await self.client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": self.settings.wechat_app_id,
                    "secret": self.settings.wechat_app_secret.get_secret_value(),
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            raise ServiceError(504, "微信登录请求超时，请重新获取 code 后重试") from None
        except (httpx.HTTPError, ValueError):
            raise ServiceError(502, "微信登录服务暂时不可用，请稍后重试") from None

        if not isinstance(payload, dict):
            raise ServiceError(502, "微信登录服务返回异常")
        error_code = payload.get("errcode", 0)
        if type(error_code) is not int:
            raise ServiceError(502, "微信登录服务返回异常")
        if error_code:
            # 只记录错误码，不能记录带 secret 的请求 URL、code 或原始响应。
            logger.warning("微信登录失败，errcode=%s", error_code)
            if error_code in {40029, 40163}:
                raise ServiceError(401, "微信登录 code 无效或已使用，请重新调用 wx.login")
            if error_code == 40226:
                raise ServiceError(403, "微信暂时限制了该账号登录")
            if error_code == 45011:
                raise ServiceError(429, "微信登录过于频繁，请稍后重试", {"Retry-After": "60"})
            if error_code in {40013, 40001, 40125}:
                raise ServiceError(503, "微信应用配置无效，请检查后端 AppID 和 AppSecret")
            if error_code == -1:
                raise ServiceError(503, "微信服务繁忙，请稍后重试")
            raise ServiceError(502, "微信登录失败，请稍后重试")

        openid = payload.get("openid")
        session_key = payload.get("session_key")
        if (
            not isinstance(openid, str)
            or not openid.strip()
            or len(openid) > 128
            or openid != openid.strip()
            or not isinstance(session_key, str)
            or not session_key.strip()
        ):
            raise ServiceError(502, "微信登录服务返回异常")
        return WeChatIdentity(openid=openid)
