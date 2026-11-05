"""显式验证开发 PostgreSQL 的上下文提交与重开；全部测试数据在外层事务回滚。"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.chat_v2 import (
    AnswerPreviewV2,
    ContextPatch,
    CreateConversationV2,
    ReferenceRequest,
    SendMessageV2,
)
from app.services.chat import ChatService
from app.services.chat_v2_context_store import ContextStoreV2
from app.services.chat_v2_tools import ChatToolsV2
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_postgresql_context_reopen_reference_and_atomic_save():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("上下文集成验证仅允许开发数据库")
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 10}
    )
    owner, member, book, cid, eid = (uuid4() for _ in range(5))
    turn_ids = []
    today = date(2026, 10, 27)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(
                    insert(User),
                    [
                        {
                            "id": uid,
                            "openid": f"ctx-v2-{uid}",
                            "wechat_app_id": settings.wechat_app_id,
                        }
                        for uid in (owner, member)
                    ],
                )
                await connection.execute(
                    insert(Book).values(id=book, owner_id=owner, kind="shared", name="上下文测试")
                )
                await connection.execute(
                    insert(BookMember),
                    [
                        dict(book_id=book, user_id=uid, role=role, name="测试人", status="active")
                        for uid, role in ((owner, "owner"), (member, "member"))
                    ],
                )
                await connection.execute(
                    insert(LedgerEntry).values(
                        id=eid,
                        book_id=book,
                        created_by=member,
                        type="expense",
                        category_id="coffee",
                        amount=1500,
                        date=today,
                        note="奶茶",
                        account="微信",
                        is_deleted=False,
                    )
                )
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    ledger = LedgerService(session, owner)
                    store = ContextStoreV2(ledger, settings, today=today)
                    await store.create(
                        CreateConversationV2(id=cid, book_id=book, initial_month="2026-10")
                    )
                    stale = await store.revise(cid, ContextPatch())
                    edits = [
                        {"field": "keywords.include", "op": "replace", "value": ["奶茶"]},
                        {"field": "keywords.include", "op": "add", "value": ["咖啡"]},
                        {"field": "keywords.include", "op": "replace", "value": ["咖啡"]},
                        {"field": "keywords.include", "op": "clear"},
                        {"field": "categoryIds", "op": "replace", "value": ["coffee"]},
                    ]
                    for index, edit in enumerate(edits):
                        draft = await store.revise(
                            cid, ContextPatch.model_validate({"edits": [edit]})
                        )
                        body = SendMessageV2(id=uuid4(), text=f"修正条件 {index}")
                        turn_ids.append(body.id)
                        turn = await store.save_success(
                            draft,
                            body,
                            AnswerPreviewV2(answer="已核对范围。", completeness="complete"),
                        )
                    assert turn.sequence == 5
                    with pytest.raises(ServiceError, match="其他请求"):
                        await store.save_success(
                            stale,
                            SendMessageV2(id=uuid4(), text="过期轮次"),
                            AnswerPreviewV2(answer="过期结果", completeness="complete"),
                        )
                    draft = await store.revise(cid, ContextPatch())
                    with pytest.raises(ServiceError, match="未完成"):
                        await store.save_success(
                            draft,
                            SendMessageV2(id=uuid4(), text="失败轮次"),
                            AnswerPreviewV2(
                                answer="未完成", completeness="partial", missing=["明细"]
                            ),
                        )
                    result = await ChatToolsV2(ledger, settings, today=today).execute(
                        "q1",
                        book,
                        "search_entries",
                        {"filters": draft.context.filters.model_dump()},
                    )
                    assert result.status == "complete"
                    body = SendMessageV2(id=uuid4(), text="查看这笔")
                    turn_ids.append(body.id)
                    preview = AnswerPreviewV2(
                        answer="已找到对应账单。",
                        completeness="complete",
                        cards=result.cards,
                        fact_refs=result.fact_refs,
                    )
                    last = await store.save_success(draft, body, preview)
                    assert await store.save_success(draft, body, preview) == last
                    legacy = ChatService(ledger, None, None, settings)
                    assert await legacy.list(book, "2026-10") == []
                    with pytest.raises(ServiceError):
                        await legacy.require_conversation(cid)
                # 使用新的 Session 重新读取，确保验证的是数据库持久化而非身份映射缓存。
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    ledger = LedgerService(session, owner)
                    store = ContextStoreV2(ledger, settings, today=today)
                    loaded = await store.load(cid)
                    assert loaded.version == 6 and loaded.context.filters.category_ids == ["coffee"]
                    assert loaded.context.summary_through_sequence == 6
                    assert len(loaded.recent_turns) == 6
                    await session.execute(
                        update(LedgerEntry)
                        .where(LedgerEntry.id == eid)
                        .values(amount=2500, note="更新后的奶茶")
                    )
                    await session.commit()
                    resolved = await store.resolve_reference(cid, ReferenceRequest())
                    assert resolved.status == "resolved"
                    assert resolved.candidates[0].label == "更新后的奶茶"
                    fresh = await ChatToolsV2(ledger, settings, today=today).execute(
                        "q2", book, "get_entry", {"entryId": str(eid)}
                    )
                    assert (
                        next(f.value for f in fresh.fact_refs if f.id == "q2.expense.total") == 2500
                    )
                    assert any(f.value == 1500 for f in loaded.recent_turns[-1].fact_refs)
                    foreign = ContextStoreV2(LedgerService(session, member), settings, today=today)
                    with pytest.raises(ServiceError):
                        await foreign.load(cid)
                    await session.execute(
                        update(BookMember)
                        .where(BookMember.book_id == book, BookMember.user_id == owner)
                        .values(status="removed")
                    )
                    await session.commit()
                    with pytest.raises(ServiceError):
                        await store.load(cid)
            finally:
                await outer.rollback()
            for model, condition in (
                (User, User.id.in_([owner, member])),
                (Book, Book.id == book),
                (LedgerEntry, LedgerEntry.id == eid),
                (ChatConversation, ChatConversation.id == cid),
                (ChatTurn, ChatTurn.id.in_(turn_ids)),
            ):
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(model).where(condition)
                    )
                    == 0
                )
    finally:
        await engine.dispose()
