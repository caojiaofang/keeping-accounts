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
        base, endpoint, payload = self.request_payload(instructions, text, schema)
        settings = self.settings
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
        self.check_status(response)
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
            return self.parse_result(text)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise ServiceError(502, "智能助手返回结果不完整，请稍后重试") from None

    def request_payload(self, instructions: str, text: str, schema: dict):
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
        return base, endpoint, payload

    @staticmethod
    def check_status(response):
        if response.status_code == 429:
            raise ServiceError(429, "智能记账服务繁忙，请稍后重试")
        if not response.is_success:
            raise ServiceError(502, "智能记账服务暂时不可用，请稍后重试")

    @staticmethod
    def parse_result(text):
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError("输出过长或格式错误")
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("输出不是对象")
        return result

    async def structured_stream(self, instructions: str, text: str, schema: dict):
        """只转发正文增量，不转发推理内容；缺少正常结束事件视为生成失败。"""
        base, endpoint, payload = self.request_payload(instructions, text, schema)
        payload["stream"] = True
        settings = self.settings
        completed, size = False, 0
        try:
            async with self.client.stream(
                "POST",
                base + endpoint,
                json=payload,
                headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                timeout=httpx.Timeout(settings.chat_timeout_seconds, connect=5),
            ) as response:
                self.check_status(response)
                data = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
                        continue
                    if line or not data:
                        continue
                    encoded, data = "\n".join(data), []
                    if encoded == "[DONE]":
                        break
                    event = json.loads(encoded)
                    delta = ""
                    if settings.llm_api_mode == "responses":
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            delta = event["delta"]
                        elif kind == "response.completed":
                            completed = event["response"].get("status") == "completed"
                            if not completed:
                                raise ValueError("生成未完成")
                            break
                        elif kind in {"error", "response.failed", "response.incomplete"}:
                            raise ValueError("生成失败")
                        elif kind == "response.refusal.delta":
                            raise ValueError("生成被拒绝")
                    else:
                        if event.get("error"):
                            raise ValueError("生成失败")
                        for choice in event.get("choices", []):
                            if choice.get("index", 0) != 0:
                                continue
                            if choice.get("delta", {}).get("refusal"):
                                raise ValueError("生成被拒绝")
                            delta = choice.get("delta", {}).get("content") or ""
                            if choice.get("finish_reason") is not None:
                                if choice["finish_reason"] != "stop":
                                    raise ValueError("生成未完成")
                                completed = True
                    if not isinstance(delta, str):
                        raise ValueError("正文格式错误")
                    size += len(delta)
                    if size > 20000:
                        raise ValueError("正文过长")
                    if delta:
                        yield delta
                if not completed or not size:
                    raise ValueError("生成未完成")
        except httpx.TimeoutException:
            raise ServiceError(504, "智能助手响应超时，请稍后重试") from None
        except httpx.HTTPError:
            raise ServiceError(502, "暂时连接不到智能记账服务，请稍后重试") from None
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise ServiceError(502, "智能助手返回结果不完整，请稍后重试") from None
