"""公共分类只读，专属分类按账本权限和行锁维护。"""

from sqlalchemy import select

from app.category_catalog import CATEGORIES
from app.core.errors import ServiceError
from app.models.ledger import BookCategory
from app.schemas.ledger import EXPENSE_CATEGORIES, INCOME_CATEGORIES, CategoryResponse


def category_response(category):
    return CategoryResponse.model_validate(category, from_attributes=True)


class CategoryService:
    def __init__(self, ledger):
        self.ledger = ledger
        self.session = ledger.session

    async def list(self, book_id, *, include_deleted=True):
        await self.ledger.require_book(book_id)
        query = (
            select(BookCategory).where(BookCategory.book_id == book_id).order_by(BookCategory.id)
        )
        if not include_deleted:
            query = query.where(BookCategory.is_deleted.is_(False))
        return list(await self.session.scalars(query))

    async def catalog(self, book_id, *, include_deleted=True):
        return {
            **{
                key: {
                    "id": key,
                    "name": value[0],
                    "icon": value[1],
                    "color": value[2],
                    "type": "expense" if key in EXPENSE_CATEGORIES else "income",
                }
                for key, value in CATEGORIES.items()
            },
            **{
                row.id: {
                    "id": row.id,
                    "name": row.name,
                    "type": row.type,
                    "icon": row.icon,
                    "color": row.color,
                }
                for row in await self.list(book_id, include_deleted=include_deleted)
            },
        }

    async def validate(self, book_id, category_id, entry_type, *, retained_id=None, history=False):
        allowed = EXPENSE_CATEGORIES if entry_type == "expense" else INCOME_CATEGORIES
        if category_id in allowed:
            return
        category = await self.session.scalar(
            select(BookCategory).where(
                BookCategory.id == category_id,
                BookCategory.book_id == book_id,
                BookCategory.type == entry_type,
            )
        )
        if category is None or (category.is_deleted and not history and category_id != retained_id):
            raise ServiceError(422, "分类不存在、已删除或不属于当前账本及收支类型，请重新选择")

    async def _unique_name(self, book_id, entry_type, name, exclude=None):
        public = EXPENSE_CATEGORIES if entry_type == "expense" else INCOME_CATEGORIES
        if any(CATEGORIES[key][0] == name for key in public):
            raise ServiceError(409, "该名称已被公共分类使用，请换一个名称")
        existing = await self.session.scalar(
            select(BookCategory.id).where(
                BookCategory.book_id == book_id,
                BookCategory.type == entry_type,
                BookCategory.name == name,
                BookCategory.is_deleted.is_(False),
                BookCategory.id != (exclude or ""),
            )
        )
        if existing:
            raise ServiceError(409, "当前账本已有同名分类，请换一个名称")

    async def create(self, book_id, body):
        book = await self.ledger.require_book(book_id, write=True)
        existing = await self.session.get(BookCategory, body.id)
        if existing:
            if existing.book_id != book_id:
                raise ServiceError(404, "分类不存在或无权访问")
            if existing.is_deleted or existing.type != body.type or existing.name != body.name:
                raise ServiceError(409, "此次分类创建已处理且内容已变化，请刷新后重试")
            return category_response(existing)
        await self._unique_name(book_id, body.type, body.name)
        category = BookCategory(id=body.id, book_id=book_id, type=body.type, name=body.name)
        self.session.add(category)
        await self.session.flush()
        result = category_response(category)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "create_category",
                payload={"after": result.model_dump(mode="json", by_alias=True)},
            )
        await self.session.commit()
        return result

    async def change(self, book_id, category_id, version, *, name=None):
        book = await self.ledger.require_book(book_id, write=True)
        if category_id in CATEGORIES:
            raise ServiceError(403, "公共分类固定，不能修改或删除")
        category = await self.session.scalar(
            select(BookCategory)
            .where(
                BookCategory.id == category_id,
                BookCategory.book_id == book_id,
            )
            .execution_options(populate_existing=True)
        )
        if category is None:
            raise ServiceError(404, "分类不存在或无权访问")
        if category.is_deleted and name is None:
            return category_response(category)
        if category.is_deleted or category.version != version:
            raise ServiceError(409, "分类已在其他设备更新或删除，请刷新后重试")
        before = category_response(category).model_dump(mode="json", by_alias=True)
        if name is not None:
            await self._unique_name(book_id, category.type, name, category_id)
            category.name = name
        else:
            category.is_deleted = True
        category.version += 1
        await self.session.flush()
        result = category_response(category)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "update_category" if name is not None else "delete_category",
                payload={"before": before, "after": result.model_dump(mode="json", by_alias=True)},
            )
        await self.session.commit()
        return result
