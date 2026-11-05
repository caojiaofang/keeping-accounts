"""PostgreSQL 工具闭环；显式开发库开关、随机测试身份和事务回滚，不调用模型。"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.services.chat_v2_tools import ChatToolsV2
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_postgresql_tool_roundtrip_and_keyset_pages():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("查账工具集成验证仅允许开发数据库")
    settings = settings.model_copy(
        update={"chat_v2_cursor_secret": SecretStr("test-only-key-" * 4)}
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 10}
    )
    owner, member, book = uuid4(), uuid4(), uuid4()
    entry_ids = [uuid4() for _ in range(48)]
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(
                    insert(User),
                    [
                        {
                            "id": uid,
                            "openid": f"tools-v2-{uid}",
                            "wechat_app_id": settings.wechat_app_id,
                        }
                        for uid in (owner, member)
                    ],
                )
                await connection.execute(
                    insert(Book).values(id=book, owner_id=owner, kind="shared", name="V2 工具测试")
                )
                await connection.execute(
                    insert(BookMember),
                    [
                        {
                            "book_id": book,
                            "user_id": uid,
                            "role": role,
                            "name": "工具测试人",
                            "status": "active",
                        }
                        for uid, role in ((owner, "owner"), (member, "member"))
                    ],
                )
                rows = [
                    dict(
                        id=eid,
                        book_id=book,
                        created_by=member,
                        type="expense",
                        category_id="coffee",
                        amount=100,
                        date=date(2026, 10, 26),
                        time=None if index % 2 else "12:00",
                        note="奶茶",
                        account="微信",
                        is_deleted=False,
                    )
                    for index, eid in enumerate(entry_ids[:45])
                ]
                rows += [
                    {**rows[0], "id": entry_ids[45], "amount": 700, "date": date(2026, 10, 14)},
                    {
                        **rows[0],
                        "id": entry_ids[46],
                        "amount": 900,
                        "type": "income",
                        "category_id": "refund",
                    },
                    {**rows[0], "id": entry_ids[47], "amount": 99999, "is_deleted": True},
                ]
                await connection.execute(insert(LedgerEntry), rows)
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    runner = ChatToolsV2(
                        LedgerService(session, owner), settings, today=date(2026, 10, 27)
                    )
                    filters = {"dateRange": {"start": "2026-10-21", "end": "2026-10-27"}}

                    async def call(name, cid="q1", **arguments):
                        result = await runner.execute(cid, book, name, arguments)
                        assert result.status == "complete", result.model_dump_json()
                        return result

                    for group in (None, "category", "date", "account", "creator"):
                        result = await call("summarize_entries", filters=filters, groupBy=group)
                        values = {fact.id: fact.value for fact in result.fact_refs}
                        assert (
                            values["q1.expense.total"] == 4500 and values["q1.expense.count"] == 45
                        )
                    for sort in ("date_desc", "amount_desc"):
                        seen, cursor = [], None
                        for index in range(3):
                            result = await call(
                                "search_entries",
                                f"p{index}",
                                filters=filters,
                                sort=sort,
                                cursor=cursor,
                            )
                            seen += [row.entry_id for row in result.cards[0].rows]
                            cursor = result.next_cursor
                            assert bool(cursor) == (index < 2)
                        assert len(seen) == len(set(seen)) == 45
                        assert set(seen) == set(entry_ids[:45])
                    for group in ("category", "date", "creator"):
                        compared = await call(
                            "compare_periods",
                            filters=filters,
                            groupBy=group,
                            previousRange={"start": "2026-10-14", "end": "2026-10-20"},
                        )
                        values = {fact.id: fact.value for fact in compared.fact_refs}
                        assert values["q1.expense.previous_total"] == 700
                        assert values["q1.expense.difference"] == 3800
                    detail = await call("get_entry", entryId=str(entry_ids[0]))
                    assert detail.cards[0].rows[0].entry_id == entry_ids[0]
                    both = await call("summarize_entries", filters={**filters, "entryType": "both"})
                    values = {fact.id: fact.value for fact in both.fact_refs}
                    assert values["q1.expense.total"] == 4500 and values["q1.income.total"] == 900
            finally:
                await outer.rollback()
            assert (
                await connection.scalar(
                    select(func.count()).select_from(User).where(User.id.in_([owner, member]))
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.id.in_(entry_ids))
                )
                == 0
            )
    finally:
        await engine.dispose()
