"""显式启用的模型验收，只用模拟账本与自制小票，不读取真实用户账单。"""

import asyncio
import base64
import os
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings
from app.integrations.llm import LlmClient
from app.schemas.assistant import RecordRequest
from app.services.assistant import AssistantService
from app.services.chat import business_today

pytestmark = pytest.mark.skipif(
    os.getenv("MOJI_RUN_LLM_TESTS") != "1", reason="显式启用后才调用真实模型"
)


def synthetic_receipt():
    image = Image.new("RGB", (900, 1100), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = (
        ImageFont.truetype(str(font_path), 36) if font_path.exists() else ImageFont.load_default(36)
    )
    # 测试票据明确标注测试用途，完全不含真实用户数据。
    lines = [
        "墨记测试小票 / TEST RECEIPT",
        "商家：测试早餐店",
        "状态：支付成功",
        "商品：早餐套餐",
        "原价：15.00 元",
        "优惠：2.50 元",
        "实付：12.50 元",
        "支付方式：微信",
        "支付时间：2026-10-27 08:15",
        "账户余额：100.00 元",
    ]
    for index, line in enumerate(lines):
        draw.text((40, 50 + index * 90), line, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def test_real_model_clarification_correction_and_receipt():
    async def run():
        settings = Settings()
        if not settings.llm_api_key:
            pytest.skip("未配置后端模型密钥")
        ledger = SimpleNamespace(
            user_id=uuid4(),
            require_book=AsyncMock(),
            session=SimpleNamespace(rollback=AsyncMock()),
            create_entry=AsyncMock(),
        )
        book_id = uuid4()
        async with httpx.AsyncClient() as client:
            service = AssistantService(
                ledger,
                LlmClient(settings, client),
                SimpleNamespace(eval=AsyncMock(return_value=1)),
                settings,
            )
            first = await service.record(RecordRequest(book_id=book_id, text="今天午饭花了钱"))
            assert first.clarification and not first.drafts
            print("真实模型：缺金额时要求补充", flush=True)
            second = await service.record(
                RecordRequest(
                    book_id=book_id,
                    text="35元",
                    history=[
                        {"role": "user", "text": "今天午饭花了钱"},
                        {"role": "assistant", "text": first.reply},
                    ],
                )
            )
            assert len(second.drafts) == 1 and second.drafts[0].amount == 3500
            assert second.drafts[0].date == business_today()
            print("真实模型：根据上文补全35元午饭草稿", flush=True)
            context = second.drafts[0].model_dump(exclude={"status"})
            third = await service.record(
                RecordRequest(
                    book_id=book_id,
                    text="改成32元，是昨天的，用支付宝",
                    drafts=[context],
                )
            )
            assert third.drafts[0].key == second.drafts[0].key
            assert third.drafts[0].amount == 3200
            assert third.drafts[0].date == business_today() - timedelta(days=1)
            assert third.drafts[0].account == "支付宝"
            print("真实模型：保留编号并修正金额、日期和账户", flush=True)
            receipt = await service.record(
                RecordRequest(book_id=book_id, image=synthetic_receipt())
            )
            assert len(receipt.drafts) == 1
            assert receipt.drafts[0].amount == 1250
            assert receipt.drafts[0].date.isoformat() == "2026-10-27"
            assert receipt.drafts[0].time == "08:15"
            assert receipt.drafts[0].type == "expense"
            print("真实模型：识别图片实付12.50元，排除原价、优惠和余额", flush=True)
            ledger.create_entry.assert_not_awaited()

    asyncio.run(run())
