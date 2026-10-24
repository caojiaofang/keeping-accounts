"""模型协议、固定推理强度、错误处理与草稿校验，不发送真实请求。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.schemas.assistant import ParseRequest
from app.services.assistant import AssistantService


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_protocol_and_fixed_xhigh(mode):
    async def run():
        def handler(request):
            payload = json.loads(request.content)
            assert payload["model"] == "gpt-6-luna"
            assert request.headers["Authorization"] == "Bearer private-key"
            assert payload["stream"] is False
            if mode == "responses":
                assert request.url.path == "/v1/responses"
                assert payload["reasoning"] == {"effort": "xhigh"}
                assert payload["store"] is False
                assert payload["text"]["format"]["strict"] is True
                body = {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok":true}'}],
                        }
                    ],
                }
            else:
                assert request.url.path == "/v1/chat/completions"
                assert payload["reasoning_effort"] == "xhigh"
                assert payload["response_format"]["json_schema"]["strict"] is True
                body = {
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]
                }
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = Settings(
                _env_file=None,
                llm_api_mode=mode,
                llm_api_key=SecretStr("private-key"),
                llm_base_url="https://example.test/v1/",
            )
            assert await LlmClient(settings, client).structured("说明", "测试", {}) == {"ok": True}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "upstream-private-secret"}, 502),
        (429, {"error": "upstream-private-secret"}, 429),
        (200, {"status": "incomplete"}, 502),
        (200, {"status": "completed", "output": []}, 502),
        (200, {"status": "completed", "output": [None]}, 502),
    ],
)
def test_upstream_errors_are_safe(status, body, expected):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
        ) as client:
            with pytest.raises(ServiceError) as error:
                await LlmClient(
                    Settings(_env_file=None, llm_api_key=SecretStr("key")), client
                ).structured("", "", {})
            assert error.value.status_code == expected
            assert "upstream-private-secret" not in error.value.detail

    asyncio.run(run())


def test_timeout_and_missing_key():
    async def run():
        def timeout(request):
            raise httpx.ReadTimeout("private connection detail")

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ServiceError) as error:
                await LlmClient(
                    Settings(_env_file=None, llm_api_key=SecretStr("key")), client
                ).structured("", "", {})
            assert error.value.status_code == 504
            with pytest.raises(ServiceError) as error:
                await LlmClient(Settings(_env_file=None), client).structured("", "", {})
            assert error.value.status_code == 503

    asyncio.run(run())


@pytest.mark.parametrize(
    "variant", ["valid", "clarify", "invalid", "missing", "forbidden", "limited"]
)
def test_drafts_are_validated_without_writing_entries(variant):
    async def run():
        draft = {
            "type": "expense",
            "amount": 800,
            "category_id": "food",
            "date": "2026-10-24",
            "time": "08:00",
            "note": "早餐",
            "account": None,
        }
        result = {"drafts": [draft], "clarification": None}
        if variant == "clarify":
            result["clarification"] = "请补充金额"
        if variant == "invalid":
            draft["amount"] = -800
        if variant == "missing":
            draft["amount"] = None
        ledger = SimpleNamespace(
            user_id=uuid4(),
            require_book=AsyncMock(),
            session=SimpleNamespace(rollback=AsyncMock()),
            create_entry=AsyncMock(),
        )
        llm = SimpleNamespace(structured=AsyncMock(return_value=result))
        redis = SimpleNamespace(eval=AsyncMock(return_value=11 if variant == "limited" else 1))
        if variant == "forbidden":
            ledger.require_book.side_effect = ServiceError(404, "账本不存在或无权访问")
        service = AssistantService(ledger, llm, redis, Settings(_env_file=None))
        body = ParseRequest(text="今天早上八点吃早餐8块钱", book_id=uuid4())
        if variant in {"invalid", "forbidden", "limited"}:
            with pytest.raises(ServiceError) as error:
                await service.parse(body)
            assert (
                error.value.status_code
                == {"invalid": 502, "forbidden": 404, "limited": 429}[variant]
            )
            if variant != "invalid":
                llm.structured.assert_not_awaited()
        else:
            parsed = await service.parse(body)
            if variant in {"clarify", "missing"}:
                assert not parsed.drafts and parsed.clarification
            else:
                assert parsed.drafts[0].amount == 800
                assert parsed.drafts[0].time == "08:00"
                assert parsed.drafts[0].account == "微信" and parsed.drafts[0].account_defaulted
                assert parsed.drafts[0].book_id == body.book_id
                assert "Asia/Shanghai" in llm.structured.call_args.args[0]
        ledger.create_entry.assert_not_awaited()

    asyncio.run(run())
