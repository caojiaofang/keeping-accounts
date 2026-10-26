"""在真实开发库事务内验证分页、统计、过滤和只读查询，最后回滚全部数据。"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.services.admin import SHANGHAI, AdminService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用开发库集成测试"
    ),
]


def test_admin_queries_on_real_postgresql():
    asyncio.run(roundtrip())


async def roundtrip():
    config = Settings()
    if config.app_env not in {"development", "test"} or not config.database_url:
        raise RuntimeError("管理端集成测试只允许开发数据库")
    engine = create_async_engine(
        config.database_url.get_secret_value(), connect_args={"timeout": 10}
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    service = AdminService(session)
                    before = await service.overview()
                    owner, member, removed = uuid4(), uuid4(), uuid4()
                    personal, shared, closed = uuid4(), uuid4(), uuid4()
                    today = datetime.now(SHANGHAI).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    yesterday = today - timedelta(seconds=1)
                    for user_id in (owner, member, removed):
                        await session.execute(
                            insert(User).values(
                                id=user_id,
                                wechat_app_id=config.wechat_app_id,
                                openid=f"admin-test-{user_id}",
                                created_at=yesterday,
                                last_login_at=datetime.now(UTC),
                            )
                        )
                    for book_id, kind, status, name in (
                        (personal, "personal", "active", f"admin-{owner}-个人"),
                        (shared, "shared", "active", f"admin-{owner}-%_共同"),
                        (closed, "shared", "closed", f"admin-{owner}-解散"),
                    ):
                        await session.execute(
                            insert(Book).values(
                                id=book_id,
                                owner_id=owner,
                                kind=kind,
                                status=status,
                                name=name[-24:],
                            )
                        )
                    for user_id, role, status in (
                        (owner, "owner", "active"),
                        (member, "member", "active"),
                        (removed, "member", "removed"),
                    ):
                        await session.execute(
                            insert(BookMember).values(
                                book_id=shared,
                                user_id=user_id,
                                name="测试成员",
                                role=role,
                                status=status,
                            )
                        )
                    for book_id, creator, deleted, created in (
                        (personal, owner, False, yesterday),
                        (shared, member, False, today),
                        (shared, owner, True, today),
                    ):
                        await session.execute(
                            insert(LedgerEntry).values(
                                id=uuid4(),
                                book_id=book_id,
                                created_by=creator,
                                type="expense",
                                category_id="food",
                                amount=100,
                                date=today.date(),
                                account="默认账户",
                                is_deleted=deleted,
                                created_at=created,
                            )
                        )
                    after = await service.overview()
                    assert after.users == before.users + 3
                    assert after.books == before.books + 2
                    assert after.shared_books == before.shared_books + 1
                    assert after.entries == before.entries + 2
                    assert after.new_users_today == before.new_users_today
                    assert after.new_entries_today == before.new_entries_today + 1
                    assert len(after.trend) == 14
                    assert after.trend[-2].users == before.trend[-2].users + 3
                    assert after.trend[-1].entries == before.trend[-1].entries + 1
                    user = await service.user(owner)
                    assert user.book_count == 2 and user.entry_count == 1
                    assert "openid" not in user.model_dump()
                    users = await service.users(str(owner).upper(), 1, 20)
                    assert users.total == 1 and users.items[0].id == owner
                    assert (await service.users("%", 1, 20)).total == 0
                    first = await service.books("", None, None, owner, 1, 2)
                    second = await service.books("", None, None, owner, 2, 2)
                    assert first.total == 3 and len(first.items) == 2 and len(second.items) == 1
                    assert not {book.id for book in first.items} & {
                        book.id for book in second.items
                    }
                    filtered = await service.books("%_", "shared", "active", owner, 1, 20)
                    assert filtered.total == 1 and filtered.items[0].id == shared
                    assert filtered.items[0].entry_count == 1
                    assert filtered.items[0].member_count == 2
                    detail = await service.book(shared)
                    assert len(detail.members) == 3
                    assert "note" not in detail.model_dump() and "budget" not in detail.model_dump()
                    for method in (service.user, service.book):
                        with pytest.raises(ServiceError) as error:
                            await method(uuid4())
                        assert error.value.status_code == 404
                    assert not session.new and not session.dirty and not session.deleted
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
