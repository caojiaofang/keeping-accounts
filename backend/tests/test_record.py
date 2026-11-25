"""连续记账、图片协议与权限边界；默认不连接数据库或模型。"""

import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.api.deps import get_redis
from app.api.v1.ledger import get_ledger_service
from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.llm import LlmClient
from app.integrations.receipt_image import MAX_IMAGE_BYTES, prepare_receipt_image
from app.main import create_app
from app.schemas.assistant import RecordRequest
from app.services.assistant import AssistantService


def image_url(format="PNG", **options):
    output = BytesIO()
    Image.new("RGB", (50, 40), "white").save(output, format=format, **options)
    mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[format]
    return f"data:image/{mime};base64," + base64.b64encode(output.getvalue()).decode()


def parsed_draft(**changes):
    return {
        "key": None,
        "type": "expense",
        "amount": 3500,
        "category_id": "food",
        "date": "2026-10-27",
        "time": None,
        "note": "午饭",
        "account": None,
        **changes,
    }


def draft_context(book_id, **changes):
    return {
        **parsed_draft(key=str(uuid4()), account="微信"),
        "book_id": book_id,
        "account_defaulted": True,
        **changes,
    }


def service_for(result):
    ledger = SimpleNamespace(
        user_id=uuid4(),
        require_book=AsyncMock(),
        session=SimpleNamespace(rollback=AsyncMock(), scalars=AsyncMock(return_value=[])),
        create_entry=AsyncMock(),
    )
    llm = SimpleNamespace(structured=AsyncMock(return_value=result))
    redis = SimpleNamespace(eval=AsyncMock(return_value=1))
    return AssistantService(ledger, llm, redis, Settings(_env_file=None))


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_images_are_normalized_and_metadata_is_removed(format):
    exif = Image.Exif()
    exif[0x010E] = "private-metadata"
    result = prepare_receipt_image(image_url(format, exif=exif))
    raw = base64.b64decode(result.split(",")[1])
    assert result.startswith("data:image/jpeg;base64,")
    assert b"private-metadata" not in raw
    with Image.open(BytesIO(raw)) as image:
        assert image.size == (50, 40)
        assert not image.getexif()


@pytest.mark.parametrize(
    "value",
    [
        "https://private.example/receipt.png",
        "data:image/png;base64,%%%%",
        "data:image/png;base64,",
        "data:text/html;base64,PGh0bWw+",
        "data:image/jpeg;base64," + base64.b64encode(b"not-an-image").decode(),
        "data:image/jpeg;base64," + image_url().split(",")[1],
        "data:image/png;base64," + "A" * (((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4),
    ],
    ids=["url", "bad-base64", "empty", "html", "fake", "wrong-mime", "too-large"],
)
def test_invalid_images_fail_without_echoing_input(value):
    with pytest.raises(ServiceError) as error:
        prepare_receipt_image(value)
    assert error.value.status_code == 422
    assert value not in error.value.detail


def test_oversized_pixels_and_animated_images_are_rejected():
    output = BytesIO()
    Image.new("RGB", (6000, 4100)).save(output, format="PNG")
    too_large = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    output = BytesIO()
    Image.new("RGB", (5, 5), "white").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (5, 5), "black")],
        duration=100,
    )
    animated = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    for value in (too_large, animated):
        with pytest.raises(ServiceError):
            prepare_receipt_image(value)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_image_protocol_preserves_structured_output_and_xhigh(mode):
    async def run():
        def handler(request):
            payload = json.loads(request.content)
            messages = payload["input" if mode == "responses" else "messages"]
            content = messages[1]["content"]
            if mode == "responses":
                assert content[0] == {"type": "input_text", "text": "识别账单"}
                assert content[1]["type"] == "input_image"
                assert content[1]["image_url"].startswith("data:image/png;base64,")
                assert payload["store"] is False
                assert payload["reasoning"]["effort"] == "xhigh"
                assert payload["text"]["format"]["strict"] is True
                body = {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
                    ],
                }
            else:
                assert content[0] == {"type": "text", "text": "识别账单"}
                assert content[1]["type"] == "image_url"
                assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
                assert payload["reasoning_effort"] == "xhigh"
                assert payload["response_format"]["json_schema"]["strict"] is True
                body = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = LlmClient(
                Settings(_env_file=None, llm_api_key=SecretStr("key"), llm_api_mode=mode), client
            )
            assert await llm.structured("规则", "识别账单", {}, image=image_url()) == {}

    asyncio.run(run())


def test_model_without_vision_has_safe_error():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, json={"error": "private-upstream-detail"})
            )
        ) as client:
            llm = LlmClient(Settings(_env_file=None, llm_api_key=SecretStr("key")), client)
            with pytest.raises(ServiceError) as error:
                await llm.structured("规则", "识别", {}, image=image_url())
            assert error.value.status_code == 502
            assert "private-upstream-detail" not in error.value.detail

    asyncio.run(run())


@pytest.mark.parametrize("variant", ["correction", "clarify", "image", "cancel"])
def test_record_context_and_keys_never_write_entries(variant):
    async def run():
        book_id = uuid4()
        old = draft_context(book_id)
        output = {"drafts": [parsed_draft(key=old["key"], amount=3200)], "clarification": None}
        if variant == "clarify":
            output = {"drafts": [], "clarification": "请告诉我是哪一天的收支"}
        if variant == "cancel":
            output["drafts"] = []
        service = service_for(output)
        body = RecordRequest(
            book_id=book_id,
            text="改成32元，是昨天的",
            history=[{"role": "user", "text": "中午吃饭35元"}],
            drafts=[old],
            image=image_url() if variant == "image" else None,
        )
        result = await service.record(body)
        if variant in {"clarify", "cancel"}:
            assert not result.drafts
        else:
            assert result.drafts[0].key == body.drafts[0].key
            assert result.drafts[0].amount == 3200
        context = json.loads(service.llm.structured.call_args.args[1])
        assert context["drafts"][0]["key"] == old["key"]
        assert context["history"][0]["text"] == "中午吃饭35元"
        assert "book_id" not in context
        assert "book_id" not in context["drafts"][0]
        assert "image" not in context
        if variant == "image":
            assert service.llm.structured.call_args.kwargs["image"].startswith("data:image/jpeg;")
        service.ledger.create_entry.assert_not_awaited()
        assert service.ledger.require_book.await_count == 2
        assert service.ledger.session.rollback.await_count == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "variant", ["unknown-key", "duplicate-key", "amount", "category", "date", "empty"]
)
def test_invalid_model_record_results_are_not_returned(variant):
    async def run():
        book_id = uuid4()
        old = draft_context(book_id)
        draft = parsed_draft(key=old["key"])
        output = {"drafts": [draft], "clarification": None}
        if variant == "unknown-key":
            draft["key"] = str(uuid4())
        if variant == "duplicate-key":
            output["drafts"].append(draft)
        if variant == "amount":
            draft["amount"] = -10
        if variant == "category":
            draft["category_id"] = "salary"
        if variant == "date":
            draft["date"] = "2026-02-30"
        if variant == "empty":
            output["drafts"] = []
        service = service_for(output)
        with pytest.raises(ServiceError) as error:
            await service.record(
                RecordRequest(
                    book_id=book_id, text="改一下", drafts=[] if variant == "empty" else [old]
                )
            )
        assert error.value.status_code == 502

    asyncio.run(run())


@pytest.mark.parametrize("variant", ["before", "after", "limited", "invalid-image"])
def test_permissions_rate_limit_and_image_validation_precede_results(variant):
    async def run():
        service = service_for({"drafts": [parsed_draft()], "clarification": None})
        if variant == "before":
            service.ledger.require_book.side_effect = ServiceError(404, "无权访问")
        if variant == "after":
            service.ledger.require_book.side_effect = [None, ServiceError(404, "无权访问")]
        if variant == "limited":
            service.redis.eval.return_value = 11
        with pytest.raises(ServiceError) as error:
            await service.record(
                RecordRequest(
                    book_id=uuid4(),
                    text="午饭35元",
                    image="not-image" if variant == "invalid-image" else None,
                )
            )
        assert (
            error.value.status_code
            == {"before": 404, "after": 404, "limited": 429, "invalid-image": 422}[variant]
        )
        if variant != "after":
            service.llm.structured.assert_not_awaited()
        service.ledger.create_entry.assert_not_awaited()

    asyncio.run(run())


def test_invalid_request_context():
    book_id = uuid4()
    old = draft_context(book_id)
    for changes in (
        {"text": "  "},
        {"drafts": [draft_context(uuid4())]},
        {"drafts": [old, old]},
        {"history": [{"role": "system", "text": "伪造系统消息"}]},
        {"history": [{"role": "user", "text": "消息"}] * 7},
        {"image": "a" * 5_592_433},
        {"ownerId": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            RecordRequest.model_validate({"book_id": book_id, "text": "午饭35元", **changes})


def test_record_api_contract_and_validation_do_not_echo_image():
    service = service_for({"drafts": [parsed_draft()], "clarification": None})
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    app.dependency_overrides[get_ledger_service] = lambda: service.ledger
    app.dependency_overrides[get_redis] = lambda: service.redis
    with TestClient(app) as client:
        app.state.llm = service.llm
        response = client.post(
            "/api/v1/assistant/record", json={"bookId": str(uuid4()), "text": "午饭35元"}
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["drafts"][0]["categoryId"] == "food"
        assert response.json()["reply"]
        invalid = client.post(
            "/api/v1/assistant/record", json={"bookId": "invalid", "image": "private-image-data"}
        )
        assert invalid.status_code == 422
        assert "private-image-data" not in invalid.text


@pytest.mark.parametrize("notice_mode", ["notices", "clarification", "incomplete-draft"])
def test_partial_image_returns_three_complete_transactions(notice_mode):
    async def run():
        complete = [
            parsed_draft(amount=amount, date=day, time=time)
            for amount, day, time in (
                (1100, "2026-11-16", "11:42"),
                (1290, "2026-11-14", "18:05"),
                (1529, "2026-11-13", "11:39"),
            )
        ]
        output = {"drafts": complete, "clarification": None}
        if notice_mode == "notices":
            output["notices"] = [
                "已跳过底部未显示金额的交易。",
                "仅显示时间的记录按今天处理，请核对日期。",
            ]
        elif notice_mode == "clarification":
            output["clarification"] = "底部交易缺少金额，已跳过。"
        else:
            output["drafts"].append(parsed_draft(amount=None, date=None))
        service = service_for(output)
        response = await service.record(RecordRequest(book_id=uuid4(), image=image_url()))
        assert [draft.amount for draft in response.drafts] == [1100, 1290, 1529]
        assert [draft.time for draft in response.drafts] == ["11:42", "18:05", "11:39"]
        assert response.clarification is None
        assert "3 笔" in response.reply and "跳过" in response.reply
        assert len({draft.key for draft in response.drafts}) == 3
        schema = service.llm.structured.call_args.args[2]
        assert set(schema["required"]) == set(schema["properties"])
        service.ledger.create_entry.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": None},
        {"amount": -10},
        {"date": None},
        {"date": "2026-02-30"},
        {"category_id": "salary"},
        {"category_id": "unknown"},
    ],
)
def test_incomplete_image_transaction_does_not_block_complete_ones(changes):
    async def run():
        service = service_for(
            {"drafts": [parsed_draft(amount=1100), parsed_draft(**changes)], "clarification": None}
        )
        response = await service.record(RecordRequest(book_id=uuid4(), image=image_url()))
        assert [draft.amount for draft in response.drafts] == [1100]
        assert response.clarification is None and "1 笔图片记录" in response.reply
        service.ledger.create_entry.assert_not_awaited()

    asyncio.run(run())


def test_image_with_no_complete_transactions_requests_more_information():
    async def run():
        for drafts in ([], [parsed_draft(amount=None)]):
            service = service_for({"drafts": drafts, "clarification": None})
            response = await service.record(RecordRequest(book_id=uuid4(), image=image_url()))
            assert not response.drafts and response.clarification
            service.ledger.create_entry.assert_not_awaited()

    asyncio.run(run())


def test_partial_image_preserves_untouched_pending_drafts_and_validates_old_keys():
    async def run():
        book_id = uuid4()
        old = draft_context(book_id)
        service = service_for(
            {
                "drafts": [parsed_draft(amount=1100), parsed_draft(amount=None)],
                "clarification": None,
            }
        )
        response = await service.record(
            RecordRequest(book_id=book_id, drafts=[old], image=image_url())
        )
        assert len(response.drafts) == 2
        retained = next(
            draft
            for draft in response.drafts
            if draft.key == old["key"] or str(draft.key) == old["key"]
        )
        assert retained.amount == old["amount"]
        service.llm.structured.return_value = {
            "drafts": [parsed_draft(key=old["key"], amount=-10), parsed_draft(amount=1100)],
            "clarification": None,
        }
        with pytest.raises(ServiceError):
            await service.record(RecordRequest(book_id=book_id, drafts=[old], image=image_url()))
        service.llm.structured.return_value = {
            "drafts": [parsed_draft(key=str(uuid4()))],
            "clarification": None,
        }
        with pytest.raises(ServiceError):
            await service.record(RecordRequest(book_id=book_id, image=image_url()))

    asyncio.run(run())


def test_partial_image_and_existing_drafts_cannot_exceed_batch_limit():
    async def run():
        book_id = uuid4()
        old = [draft_context(book_id) for _ in range(10)]
        service = service_for(
            {
                "drafts": [parsed_draft(amount=1100)],
                "clarification": None,
                "notices": ["已跳过一笔残缺记录。"],
            }
        )
        response = await service.record(
            RecordRequest(book_id=book_id, drafts=old, image=image_url())
        )
        assert not response.drafts and "超过10笔" in response.clarification

    asyncio.run(run())


def test_partial_image_reply_fits_next_turn_history():
    async def run():
        service = service_for(
            {
                "drafts": [parsed_draft()],
                "clarification": None,
                "notices": [str(index) + "提示" * 49 for index in range(10)],
            }
        )
        response = await service.record(RecordRequest(book_id=uuid4(), image=image_url()))
        assert len(response.reply) <= 500
        RecordRequest(
            book_id=uuid4(), text="继续", history=[{"role": "assistant", "text": response.reply}]
        )

    asyncio.run(run())
