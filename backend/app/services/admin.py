"""只读平台查询，所有列表在数据库分页，过滤已删除账单。"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.admin import (
    AdminBook,
    AdminBookDetail,
    AdminMember,
    AdminUser,
    Overview,
    Page,
    TrendPoint,
)

SHANGHAI = timezone(timedelta(hours=8))


class AdminService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def count(self, model, *conditions) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(model).where(*conditions))
            or 0
        )

    async def overview(self) -> Overview:
        now = datetime.now(UTC)
        today = now.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
        since = today - timedelta(days=13)
        valid_entry = LedgerEntry.is_deleted.is_(False)
        active_book = Book.status == "active"
        users = await self.count(User)
        new_users = await self.count(User, User.created_at >= today)
        active_users = await self.count(User, User.last_login_at >= now - timedelta(days=7))
        books = await self.count(Book, active_book)
        shared = await self.count(Book, active_book, Book.kind == "shared")
        entries = await self.count(LedgerEntry, valid_entry)
        new_entries = await self.count(LedgerEntry, valid_entry, LedgerEntry.created_at >= today)
        series = []
        for model, conditions in ((User, []), (LedgerEntry, [valid_entry])):
            day = func.date(func.timezone("Asia/Shanghai", model.created_at))
            rows = await self.session.execute(
                select(day, func.count())
                .where(model.created_at >= since, *conditions)
                .group_by(day)
            )
            series.append(dict(rows.all()))
        return Overview(
            users=users,
            new_users_today=new_users,
            active_users_7d=active_users,
            books=books,
            shared_books=shared,
            entries=entries,
            new_entries_today=new_entries,
            trend=[
                TrendPoint(
                    date=(since + timedelta(days=i)).date(),
                    users=series[0].get((since + timedelta(days=i)).date(), 0),
                    entries=series[1].get((since + timedelta(days=i)).date(), 0),
                )
                for i in range(14)
            ],
            generated_at=now,
        )

    def user_query(self):
        books = (
            select(func.count())
            .select_from(Book)
            .where(Book.owner_id == User.id, Book.status == "active")
            .correlate(User)
            .scalar_subquery()
        )
        entries = (
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.created_by == User.id, LedgerEntry.is_deleted.is_(False))
            .correlate(User)
            .scalar_subquery()
        )
        return select(
            User.id,
            User.created_at,
            User.last_login_at,
            books.label("book_count"),
            entries.label("entry_count"),
        )

    async def users(self, q: str, page: int, page_size: int) -> Page[AdminUser]:
        conditions = [cast(User.id, String).icontains(q, autoescape=True)] if q else []
        rows = await self.session.execute(
            self.user_query()
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return Page(
            items=[AdminUser(**row) for row in rows.mappings()],
            total=await self.count(User, *conditions),
            page=page,
            page_size=page_size,
        )

    async def user(self, user_id: UUID) -> AdminUser:
        row = (
            (await self.session.execute(self.user_query().where(User.id == user_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise ServiceError(404, "用户不存在")
        return AdminUser(**row)

    def book_query(self):
        entries = (
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.book_id == Book.id, LedgerEntry.is_deleted.is_(False))
            .correlate(Book)
            .scalar_subquery()
        )
        members = (
            select(func.count())
            .select_from(BookMember)
            .where(BookMember.book_id == Book.id, BookMember.status == "active")
            .correlate(Book)
            .scalar_subquery()
        )
        return select(
            Book.id,
            Book.owner_id,
            Book.name,
            Book.kind,
            Book.status,
            Book.created_at,
            entries.label("entry_count"),
            members.label("member_count"),
        )

    async def books(
        self,
        q: str,
        kind: str | None,
        status: str | None,
        owner_id: UUID | None,
        page: int,
        page_size: int,
    ) -> Page[AdminBook]:
        conditions = []
        if q:
            conditions.append(
                Book.name.icontains(q, autoescape=True)
                | cast(Book.id, String).icontains(q, autoescape=True)
            )
        if kind:
            conditions.append(Book.kind == kind)
        if status:
            conditions.append(Book.status == status)
        if owner_id:
            conditions.append(Book.owner_id == owner_id)
        rows = await self.session.execute(
            self.book_query()
            .where(*conditions)
            .order_by(Book.created_at.desc(), Book.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return Page(
            items=[AdminBook(**row) for row in rows.mappings()],
            total=await self.count(Book, *conditions),
            page=page,
            page_size=page_size,
        )

    async def book(self, book_id: UUID) -> AdminBookDetail:
        row = (
            (await self.session.execute(self.book_query().where(Book.id == book_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise ServiceError(404, "账本不存在")
        members = (
            await self.session.execute(
                select(BookMember.user_id, BookMember.role, BookMember.status, BookMember.joined_at)
                .where(BookMember.book_id == book_id)
                .order_by(BookMember.joined_at, BookMember.user_id)
            )
        ).mappings()
        return AdminBookDetail(**row, members=[AdminMember(**member) for member in members])
