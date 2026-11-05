"""显式启用的 PostgreSQL 筛选验证；只用开发库，所有临时数据最终回滚。"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.chat_v2 import CompareArgs, DateRange, EntryFilter
from app.services.analysis_v2 import FilterQueriesV2
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_postgresql_filters_permissions_and_literal_search():
    asyncio.run(roundtrip())


async def roundtrip():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or settings.database_url is None:
        raise RuntimeError("V2 筛选集成测试仅允许使用开发数据库")
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 10}
    )
    owner, member, outsider = uuid4(), uuid4(), uuid4()
    book, foreign_book = uuid4(), uuid4()
    milk, coffee, literal, foreign_entry, previous = (uuid4() for _ in range(5))
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                for uid in (owner, member, outsider):
                    await connection.execute(
                        insert(User).values(
                            id=uid,
                            openid=f"analysis-v2-test-{uid}",
                            wechat_app_id=settings.wechat_app_id,
                        )
                    )
                await connection.execute(
                    insert(Book),
                    [
                        {
                            "id": book,
                            "owner_id": owner,
                            "name": "V2 临时共享账本",
                            "kind": "shared",
                        },
                        {
                            "id": foreign_book,
                            "owner_id": outsider,
                            "name": "V2 临时隔离账本",
                            "kind": "personal",
                        },
                    ],
                )
                for uid, role in ((owner, "owner"), (member, "member")):
                    await connection.execute(
                        insert(BookMember).values(
                            book_id=book, user_id=uid, name="临时记账人", role=role, status="active"
                        )
                    )
                for eid, bid, creator, amount, day, note in (
                    (milk, book, member, 1000, 26, "奶茶"),
                    (coffee, book, owner, 2000, 25, "COFFEE 咖啡"),
                    (literal, book, owner, 20000, 24, "折扣100%_到手/券\\票"),
                    (foreign_entry, foreign_book, outsider, 99999, 24, "奶茶"),
                    (previous, book, member, 700, 14, "奶茶"),
                ):
                    await connection.execute(
                        insert(LedgerEntry).values(
                            id=eid,
                            book_id=bid,
                            created_by=creator,
                            type="expense",
                            category_id="coffee",
                            amount=amount,
                            date=date(2026, 10, day),
                            note=note,
                            account="微信",
                        )
                    )
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    queries = FilterQueriesV2(
                        LedgerService(session, owner), settings, today=date(2026, 10, 27)
                    )

                    def filters(**changes):
                        return EntryFilter.model_validate(
                            {"dateRange": {"start": "2026-10-21", "end": "2026-10-27"}, **changes}
                        )

                    async def found(**changes):
                        prepared = await queries.prepare(book, filters(**changes))
                        return {
                            row.id for row in await session.scalars(prepared.entries_statement())
                        }

                    assert await found(keywords={"include": ["奶茶", "咖啡"]}) == {milk, coffee}
                    assert (
                        await found(keywords={"include": ["奶茶", "咖啡"], "mode": "all"}) == set()
                    )
                    assert await found(keywords={"include": ["coffee"]}) == {coffee}
                    for word in ("%", "_", "/", "\\", "%_"):
                        assert await found(keywords={"include": [word]}) == {literal}
                    assert await found(keywords={"include": ["' OR 1=1 --"]}) == set()
                    assert await found(weekdays=[6, 7]) == {coffee, literal}
                    assert await found(
                        accounts=["微信"],
                        creatorIds=[member],
                        amountMin=999,
                        excludedEntryIds=[coffee],
                    ) == {milk}
                    prepared = await queries.prepare(book, filters())
                    total, count = (
                        await session.execute(
                            select(func.sum(LedgerEntry.amount), func.count())
                            .join(Book, Book.id == LedgerEntry.book_id)
                            .where(*prepared.conditions)
                        )
                    ).one()
                    assert (int(total), count) == (23000, 3)
                    current, earlier = await queries.prepare_compare(
                        book,
                        CompareArgs(
                            filters=filters(creatorIds=[member]),
                            previous_range=DateRange(start="2026-10-14", end="2026-10-20"),
                        ),
                    )
                    assert {
                        row.id for row in await session.scalars(current.entries_statement())
                    } == {milk}
                    assert {
                        row.id for row in await session.scalars(earlier.entries_statement())
                    } == {previous}
                    for changes in (
                        {"creatorIds": [outsider]},
                        {"excludedEntryIds": [foreign_entry]},
                        {"excludedEntryIds": [uuid4()]},
                    ):
                        with pytest.raises(ServiceError) as error:
                            await queries.prepare(book, filters(**changes))
                        assert error.value.status_code == 404
            finally:
                await outer.rollback()
            # 回滚后通过另一个事务检查本次用户和账单没有残留，不扫描其他用户数据。
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.id.in_([owner, member, outsider]))
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.id.in_([milk, coffee, literal, foreign_entry, previous]))
                )
                == 0
            )
    finally:
        await engine.dispose()
