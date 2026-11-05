"""有界模型会话；原生工具和显式结构化模式共享严格业务校验，不自动重试。"""

import copy
import json

import httpx
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.schemas.chat_v2 import ContextPlanV2, FinishDecisionV2, ToolDecision
from app.services.chat_v2_tools import ARGUMENTS

TOOL_DESCRIPTIONS = {
    "summarize_entries": "汇总当前账本的全部匹配记录，可按分类、日期、账户或记账人分组。",
    "search_entries": "检索账单并返回真实 ID、匹配依据和签名分页游标；一页不是全部记录。",
    "compare_periods": "按相同筛选比较两个明确日期区间，计算差额及分类贡献，不推断现实原因。",
    "get_entry": "读取当前账本内单笔有效账单，必须使用已知的真实账单 ID。",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def strict_schema(model):
    """展开本地 $ref 与标签联合；默认字段显式必填，保留 nullable 和业务约束。"""
    root = model.model_json_schema(by_alias=True)
    definitions = root.get("$defs", {})

    def expand(node):
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = node["$ref"].removeprefix("#/$defs/")
            return expand(definitions[target])
        result = {
            ("anyOf" if key == "oneOf" else key): expand(value)
            for key, value in node.items()
            if key not in {"$defs", "title", "default", "discriminator"}
        }
        if result.get("type") == "object":
            result["required"] = list(result.get("properties", {}))
            result["additionalProperties"] = False
        return result

    return expand(root)


def definition(model):
    return {
        "type": "json_schema",
        "name": "moji_v2_decision",
        "strict": True,
        "schema": strict_schema(model),
    }


class ChatModelV2:
    def __init__(self, llm: LlmClient):
        self.llm, self.settings = llm, llm.settings

    def session(self, instructions, data):
        return ModelSessionV2(self, instructions, data)

    async def plan(self, instructions, data):
        session = self.session(instructions, data)
        payload = session.payload(ContextPlanV2)
        body = await self.post(payload)
        try:
            return ContextPlanV2.model_validate(session.text_result(body))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, ValidationError):
            raise ServiceError(502, "助手未能理解筛选修改，请明确日期或条件后重试") from None

    async def post(self, payload):
        settings = self.settings
        if len(encoded(payload)) > settings.chat_v2_max_input_chars:
            raise ServiceError(422, "本轮查询内容过多，请缩小日期范围或减少条件")
        base = settings.llm_base_url
        base = base if base.endswith("/v1") else base + "/v1"
        endpoint = "/responses" if settings.llm_api_mode == "responses" else "/chat/completions"
        if not settings.llm_api_key:
            raise ServiceError(503, "尚未配置智能记账服务，请先使用手动记账")
        try:
            async with self.llm.client.stream(
                "POST",
                base + endpoint,
                json=payload,
                headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                timeout=httpx.Timeout(settings.chat_timeout_seconds, connect=5),
            ) as response:
                self.llm.check_status(response)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > settings.chat_v2_max_response_bytes:
                        raise ServiceError(502, "助手返回内容过多，请缩小问题范围后重试")
                    content.extend(chunk)
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("响应不是对象")
                return result
        except httpx.TimeoutException:
            raise ServiceError(504, "智能助手响应超时，请稍后重试") from None
        except httpx.HTTPError:
            raise ServiceError(502, "智能助手连接中断，请稍后重试") from None
        except (ValueError, UnicodeError):
            raise ServiceError(502, "智能助手返回结果不完整，请稍后重试") from None


class ModelSessionV2:
    def __init__(self, model, instructions, data):
        self.model, self.settings = model, model.settings
        self.messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": encoded(data)},
        ]
        self.pending = None
        self.call_ids = set()

    def payload(self, result_model):
        responses = self.settings.llm_api_mode == "responses"
        payload = {"model": self.settings.llm_model, "stream": False}
        if responses:
            payload.update(
                input=copy.deepcopy(self.messages),
                store=False,
                reasoning={"effort": "xhigh"},
                include=["reasoning.encrypted_content"],
                text={"format": definition(result_model)},
                max_output_tokens=self.settings.chat_v2_max_output_tokens,
            )
        else:
            fmt = definition(result_model)
            fmt.pop("type")
            payload.update(
                messages=copy.deepcopy(self.messages),
                reasoning_effort="xhigh",
                response_format={"type": "json_schema", "json_schema": fmt},
                max_completion_tokens=self.settings.chat_v2_max_output_tokens,
            )
        return payload

    def text_result(self, body):
        if self.settings.llm_api_mode == "responses":
            if body.get("status") != "completed":
                raise ValueError("响应未完成")
            parts = []
            for item in body["output"]:
                if item["type"] not in {"message", "reasoning"}:
                    raise ValueError("存在未处理输出")
                if item["type"] == "message":
                    for part in item.get("content", []):
                        if part["type"] != "output_text":
                            raise ValueError("正文未完成")
                        parts.append(part["text"])
            text = "".join(parts)
        else:
            choices = body["choices"]
            if len(choices) != 1 or choices[0]["finish_reason"] != "stop":
                raise ValueError("响应未完成")
            message = choices[0]["message"]
            if message.get("refusal") or message.get("tool_calls"):
                raise ValueError("正文未完成")
            text = message["content"]
        return self.model.llm.parse_result(text)

    async def decide(self, *, allow_tools=True):
        if self.pending:
            raise ServiceError(502, "上一步查询尚未完成")
        native = self.settings.chat_v2_tool_mode == "native"
        payload = self.payload(FinishDecisionV2 if native or not allow_tools else ToolDecision)
        if native:
            definitions = [
                {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": strict_schema(args),
                    "strict": True,
                }
                for name, args in ARGUMENTS.items()
            ]
            responses = self.settings.llm_api_mode == "responses"
            payload["tools"] = [
                {"type": "function", **tool}
                if responses
                else {"type": "function", "function": tool}
                for tool in definitions
            ]
            payload.update(parallel_tool_calls=False, tool_choice="auto" if allow_tools else "none")
        else:
            # 结构化兼容路径按用户消息保留模型决策；工具结果只能作为数据回传。
            payload["input" if self.settings.llm_api_mode == "responses" else "messages"].append(
                {"role": "user", "content": encoded({"toolsAllowed": allow_tools})}
            )
        body = await self.model.post(payload)
        try:
            if native:
                call = self.native_call(body)
                if call:
                    if not allow_tools:
                        raise ValueError("工具已停用")
                    return call
            result = self.text_result(body)
            decision = ToolDecision.model_validate(result)
            if not allow_tools and decision.action == "tool":
                raise ValueError("工具已停用")
            if decision.action == "tool":
                self.pending = "structured"
                self.messages.append({"role": "assistant", "content": encoded(result)})
            return decision
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, ValidationError):
            raise ServiceError(502, "助手返回的查询步骤不完整，请重新说明问题") from None

    def native_call(self, body):
        responses = self.settings.llm_api_mode == "responses"
        if responses:
            if body.get("status") != "completed":
                raise ValueError("工具调用未完成")
            output = body["output"]
            if any(
                item["type"] not in {"message", "reasoning", "function_call"} for item in output
            ):
                raise ValueError("未知工具输出")
            calls = [item for item in output if item["type"] == "function_call"]
            if not calls:
                return None
            if len(calls) != 1:
                raise ValueError("仅支持串行工具")
            call = calls[0]
            call_id, name, arguments = call["call_id"], call["name"], call["arguments"]
            continuation = copy.deepcopy(output)
            if any(
                part.get("type") == "refusal" for item in output for part in item.get("content", [])
            ):
                raise ValueError("工具调用被拒绝")
        else:
            choices = body["choices"]
            if len(choices) != 1:
                raise ValueError("多个候选")
            message = choices[0]["message"]
            calls = message.get("tool_calls", [])
            if not calls:
                return None
            if (
                choices[0]["finish_reason"] != "tool_calls"
                or message.get("refusal")
                or len(calls) != 1
            ):
                raise ValueError("工具调用未完成")
            call = calls[0]
            if call["type"] != "function":
                raise ValueError("未知工具类型")
            call_id, name = call["id"], call["function"]["name"]
            arguments = call["function"]["arguments"]
            continuation = [
                {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
            ]
        if (
            not isinstance(call_id, str)
            or not call_id
            or len(call_id) > 256
            or call_id in self.call_ids
        ):
            raise ValueError("调用编号无效或重复")
        decision = ToolDecision.model_validate(
            {
                "action": "tool",
                "call": {"name": name, "arguments": self.model.llm.parse_result(arguments)},
                "text": None,
            }
        )
        self.messages.extend(continuation)
        self.pending = call_id
        self.call_ids.add(call_id)
        return decision

    def submit(self, result):
        if not self.pending:
            raise ServiceError(502, "查询回传缺少对应步骤")
        value = {"dataOnly": True, "toolResult": result.model_dump(mode="json", by_alias=True)}
        if self.settings.chat_v2_tool_mode == "structured":
            message = {"role": "user", "content": encoded(value)}
        elif self.settings.llm_api_mode == "responses":
            message = {
                "type": "function_call_output",
                "call_id": self.pending,
                "output": encoded(value),
            }
        else:
            message = {"role": "tool", "tool_call_id": self.pending, "content": encoded(value)}
        self.messages.append(message)
        self.pending = None
