"""使用独立事务验证等待行锁后的权限重验；只提交并清理本测试专用 UUID 数据。"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookActivity, BookInvitation, BookMember
from app.models.user import User
from app.schemas.ledger import CreateEntry
from app.schemas.shared import CreateInvitation
from app.services.ledger import LedgerService
from app.services.shared import SharedBookService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用开发库并发测试",
    ),
]


def test_remove_and_revoke_win_over_waiting_requests():
    asyncio.run(races())


async def races():
    settings = Settings()
    if settings.app_env not in {"development", "test"} or not settings.database_url:
        raise RuntimeError("并发测试只允许开发库")
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"server_settings": {"statement_timeout": "15000"}},
    )
    users, book_id = [uuid4(), uuid4()], uuid4()
    try:
        async with engine.begin() as connection:
            for user in users:
                await connection.execute(
                    insert(User).values(
                        id=user, wechat_app_id=settings.wechat_app_id, openid=f"shared-race-{user}"
                    )
                )
            await connection.execute(
                insert(Book).values(id=book_id, owner_id=users[0], kind="shared", name="并发测试")
            )
            for index, user in enumerate(users):
                await connection.execute(
                    insert(BookMember).values(
                        book_id=book_id,
                        user_id=user,
                        name="测试成员",
                        role="owner" if index == 0 else "member",
                    )
                )

        async def race(write, read, expected):
            locked, release, pid_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
            member_pid = None

            async def writer():
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    await LedgerService(session, users[0]).require_book(book_id, write=True)
                    await write(session)
                    locked.set()
                    await release.wait()
                    await session.commit()

            async def reader():
                nonlocal member_pid
                await locked.wait()
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    member_pid = await session.scalar(select(func.pg_backend_pid()))
                    pid_ready.set()
                    try:
                        await read(session)
                    except ServiceError as error:
                        assert error.status_code == expected
                        return
                    raise AssertionError("等待行锁的旧请求没有被拒绝")

            write_task, read_task = asyncio.create_task(writer()), asyncio.create_task(reader())
            try:
                await asyncio.wait_for(pid_ready.wait(), timeout=12)
                blocked = False
                async with engine.connect() as connection:
                    for _ in range(20):
                        blockers = await connection.scalar(
                            select(func.pg_blocking_pids(member_pid))
                        )
                        if blockers:
                            blocked = True
                            break
                        await asyncio.sleep(0.05)
                assert blocked, "未观察到请求等待账本行锁"
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(write_task, read_task), timeout=20)

        async def remove(session):
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == book_id, BookMember.user_id == users[1])
                .values(status="removed")
            )
            await session.execute(
                update(Book).where(Book.id == book_id).values(version=Book.version + 1)
            )

        async def save(session):
            await LedgerService(session, users[1]).create_entry(
                CreateEntry(
                    id=uuid4(),
                    book_id=book_id,
                    type="expense",
                    category_id="food",
                    amount=800,
                    date="2026-10-24",
                    account="微信",
                )
            )

        await race(remove, save, 404)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            invitation = await SharedBookService(session, settings, users[0]).create_invitation(
                book_id, CreateInvitation(id=uuid4())
            )

        async def revoke(session):
            await session.execute(
                update(BookInvitation)
                .where(BookInvitation.id == invitation.id)
                .values(revoked_at=datetime.now(UTC))
            )
            await session.execute(
                update(Book).where(Book.id == book_id).values(version=Book.version + 1)
            )

        async def accept(session):
            await SharedBookService(session, settings, users[1]).accept(
                invitation.token, "测试成员"
            )

        await race(revoke, accept, 410)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.book_id == book_id)
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(BookMember.status).where(
                        BookMember.book_id == book_id, BookMember.user_id == users[1]
                    )
                )
                == "removed"
            )
    finally:
        async with engine.begin() as connection:
            for model in (BookActivity, BookInvitation, BookMember, LedgerEntry):
                await connection.execute(delete(model).where(model.book_id == book_id))
            await connection.execute(delete(Book).where(Book.id == book_id))
            await connection.execute(delete(User).where(User.id.in_(users)))
        await engine.dispose()
