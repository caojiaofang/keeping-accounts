"""显式运行的无业务数据模型探测；默认不读取密钥，也不发送网络请求。"""

import argparse
import asyncio
import json
import sys
import time
from contextlib import aclosing, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient

ECHO_SCHEMA = {
    "type": "object",
    "properties": {"echo": {"type": "string"}},
    "required": ["echo"],
    "additionalProperties": False,
}
INSTRUCTIONS = "你在进行协议探测。只返回指定结构，不访问外部数据。"


@dataclass
class ProbeCheck:
    status: Literal["passed", "rejected", "failed", "not_run"] = "not_run"
    elapsed_ms: int = 0
    detail: str = "未启用真实探测"


class ProbeRejected(Exception):
    """只保留 HTTP 状态，不携带上游错误正文。"""


class ModelProbe:
    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def structured(self):
        nonce = uuid4().hex
        result = await self.llm.structured(INSTRUCTIONS, f"echo 必须为 {nonce}", ECHO_SCHEMA)
        self.validate_echo(result, nonce)

    async def streaming(self):
        nonce = uuid4().hex
        chunks = []
        async with aclosing(
            self.llm.structured_stream(INSTRUCTIONS, f"echo 必须为 {nonce}", ECHO_SCHEMA)
        ) as stream:
            async for delta in stream:
                chunks.append(delta)
        self.validate_echo(self.llm.parse_result("".join(chunks)), nonce)

    async def cancellation(self):
        """收到正文后主动取消读取任务，并等待流清理完成。"""
        received = asyncio.Event()

        async def consume():
            async with aclosing(
                self.llm.structured_stream(
                    INSTRUCTIONS, "echo 请填写连续一千个汉字，用于测试客户端中断", ECHO_SCHEMA
                )
            ) as stream:
                async for _ in stream:
                    received.set()
                    # 保持流打开，使取消测试不会因模型已发完短正文而退化。
                    await asyncio.Event().wait()

        task = asyncio.create_task(consume())
        waiter = asyncio.create_task(received.wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task
                raise ValueError("取消前未收到正文")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return
            raise ValueError("读取任务未传播取消")
        finally:
            for pending in (task, waiter):
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending

    async def post(self, base, endpoint, payload):
        response = await self.llm.client.post(
            base + endpoint,
            json=payload,
            headers={"Authorization": "Bearer " + self.llm.settings.llm_api_key.get_secret_value()},
            timeout=httpx.Timeout(self.llm.settings.chat_timeout_seconds, connect=5),
        )
        if response.status_code in {400, 404, 405, 415, 422, 501}:
            raise ProbeRejected()
        self.llm.check_status(response)
        return response.json()

    async def native_tools(self):
        """先验证强制工具调用，再回传新生成的工具结果，验证完整往返。"""
        nonce = uuid4().hex
        receipt = uuid4().hex
        base, endpoint, payload = self.llm.request_payload(
            INSTRUCTIONS + " 必须调用 echo_probe；收到工具结果后，将其 receipt 填入 echo。",
            f"调用 echo_probe，echo 参数必须为 {nonce}",
            ECHO_SCHEMA,
        )
        definition = {
            "name": "echo_probe",
            "description": "只用于协议探测，不读写任何业务数据。",
            "parameters": ECHO_SCHEMA,
            "strict": True,
        }
        responses = self.llm.settings.llm_api_mode == "responses"
        payload["parallel_tool_calls"] = False
        if responses:
            payload.pop("text")
            payload["tools"] = [{"type": "function", **definition}]
            payload["tool_choice"] = {"type": "function", "name": "echo_probe"}
        else:
            payload.pop("response_format")
            payload["tools"] = [{"type": "function", "function": definition}]
            payload["tool_choice"] = {"type": "function", "function": {"name": "echo_probe"}}
        first = await self.post(base, endpoint, payload)
        if responses:
            if first.get("status") != "completed":
                raise ValueError("工具调用未完成")
            calls = [item for item in first["output"] if item.get("type") == "function_call"]
            if len(calls) != 1 or calls[0]["name"] != "echo_probe" or not calls[0]["call_id"]:
                raise ValueError("工具调用不匹配")
            self.validate_echo(self.llm.parse_result(calls[0]["arguments"]), nonce)
            # 无状态回传包含 reasoning 项在内的 output；不记录或向用户展示。
            payload["input"] += first["output"] + [
                {
                    "type": "function_call_output",
                    "call_id": calls[0]["call_id"],
                    "output": json.dumps({"receipt": receipt}),
                }
            ]
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "probe_result",
                    "strict": True,
                    "schema": ECHO_SCHEMA,
                }
            }
        else:
            choice = first["choices"][0]
            message = choice["message"]
            calls = message.get("tool_calls", [])
            if choice["finish_reason"] != "tool_calls" or len(calls) != 1:
                raise ValueError("工具调用不匹配")
            call = calls[0]
            if call["type"] != "function" or call["function"]["name"] != "echo_probe":
                raise ValueError("工具名称不匹配")
            if not call["id"]:
                raise ValueError("缺少工具调用 ID")
            self.validate_echo(self.llm.parse_result(call["function"]["arguments"]), nonce)
            payload["messages"] += [
                message,
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps({"receipt": receipt}),
                },
            ]
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "probe_result", "strict": True, "schema": ECHO_SCHEMA},
            }
        payload["tool_choice"] = "none"
        final = await self.post(base, endpoint, payload)
        if responses:
            if final.get("status") != "completed":
                raise ValueError("工具结果回传未完成")
            text = "".join(
                part["text"]
                for item in final["output"]
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
        else:
            choice = final["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                raise ValueError("工具结果回传未完成")
            text = choice["message"]["content"]
        self.validate_echo(self.llm.parse_result(text), receipt)

    @staticmethod
    def validate_echo(result, expected):
        if result != {"echo": expected}:
            raise ValueError("探测结构或内容不匹配")

    async def run(self, progress=None):
        checks = {}
        for name in ("structured", "native_tools", "streaming", "cancellation"):
            started = time.monotonic()
            try:
                async with asyncio.timeout(self.llm.settings.chat_timeout_seconds):
                    await getattr(self, name)()
                check = ProbeCheck("passed", detail="通过协议及内容校验")
            except ProbeRejected:
                check = ProbeCheck("rejected", detail="上游拒绝本探测请求，未确认能力可用")
            except (TimeoutError, httpx.TimeoutException):
                check = ProbeCheck("failed", detail="探测超时，未确认能力可用")
            except (ServiceError, httpx.HTTPError):
                check = ProbeCheck("failed", detail="模型服务请求失败，未确认能力可用")
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                check = ProbeCheck("failed", detail="响应结构或内容不符合探测协议")
            check.elapsed_ms = round((time.monotonic() - started) * 1000)
            checks[name] = check
            if progress:
                progress(name, check)
        return checks


def recommendation(checks):
    if any(checks[key].status != "passed" for key in ("structured", "streaming", "cancellation")):
        return "pending"
    return "native" if checks["native_tools"].status == "passed" else "structured"


def main(argv=None):
    # Windows 重定向输出统一为 UTF-8；不改变配置文件或源文件编码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="模型能力探测，默认零网络请求")
    parser.add_argument("--live", action="store_true", help="显式启用真实模型，最多五次付费请求")
    args = parser.parse_args(argv)
    if not args.live:
        print(
            json.dumps(
                {
                    "live": False,
                    "recommendation": "pending",
                    "detail": "未发送请求；使用 --live 启用真实探测",
                },
                ensure_ascii=False,
            )
        )
        return 0

    async def run():
        try:
            settings = Settings()
        except ValidationError:
            print("配置无效，请检查本地环境；不输出含输入值的配置错误。")
            return 2
        if not settings.llm_api_key:
            print("未配置 LLM_API_KEY，未发送请求。")
            return 2
        async with httpx.AsyncClient() as client:

            def progress(name, check):
                print(f"{name}: {check.status} ({check.elapsed_ms} ms)", flush=True)

            checks = await ModelProbe(LlmClient(settings, client)).run(progress)
        report = {
            "live": True,
            "checkedAt": datetime.now(UTC).isoformat(),
            "model": settings.llm_model,
            "apiMode": settings.llm_api_mode,
            "reasoningEffort": "xhigh",
            "recommendation": recommendation(checks),
            "checks": {name: asdict(check) for name, check in checks.items()},
            "cancellationScope": "本地读取任务已取消并关闭流；不证明上游停止计费",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if all(check.status == "passed" for check in checks.values()) else 1

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        print("探测已取消，活动连接已清理。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
