"""开发 PostgreSQL 保存/恢复可信回答与建议；模型仅内存模拟，外层事务全部回滚。"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_chat_v2_loop import ANSWER, FILTERS, PLAN, Script, tool

from app.core.config import Settings
from app.integrations.chat_v2_llm import ChatModelV2
from app.integrations.llm import LlmClient
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.user import User
from app.schemas.chat_v2 import CreateConversationV2, SendMessageV2
from app.services.chat_v2_answer import AnswerServiceV2
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.chat_v2_loop import ChatLoopV2
from app.services.chat_v2_suggestions import COMPARE
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库集成测试",
    ),
]


def test_postgresql_answer_and_saved_followup_roundtrip():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("回答集成验证仅允许开发数据库")
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 10}
    )
    # HTTP 全部使用 MockTransport，测试 key 永远不发到外部。
    settings = settings.model_copy(
        update={
            "llm_api_key": SecretStr("private-test-key"),
            "llm_api_mode": "responses",
            "chat_v2_tool_mode": "native",
            "llm_model": "gpt-6-luna",
        }
    )
    owner, book, cid, eid, mid = (uuid4() for _ in range(5))
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(
                    insert(User).values(
                        id=owner, openid=f"answer-v2-{owner}", wechat_app_id=settings.wechat_app_id
                    )
                )
                await connection.execute(
                    insert(Book).values(id=book, owner_id=owner, kind="personal", name="回答测试")
                )
                await connection.execute(
                    insert(LedgerEntry).values(
                        id=eid,
                        book_id=book,
                        created_by=owner,
                        type="expense",
                        category_id="coffee",
                        amount=1500,
                        date=date(2026, 10, 26),
                        note="回答测试奶茶",
                        account="微信",
                        is_deleted=False,
                    )
                )
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    store = ContextStoreV2(
                        LedgerService(session, owner), settings, today=date(2026, 10, 27)
                    )
                    await store.create(
                        CreateConversationV2(id=cid, book_id=book, initial_month="2026-10")
                    )
                    plan = {
                        **PLAN,
                        "patch": {
                            "edits": [
                                {
                                    "field": "dateRange",
                                    "op": "replace",
                                    "value": FILTERS["dateRange"],
                                }
                            ]
                        },
                    }
                    script = Script(settings, [plan, tool(), ANSWER])
                    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as client:
                        loop = ChatLoopV2(store, ChatModelV2(LlmClient(settings, client)))
                        body = SendMessageV2(id=mid, text="最近七天花多少")
                        ready = await AnswerServiceV2(loop).run(cid, body)
                    assert (
                        ready.preview.completeness == "complete"
                        and "¥15.00" in ready.preview.answer
                    )
                    assert not ready.preview.cards and len(ready.preview.suggestions) == 3
                    await store.save_success(ready.draft, body, ready.preview)
                    suggestion = next(s for s in ready.preview.suggestions if s.text == COMPARE)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    store = ContextStoreV2(
                        LedgerService(session, owner), settings, today=date(2026, 10, 27)
                    )
                    restored = await store.load(cid)
                    assert restored.recent_turns[-1].suggestions == ready.preview.suggestions
                    plan = await store.resolve_suggestion(
                        cid,
                        SendMessageV2(
                            id=uuid4(), text=suggestion.text, suggestion_id=suggestion.id
                        ),
                    )
                    draft = await store.revise(cid, plan.patch)
                    assert draft.context.filters.date_range.start == date(2026, 10, 21)
                    assert draft.context.previous_range.start == date(2026, 10, 14)
                    assert (await store.load(cid)).version == 1
            finally:
                await outer.rollback()
            for model, condition in (
                (User, User.id == owner),
                (Book, Book.id == book),
                (LedgerEntry, LedgerEntry.id == eid),
                (ChatConversation, ChatConversation.id == cid),
                (ChatTurn, ChatTurn.id == mid),
            ):
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(model).where(condition)
                    )
                    == 0
                )
    finally:
        await engine.dispose()
