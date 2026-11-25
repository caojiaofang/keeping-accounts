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
            session=SimpleNamespace(rollback=AsyncMock(), scalars=AsyncMock(return_value=[])),
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


def synthetic_payment_messages():
    """三张完整支付卡片加底部残缺卡片，不包含用户截图或个人信息。"""
    today = business_today()
    image = Image.new("RGB", (1080, 2400), "#f5f5f5")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = (
        ImageFont.truetype(str(font_path), 36) if font_path.exists() else ImageFont.load_default(36)
    )
    large = (
        ImageFont.truetype(str(font_path), 76) if font_path.exists() else ImageFont.load_default(76)
    )
    draw.text((55, 25), "测试截图 / 支付消息", fill="black", font=font)
    previous = [today - timedelta(days=2), today - timedelta(days=3)]
    rows = [
        ("测试餐饮甲", "上午 11:42", "11.00"),
        ("测试超市", f"{previous[0].month}月{previous[0].day}日 下午 6:05", "12.90"),
        ("测试餐饮乙", f"{previous[1].month}月{previous[1].day}日 上午 11:39", "15.29"),
    ]
    for index, (merchant, stamp, amount) in enumerate(rows):
        top = 120 + index * 690
        draw.rounded_rectangle((25, top, 1055, top + 660), radius=28, fill="white")
        draw.text((65, top + 35), merchant, fill="black", font=font)
        draw.text((65, top + 95), stamp, fill="#666666", font=font)
        draw.text((390, top + 220), "付款成功", fill="#666666", font=font)
        draw.text((355, top + 290), "¥" + amount, fill="black", font=large)
        draw.text((65, top + 470), "付款方式：支付宝亲情卡", fill="#666666", font=font)
        draw.text((65, top + 570), "支付奖励：领2倍积分", fill="#666666", font=font)
    draw.rounded_rectangle((25, 2220, 1055, 2600), radius=28, fill="white")
    draw.text((65, 2255), "测试生鲜（底部被截断）", fill="black", font=font)
    draw.text((65, 2320), "11月12日 晚上7:14", fill="#666666", font=font)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def test_real_model_partial_payment_message_image():
    async def run():
        settings = Settings(llm_timeout_seconds=90)
        if not settings.llm_api_key:
            pytest.skip("未配置后端模型密钥")
        ledger = SimpleNamespace(
            user_id=uuid4(),
            require_book=AsyncMock(),
            session=SimpleNamespace(rollback=AsyncMock(), scalars=AsyncMock(return_value=[])),
            create_entry=AsyncMock(),
        )
        async with httpx.AsyncClient() as client:
            service = AssistantService(
                ledger,
                LlmClient(settings, client),
                SimpleNamespace(eval=AsyncMock(return_value=1)),
                settings,
            )
            response = await service.record(
                RecordRequest(book_id=uuid4(), image=synthetic_payment_messages())
            )
        assert not response.clarification and len(response.drafts) == 3
        by_amount = {draft.amount: draft for draft in response.drafts}
        assert set(by_amount) == {1100, 1290, 1529}
        assert by_amount[1100].date == business_today() and by_amount[1100].time == "11:42"
        assert by_amount[1290].date == business_today() - timedelta(days=2)
        assert by_amount[1290].time == "18:05"
        assert by_amount[1529].date == business_today() - timedelta(days=3)
        assert by_amount[1529].time == "11:39"
        assert all(draft.type == "expense" for draft in response.drafts)
        assert "今天" in response.reply and "跳过" in response.reply
        ledger.create_entry.assert_not_awaited()
        print(
            "真实模型：提取三笔完整交易，忽略底部残缺卡片与奖励积分，并提示日期补全。", flush=True
        )

    asyncio.run(run())
