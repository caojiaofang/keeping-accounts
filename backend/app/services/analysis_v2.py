"""V2 日期和受控筛选；仅构造参数化查询，不接入模型或新业务路由。"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import and_, extract, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.schemas.chat_v2 import (
    CompareArgs,
    ComparisonSelection,
    DateRange,
    DateSelection,
    EntryFilter,
    KeywordFilter,
)
from app.schemas.ledger import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from app.services.ledger import LedgerService


@dataclass(frozen=True)
class ResolvedRange:
    date_range: DateRange
    notices: tuple[str, ...] = ()


def validate_date_range(value: DateRange, *, today: date, max_days: int = 366) -> ResolvedRange:
    """今天由服务端注入；返回实际区间及截断提示，不能静默查询未来记录。"""
    # 再次验证，避免 model_copy(update=...) 等内部构造绕过 Pydantic 边界。
    value = DateRange.model_validate(value.model_dump())
    if not 1 <= max_days <= 366:
        raise ValueError("查询跨度配置必须介于 1 至 366 天")
    if value.start > today:
        raise ServiceError(422, "查询区间完全位于未来，请选择截至今天的日期")
    end = min(value.end, today)
    if (end - value.start).days + 1 > max_days:
        raise ServiceError(422, f"单次最多查询 {max_days} 天，请缩小日期范围")
    notices = ()
    if end != value.end:
        notices = (f"结束日期已按北京时间今天 {today.isoformat()} 截止，未统计未来账单。",)
    return ResolvedRange(DateRange(start=value.start, end=end), notices)


def _range(start: date, end: date, *, today: date, max_days: int) -> ResolvedRange:
    if start < date(2000, 1, 1) or end > date(2100, 12, 31):
        raise ServiceError(422, "查询日期仅支持 2000 至 2100 年")
    return validate_date_range(DateRange(start=start, end=end), today=today, max_days=max_days)


def resolve_date_range(
    selection: DateSelection, *, today: date, max_days: int = 366
) -> ResolvedRange:
    selection = DateSelection.model_validate(selection.model_dump())
    kind = selection.kind
    end = today
    if kind == "range":
        return validate_date_range(selection.date_range, today=today, max_days=max_days)
    if kind == "today":
        start = today
    elif kind == "yesterday":
        start = end = today - timedelta(days=1)
    elif kind == "this_week":
        start = today - timedelta(days=today.weekday())
    elif kind == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
    elif kind == "last_n_days":
        start = today - timedelta(days=selection.days - 1)
    elif kind == "this_month":
        start = today.replace(day=1)
    elif kind == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif kind == "this_year":
        start = today.replace(month=1, day=1)
    elif kind == "last_year":
        start = date(today.year - 1, 1, 1)
        end = date(today.year - 1, 12, 31)
    else:
        start = date.fromisoformat(selection.month + "-01")
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return _range(start, end, today=today, max_days=max_days)


def resolve_comparison_range(
    current: DateRange,
    selection: ComparisonSelection,
    *,
    today: date,
    max_days: int = 366,
) -> ResolvedRange:
    current = validate_date_range(current, today=today, max_days=max_days).date_range
    selection = ComparisonSelection.model_validate(selection.model_dump())
    if selection.kind == "range":
        return validate_date_range(selection.date_range, today=today, max_days=max_days)
    if selection.kind == "previous_period":
        end = current.start - timedelta(days=1)
        start = end - (current.end - current.start)
    else:
        if (current.start.year, current.start.month) != (current.end.year, current.end.month):
            raise ServiceError(422, "跨月区间请使用前一等长区间或提供明确的比较日期")
        end_of_month = current.start.replace(day=1) - timedelta(days=1)
        if selection.kind == "previous_month_full":
            start, end = end_of_month.replace(day=1), end_of_month
        else:
            start = end_of_month.replace(day=min(current.start.day, end_of_month.day))
            end = end_of_month.replace(day=min(current.end.day, end_of_month.day))
    return _range(start, end, today=today, max_days=max_days)


def _unique(values, *, ignore_case=False):
    result, seen = [], set()
    for value in values:
        key = value.lower() if ignore_case else value
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def normalize_filter(
    value: EntryFilter, *, today: date, max_days: int = 366
) -> tuple[EntryFilter, tuple[str, ...]]:
    value = EntryFilter.model_validate(value.model_dump())
    allowed = (
        EXPENSE_CATEGORIES
        if value.entry_type == "expense"
        else INCOME_CATEGORIES
        if value.entry_type == "income"
        else EXPENSE_CATEGORIES | INCOME_CATEGORIES
    )
    if set(value.category_ids) - allowed:
        raise ServiceError(422, "分类不存在或与收支类型不匹配，请重新选择")
    if (
        value.amount_min is not None
        and value.amount_min == value.amount_max
        and not (value.min_inclusive and value.max_inclusive)
    ):
        raise ServiceError(422, "金额区间为空，请调整上下限或是否包含边界")
    resolved = validate_date_range(value.date_range, today=today, max_days=max_days)
    normalized = EntryFilter(
        **{
            **value.model_dump(),
            "date_range": resolved.date_range,
            "category_ids": _unique(value.category_ids),
            "accounts": _unique(value.accounts),
            "creator_ids": _unique(value.creator_ids),
            "excluded_entry_ids": _unique(value.excluded_entry_ids),
            "weekdays": sorted(set(value.weekdays)),
            "keywords": KeywordFilter(
                include=_unique(value.keywords.include, ignore_case=True),
                exclude=_unique(value.keywords.exclude, ignore_case=True),
                mode=value.keywords.mode,
            ),
        }
    )
    return normalized, resolved.notices


@dataclass(frozen=True)
class PreparedFilter:
    filters: EntryFilter
    conditions: tuple[ColumnElement[bool], ...]
    notices: tuple[str, ...] = ()

    def entries_statement(self):
        return (
            select(LedgerEntry).join(Book, Book.id == LedgerEntry.book_id).where(*self.conditions)
        )


class FilterQueriesV2:
    def __init__(self, ledger: LedgerService, settings: Settings, *, today: date):
        self.ledger = ledger
        self.max_days = settings.chat_v2_max_range_days
        self.today = today

    async def prepare(self, book_id: UUID, value: EntryFilter) -> PreparedFilter:
        book = await self.ledger.require_book(book_id)
        value, notices = normalize_filter(value, today=self.today, max_days=self.max_days)
        if value.creator_ids:
            if book.kind == "personal":
                allowed_creators = {book.owner_id}
            else:
                # 已退出/移出的记账人的历史记录仍属于这个账本，不改变其本人访问权限。
                allowed_creators = set(
                    await self.ledger.session.scalars(
                        select(BookMember.user_id)
                        .join(Book, Book.id == BookMember.book_id)
                        .where(
                            Book.id == book_id,
                            self.ledger.access_condition(),
                            BookMember.user_id.in_(value.creator_ids),
                        )
                    )
                )
            if set(value.creator_ids) - allowed_creators:
                raise ServiceError(404, "所选记账人不属于当前可访问账本，请重新选择")
        if value.excluded_entry_ids:
            visible_ids = set(
                await self.ledger.session.scalars(
                    select(LedgerEntry.id)
                    .join(Book, Book.id == LedgerEntry.book_id)
                    .where(
                        Book.id == book_id,
                        self.ledger.access_condition(),
                        LedgerEntry.is_deleted.is_(False),
                        LedgerEntry.id.in_(value.excluded_entry_ids),
                    )
                )
            )
            if visible_ids != set(value.excluded_entry_ids):
                raise ServiceError(404, "账单不存在或无权访问，请重新选择要排除的账单")
        return self._prepared(book_id, value, notices)

    async def prepare_compare(
        self, book_id: UUID, arguments: CompareArgs
    ) -> tuple[PreparedFilter, PreparedFilter]:
        current = await self.prepare(book_id, arguments.filters)
        previous_range = validate_date_range(
            arguments.previous_range, today=self.today, max_days=self.max_days
        )
        previous = EntryFilter.model_validate(
            {
                **current.filters.model_dump(),
                "date_range": previous_range.date_range,
            }
        )
        return current, self._prepared(book_id, previous, previous_range.notices)

    def _prepared(self, book_id, value, notices):
        conditions = [
            self.ledger.access_condition(),
            Book.id == book_id,
            LedgerEntry.is_deleted.is_(False),
            LedgerEntry.date.between(value.date_range.start, value.date_range.end),
        ]
        if value.entry_type != "both":
            conditions.append(LedgerEntry.type == value.entry_type)
        for column, values in (
            (LedgerEntry.category_id, value.category_ids),
            (LedgerEntry.account, value.accounts),
            (LedgerEntry.created_by, value.creator_ids),
        ):
            if values:
                conditions.append(column.in_(values))
        include = [
            LedgerEntry.note.icontains(word, autoescape=True) for word in value.keywords.include
        ]
        if include:
            conditions.append((or_ if value.keywords.mode == "any" else and_)(*include))
        for word in value.keywords.exclude:
            conditions.append(~LedgerEntry.note.icontains(word, autoescape=True))
        if value.amount_min is not None:
            conditions.append(
                LedgerEntry.amount >= value.amount_min
                if value.min_inclusive
                else LedgerEntry.amount > value.amount_min
            )
        if value.amount_max is not None:
            conditions.append(
                LedgerEntry.amount <= value.amount_max
                if value.max_inclusive
                else LedgerEntry.amount < value.amount_max
            )
        if value.weekdays:
            # PostgreSQL DOW 的周日为 0，输入使用 ISO 星期 1—7。
            conditions.append(
                extract("dow", LedgerEntry.date).in_([day % 7 for day in value.weekdays])
            )
        if value.excluded_entry_ids:
            conditions.append(LedgerEntry.id.not_in(value.excluded_entry_ids))
        return PreparedFilter(value, tuple(conditions), notices)
