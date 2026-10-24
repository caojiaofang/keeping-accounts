"""统一个人与共享权限；共享读写与成员变更通过账本行锁协调。"""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookActivity, BookMember
from app.schemas.ledger import (
    BookResponse,
    CreateBook,
    CreateEntry,
    EntryResponse,
    LedgerResponse,
    MemberResponse,
    UpdateBudget,
    UpdateEntry,
)


def entry_response(entry: LedgerEntry) -> EntryResponse:
    values = {
        key: getattr(entry, key)
        for key in (
            "id",
            "book_id",
            "type",
            "category_id",
            "amount",
            "date",
            "time",
            "note",
            "account",
            "created_by",
            "version",
            "updated_by",
        )
    }
    return EntryResponse(
        **values,
        created_at=int(entry.created_at.timestamp() * 1000),
        updated_at=int(entry.updated_at.timestamp() * 1000),
    )


class LedgerService:
    def __init__(self, session: AsyncSession, user_id: UUID):
        self.session = session
        self.user_id = user_id

    def access_condition(self):
        member = exists(
            select(1)
            .where(
                BookMember.book_id == Book.id,
                BookMember.user_id == self.user_id,
                BookMember.status == "active",
            )
            .correlate(Book)
        )
        return and_(
            Book.status == "active",
            or_(
                and_(Book.kind == "personal", Book.owner_id == self.user_id),
                and_(Book.kind == "shared", member),
            ),
        )

    async def require_book(self, book_id: UUID, write=False) -> Book:
        book = await self.session.scalar(
            select(Book)
            .where(
                Book.id == book_id,
                self.access_condition(),
            )
            .with_for_update(read=not write, of=Book)
            .execution_options(populate_existing=True)
        )
        if book is None:
            raise ServiceError(404, "账本不存在或无权访问")
        # 等待行锁时成员关系可能已经变更，必须用新的数据库语句再次核验。
        if book.kind == "shared":
            status = await self.session.scalar(
                select(BookMember.status).where(
                    BookMember.book_id == book.id, BookMember.user_id == self.user_id
                )
            )
            if status != "active":
                raise ServiceError(404, "账本不存在或无权访问")
        elif book.owner_id != self.user_id:
            raise ServiceError(404, "账本不存在或无权访问")
        return book

    def require_owner(self, book: Book):
        if book.owner_id != self.user_id:
            raise ServiceError(403, "只有账本创建者可以执行此操作")

    async def describe(self, book: Book) -> BookResponse:
        members = []
        if book.kind == "shared":
            rows = await self.session.scalars(
                select(BookMember)
                .where(BookMember.book_id == book.id)
                .order_by(BookMember.joined_at, BookMember.user_id)
                .execution_options(populate_existing=True)
            )
            members = [
                MemberResponse(user_id=row.user_id, name=row.name, role=row.role, status=row.status)
                for row in rows
            ]
        return BookResponse(
            **{
                key: getattr(book, key)
                for key in (
                    "id",
                    "name",
                    "description",
                    "icon",
                    "color",
                    "budget",
                    "version",
                    "kind",
                    "owner_id",
                )
            },
            current_user_id=self.user_id,
            members=members,
        )

    def activity(self, book_id: UUID, action: str, target_id=None, payload=None):
        self.session.add(
            BookActivity(
                book_id=book_id,
                actor_id=self.user_id,
                action=action,
                target_id=target_id,
                payload=payload or {},
                created_at=datetime.now(UTC),
            )
        )

    async def snapshot(self) -> LedgerResponse:
        default_id = uuid5(self.user_id, "moji:daily")
        await self.session.execute(
            insert(Book)
            .values(
                id=default_id,
                owner_id=self.user_id,
                name="日常账本",
                description="认真生活的每一天",
                icon="leaf",
            )
            .on_conflict_do_nothing(index_elements=[Book.id])
        )
        await self.session.commit()
        books = list(
            await self.session.scalars(
                select(Book)
                .where(self.access_condition())
                .order_by(Book.id)
                .with_for_update(read=True, of=Book)
            )
        )
        allowed_ids = set(
            await self.session.scalars(select(Book.id).where(self.access_condition()))
        )
        books = [book for book in books if book.id in allowed_ids]
        entries = await self.session.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.book_id.in_([book.id for book in books]),
                LedgerEntry.is_deleted.is_(False),
            )
            .order_by(LedgerEntry.date, LedgerEntry.created_at, LedgerEntry.id)
        )
        return LedgerResponse(
            books=[await self.describe(book) for book in books],
            entries=[entry_response(entry) for entry in entries],
        )

    async def create_book(self, body: CreateBook) -> BookResponse:
        inserted = await self.session.scalar(
            insert(Book)
            .values(
                id=body.id,
                name=body.name,
                owner_id=self.user_id,
                kind=body.kind,
                description="和在意的人，一起记录生活"
                if body.kind == "shared"
                else "新的故事，从这一笔开始",
                icon="home" if body.kind == "shared" else "book",
                budget=800000 if body.kind == "shared" else 500000,
            )
            .on_conflict_do_nothing(index_elements=[Book.id])
            .returning(Book.id)
        )
        if inserted and body.kind == "shared":
            await self.session.execute(
                insert(BookMember).values(
                    book_id=body.id,
                    user_id=self.user_id,
                    name=body.member_name,
                    role="owner",
                )
            )
            self.activity(body.id, "create_book", body.id)
        book = await self.require_book(body.id, write=True)
        if book.owner_id != self.user_id:
            raise ServiceError(404, "账本不存在或无权访问")
        if book.name != body.name or book.kind != body.kind:
            raise ServiceError(409, "此次创建的内容已变化，请刷新后重试")
        result = await self.describe(book)
        await self.session.commit()
        return result

    async def update_budget(self, book_id: UUID, body: UpdateBudget) -> BookResponse:
        original = await self.require_book(book_id, write=True)
        self.require_owner(original)
        before = original.budget
        book = await self.session.scalar(
            update(Book)
            .where(Book.id == book_id, Book.version == body.version)
            .values(budget=body.budget, version=Book.version + 1)
            .returning(Book)
            .execution_options(populate_existing=True)
        )
        if book is None:
            raise ServiceError(409, "账本已在其他设备更新，请刷新后重试")
        if book.kind == "shared":
            self.activity(book_id, "budget", book_id, {"before": before, "after": body.budget})
        result = await self.describe(book)
        await self.session.commit()
        return result

    async def create_entry(self, body: CreateEntry) -> EntryResponse:
        book = await self.require_book(body.book_id, write=True)
        inserted = await self.session.scalar(
            insert(LedgerEntry)
            .values(
                **body.model_dump(),
                created_by=self.user_id,
                updated_by=self.user_id,
            )
            .on_conflict_do_nothing(index_elements=[LedgerEntry.id])
            .returning(LedgerEntry.id)
        )
        entry = await self.session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.id == body.id,
                LedgerEntry.created_by == self.user_id,
            )
        )
        if entry is None:
            raise ServiceError(404, "账单不存在或无权访问")
        if entry.is_deleted or any(
            getattr(entry, key) != value for key, value in body.model_dump().items()
        ):
            raise ServiceError(409, "此次记账已处理且内容已变化，请刷新明细后重试")
        result = entry_response(entry)
        if inserted and book.kind == "shared":
            self.activity(
                book.id,
                "create_entry",
                entry.id,
                {"after": result.model_dump(mode="json", by_alias=True)},
            )
        await self.session.commit()
        return result

    async def require_entry(
        self, entry_id: UUID, include_deleted=False, write=False, target_id=None
    ):
        statement = (
            select(LedgerEntry)
            .join(Book)
            .where(
                LedgerEntry.id == entry_id,
                self.access_condition(),
            )
            .execution_options(populate_existing=True)
        )
        if not include_deleted:
            statement = statement.where(LedgerEntry.is_deleted.is_(False))
        entry = await self.session.scalar(statement)
        if entry is None:
            raise ServiceError(404, "账单不存在或无权访问")
        original_book_id = entry.book_id
        # 跨个人账本转移时按固定顺序加锁，避免两个方向的转移互相等待。
        for book_id in sorted({original_book_id, target_id or original_book_id}, key=str):
            await self.require_book(book_id, write=write)
        await self.session.refresh(entry)
        if entry.book_id != original_book_id:
            raise ServiceError(409, "账单已被转移，请重新读取后重试")
        book = await self.require_book(entry.book_id, write=write)
        if write and entry.created_by != self.user_id and book.owner_id != self.user_id:
            raise ServiceError(403, "只能修改或删除自己记录的账单")
        return entry

    async def update_entry(self, entry_id: UUID, body: UpdateEntry) -> EntryResponse:
        original = await self.require_entry(entry_id, write=True, target_id=body.book_id)
        old_book = await self.require_book(original.book_id, write=True)
        target = await self.require_book(body.book_id, write=True)
        if original.book_id != body.book_id and (
            old_book.kind == "shared" or target.kind == "shared"
        ):
            raise ServiceError(422, "共同账单不能转移到其他账本")
        before = entry_response(original).model_dump(mode="json", by_alias=True)
        entry = await self.session.scalar(
            update(LedgerEntry)
            .where(
                LedgerEntry.id == entry_id,
                LedgerEntry.is_deleted.is_(False),
                LedgerEntry.version == body.version,
            )
            .values(
                **body.model_dump(exclude={"version"}),
                version=LedgerEntry.version + 1,
                updated_by=self.user_id,
                updated_at=func.now(),
            )
            .returning(LedgerEntry)
            .execution_options(populate_existing=True)
        )
        if entry is None:
            raise ServiceError(409, "账单已在其他设备更新，请刷新后重试")
        result = entry_response(entry)
        if old_book.kind == "shared":
            self.activity(
                old_book.id,
                "update_entry",
                entry.id,
                {"before": before, "after": result.model_dump(mode="json", by_alias=True)},
            )
        await self.session.commit()
        return result

    async def delete_entry(self, entry_id: UUID, version: int) -> None:
        entry = await self.require_entry(entry_id, include_deleted=True, write=True)
        book = await self.require_book(entry.book_id, write=True)
        if not entry.is_deleted:
            before = entry_response(entry).model_dump(mode="json", by_alias=True)
            changed = await self.session.execute(
                update(LedgerEntry)
                .where(
                    LedgerEntry.id == entry_id,
                    LedgerEntry.version == version,
                    LedgerEntry.is_deleted.is_(False),
                )
                .values(
                    is_deleted=True,
                    version=LedgerEntry.version + 1,
                    updated_by=self.user_id,
                    updated_at=func.now(),
                )
            )
            if not changed.rowcount:
                raise ServiceError(409, "账单已在其他设备更新，请刷新后重试")
            if book.kind == "shared":
                self.activity(book.id, "delete_entry", entry_id, {"before": before})
        await self.session.commit()
