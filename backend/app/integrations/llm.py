"""sub2api 适配；不记录请求正文、密钥或上游错误原文，不自动重试付费请求。"""

import json

import httpx

from app.core.config import Settings
from app.core.errors import ServiceError


class LlmClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def structured(self, instructions: str, text: str, schema: dict) -> dict:
        settings = self.settings
        if not settings.llm_api_key:
            raise ServiceError(503, "尚未配置智能记账服务，请先使用手动记账")
        messages = [{"role": "system", "content": instructions}, {"role": "user", "content": text}]
        definition = {"name": "moji_result", "strict": True, "schema": schema}
        base = settings.llm_base_url
        base = base if base.endswith("/v1") else base + "/v1"
        payload = {"model": settings.llm_model, "stream": False}
        if settings.llm_api_mode == "responses":
            endpoint = "/responses"
            payload.update(
                input=messages,
                store=False,
                reasoning={"effort": "xhigh"},
                text={"format": {"type": "json_schema", **definition}},
            )
        else:
            endpoint = "/chat/completions"
            payload.update(
                messages=messages,
                reasoning_effort="xhigh",
                response_format={"type": "json_schema", "json_schema": definition},
            )
        try:
            response = await self.client.post(
                base + endpoint,
                json=payload,
                headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=5),
            )
        except httpx.TimeoutException:
            raise ServiceError(504, "智能助手响应超时，请稍后重试") from None
        except httpx.HTTPError:
            raise ServiceError(502, "暂时连接不到智能记账服务，请稍后重试") from None
        if response.status_code == 429:
            raise ServiceError(429, "智能记账服务繁忙，请稍后重试")
        if not response.is_success:
            raise ServiceError(502, "智能记账服务暂时不可用，请稍后重试")
        try:
            body = response.json()
            if settings.llm_api_mode == "responses":
                if body.get("status") != "completed":
                    raise ValueError("生成未完成")
                text = "".join(
                    part["text"]
                    for item in body["output"]
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                )
            else:
                choice = body["choices"][0]
                if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                    raise ValueError("生成未完成")
                text = choice["message"]["content"]
            if not isinstance(text, str) or len(text) > 20000:
                raise ValueError("输出过长或格式错误")
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError("输出不是对象")
            return result
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise ServiceError(502, "智能助手返回结果不完整，请稍后重试") from None
