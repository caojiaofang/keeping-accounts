"""两种协议的探测、工具结果回传、失败脱敏与取消；全部使用内存传输。"""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.llm import LlmClient
from app.integrations.llm_probe import ModelProbe, main, recommendation


def echo_body(mode, value):
    text = json.dumps({"echo": value})
    if mode == "responses":
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        }
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


class ProbeTransport:
    def __init__(self, mode, failure=None):
        self.mode = mode
        self.failure = failure
        self.calls = []
        self.streams = []

    def __call__(self, request):
        payload = json.loads(request.content)
        self.calls.append(payload)
        responses = self.mode == "responses"
        assert request.headers["Authorization"] == "Bearer private-probe-key"
        assert (payload["reasoning"] if responses else payload["reasoning_effort"]) == (
            {"effort": "xhigh"} if responses else "xhigh"
        )
        if responses:
            assert payload["store"] is False
        if self.failure == "service":
            return httpx.Response(401, json={"error": "private-upstream-body"})
        if self.failure == "network":
            raise httpx.ConnectError("private-connection", request=request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout("private-connection", request=request)
        messages = payload["input" if responses else "messages"]
        question = messages[1]["content"]
        if payload["stream"]:
            nonce = question.split()[-1]
            stream = ProbeStream(self.mode, nonce, "取消" in question, self.failure)
            self.streams.append(stream)
            return httpx.Response(200, stream=stream)
        if "tools" not in payload:
            return httpx.Response(200, json=echo_body(self.mode, question.split()[-1]))
        if self.failure == "unsupported":
            return httpx.Response(400, json={"error": "private-upstream-body"})
        if payload["tool_choice"] != "none":
            arguments = json.dumps({"echo": question.split()[-1]})
            if self.failure == "malformed":
                arguments = "{broken-private-json"
            if responses:
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {"type": "reasoning", "id": "rs-probe", "summary": []},
                            {
                                "type": "function_call",
                                "name": "echo_probe",
                                "call_id": "call-probe",
                                "arguments": arguments,
                            },
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "id": "call-probe",
                                        "function": {"name": "echo_probe", "arguments": arguments},
                                    }
                                ],
                            },
                        }
                    ]
                },
            )
        if responses:
            assert messages[2]["type"] == "reasoning"
            assert messages[-1]["type"] == "function_call_output"
            assert messages[-1]["call_id"] == "call-probe"
            result = json.loads(messages[-1]["output"])
        else:
            assert messages[-2]["tool_calls"][0]["id"] == "call-probe"
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "call-probe"
            result = json.loads(messages[-1]["content"])
        return httpx.Response(200, json=echo_body(self.mode, result["receipt"]))


class ProbeStream(httpx.AsyncByteStream):
    def __init__(self, mode, nonce, cancellation, failure):
        self.mode, self.nonce = mode, nonce
        self.cancellation, self.failure = cancellation, failure
        self.closed = False

    async def __aiter__(self):
        if self.failure == "waiting":
            await asyncio.Event().wait()
        if self.failure == "malformed_stream":
            yield b"data: {bad-json}\n\n"
            return
        text = json.dumps({"echo": self.nonce})
        event = (
            {"type": "response.output_text.delta", "delta": text}
            if self.mode == "responses"
            else {"choices": [{"delta": {"content": text}}]}
        )
        yield ("data: " + json.dumps(event) + "\n\n").encode()
        if self.cancellation:
            await asyncio.Event().wait()
        if self.failure == "truncated":
            return
        event = (
            {"type": "response.completed", "response": {"status": "completed"}}
            if self.mode == "responses"
            else {"choices": [{"finish_reason": "stop"}]}
        )
        yield ("data: " + json.dumps(event) + "\n\n").encode()

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "unsupported",
        "malformed",
        "service",
        "network",
        "timeout",
        "truncated",
        "malformed_stream",
    ],
)
def test_probe_capabilities_and_safe_failures(mode, failure):
    async def run():
        transport = ProbeTransport(mode, failure)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            settings = Settings(
                _env_file=None, llm_api_mode=mode, llm_api_key=SecretStr("private-probe-key")
            )
            checks = await ModelProbe(LlmClient(settings, client)).run()
        if failure is None:
            assert all(check.status == "passed" for check in checks.values())
            assert len(transport.calls) == 5
            assert recommendation(checks) == "native"
        elif failure == "unsupported":
            assert checks["native_tools"].status == "rejected"
            assert recommendation(checks) == "structured"
        elif failure == "malformed":
            assert checks["native_tools"].status == "failed"
            assert recommendation(checks) == "structured"
        else:
            assert recommendation(checks) == "pending"
        assert all(stream.closed for stream in transport.streams)
        assert "private" not in str(checks)

    asyncio.run(run())


def test_cli_without_live_does_not_read_settings_or_send_requests(monkeypatch, capsys):
    import app.integrations.llm_probe as module

    def forbidden(*args, **kwargs):
        pytest.fail("默认命令不能读取真实配置或创建网络客户端")

    monkeypatch.setattr(module, "Settings", forbidden)
    monkeypatch.setattr(module.httpx, "AsyncClient", forbidden)
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["live"] is False


def test_external_cancel_closes_stream_and_propagates():
    async def run():
        transport = ProbeTransport("responses", "waiting")
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            probe = ModelProbe(
                LlmClient(
                    Settings(_env_file=None, llm_api_key=SecretStr("private-probe-key")), client
                )
            )
            task = asyncio.create_task(probe.cancellation())
            while not transport.streams:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert transport.streams[0].closed

    asyncio.run(run())
