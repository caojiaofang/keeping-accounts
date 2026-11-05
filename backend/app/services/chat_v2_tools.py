"""受控只读查账工具；金额由数据库计算，模型不执行 SQL，也不决定查询身份。"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ValidationError
from sqlalchemy import String, case, cast, func, literal, select, true, tuple_, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.schemas.chat_v2 import (
    CompareArgs,
    DateRange,
    EntryArgs,
    EntryFilter,
    FactRef,
    ResultCard,
    ResultRow,
    SearchArgs,
    SummaryArgs,
    ToolError,
    ToolResult,
)
from app.services.analysis import CATEGORIES
from app.services.analysis_v2 import FilterQueriesV2
from app.services.chat_v2_cursor import CursorCodec
from app.services.ledger import LedgerService

ARGUMENTS = {
    "summarize_entries": SummaryArgs,
    "search_entries": SearchArgs,
    "compare_periods": CompareArgs,
    "get_entry": EntryArgs,
}
TYPE_LABELS = {"expense": "支出", "income": "收入"}
FOOTNOTE = "基于当前有效账单；没有记录不代表实际没有收支。"


def now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


def basis_points(numerator, denominator):
    return int(
        (Decimal(numerator) * 10000 / Decimal(denominator)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


class Bundle:
    def __init__(self, call_id, filters, generated_at, previous_range=None):
        self.call_id, self.filters = call_id, filters
        self.generated_at, self.previous_range = generated_at, previous_range
        self.facts = []
        self.add("range.start", filters.date_range.start.isoformat(), "date", "本期开始日期")
        self.add("range.end", filters.date_range.end.isoformat(), "date", "本期结束日期")
        if previous_range:
            self.add(
                "previous.start",
                previous_range.start.isoformat(),
                "date",
                "上期开始日期",
                previous=True,
            )
            self.add(
                "previous.end",
                previous_range.end.isoformat(),
                "date",
                "上期结束日期",
                previous=True,
            )

    def add(self, suffix, value, unit, label, metric=None, *, previous=False):
        fact = FactRef(
            id=f"{self.call_id}.{suffix}",
            value=value,
            unit=unit,
            label=label,
            source_call_id=self.call_id,
            metric=metric,
            date_range=self.previous_range if previous else self.filters.date_range,
            previous_range=self.previous_range
            if metric in {"difference", "change_rate", "share"}
            else None,
            generated_at=self.generated_at,
        )
        self.facts.append(fact)
        return fact.id

    def totals(self, entry_type, total, count):
        label = TYPE_LABELS[entry_type]
        return [
            self.add(f"{entry_type}.total", int(total), "cents", f"{label}合计", "total"),
            self.add(f"{entry_type}.count", int(count), "count", f"{label}笔数", "count"),
        ]

    def card(self, suffix, kind, title, fact_ids, rows, footnote=FOOTNOTE):
        return ResultCard(
            id=f"{self.call_id}.{suffix}",
            type=kind,
            title=title,
            filters=self.filters,
            previous_range=self.previous_range,
            generated_at=self.generated_at,
            fact_ids=fact_ids,
            rows=rows,
            footnote=footnote,
        )


def grouped_statement(prepared, group_by):
    member = aliased(BookMember)
    group_key = {
        "category": LedgerEntry.category_id,
        "date": cast(LedgerEntry.date, String),
        "account": LedgerEntry.account,
        "creator": cast(LedgerEntry.created_by, String),
    }.get(group_by, literal("all"))
    group_name = member.name if group_by == "creator" else literal("")
    statement = (
        select(
            LedgerEntry.type.label("entry_type"),
            group_key.label("group_key"),
            group_name.label("group_name"),
            func.sum(LedgerEntry.amount).label("total"),
            func.count().label("count"),
        )
        .select_from(LedgerEntry)
        .join(Book, Book.id == LedgerEntry.book_id)
    )
    if group_by == "creator":
        statement = statement.outerjoin(
            member, (member.book_id == Book.id) & (member.user_id == LedgerEntry.created_by)
        )
    return statement.where(*prepared.conditions).group_by(LedgerEntry.type, group_key, group_name)


def group_label(key, name, group_by):
    if group_by == "category":
        return CATEGORIES.get(key, ("未识别分类",))[0]
    if group_by == "creator":
        return name or "历史记账人"
    return key


class ChatToolsV2:
    def __init__(self, ledger: LedgerService, settings: Settings, *, today: date, clock=now_ms):
        self.ledger, self.clock, self.today = ledger, clock, today
        self.filters = FilterQueriesV2(ledger, settings, today=today)
        self.cursor = CursorCodec(settings.chat_v2_cursor_secret)

    async def execute(self, call_id, book_id, name, arguments):
        """call_id 由编排器生成短命名空间；上游原生调用 ID 在适配层另行关联。"""
        if (
            not isinstance(name, str)
            or name not in ARGUMENTS
            or not isinstance(call_id, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", call_id)
        ):
            raise ServiceError(422, "工具名称或调用编号无效")
        try:
            if isinstance(arguments, BaseModel):
                arguments = arguments.model_dump()
            args = ARGUMENTS[name].model_validate(arguments)
        except ValidationError:
            await self.ledger.session.rollback()
            return self._error(
                call_id, name, "invalid_arguments", "查询参数不正确，请检查日期和筛选条件"
            )
        try:
            result = await getattr(self, name)(call_id, book_id, args)
            # 不能把查询期间的权限变化解释成零记录；返回前再次核验。
            await self.ledger.require_book(book_id)
            return result
        except ServiceError as error:
            code = {404: "not_found", 403: "permission_denied", 422: "invalid_arguments"}.get(
                error.status_code, "unavailable"
            )
            return self._error(call_id, name, code, error.detail)
        except SQLAlchemyError:
            return self._error(call_id, name, "unavailable", "账单查询暂时不可用，请稍后重试")
        finally:
            # 查询工具不写入业务数据；结束短事务，不能持锁等待下一次模型调用。
            await self.ledger.session.rollback()

    @staticmethod
    def _error(call_id, name, code, detail):
        return ToolResult(
            call_id=call_id, name=name, status="error", error=ToolError(code=code, detail=detail)
        )

    async def summarize_entries(self, call_id, book_id, args):
        prepared = await self.filters.prepare(book_id, args.filters)
        groups = (
            (await self.ledger.session.execute(grouped_statement(prepared, args.group_by)))
            .mappings()
            .all()
        )
        bundle = Bundle(call_id, prepared.filters, self.clock())
        cards = []
        for entry_type in self._types(prepared.filters):
            selected = [row for row in groups if row["entry_type"] == entry_type]
            total, count = (
                sum(int(row["total"]) for row in selected),
                sum(row["count"] for row in selected),
            )
            totals = bundle.totals(entry_type, total, count)
            rows = []
            if args.group_by:
                selected = sorted(
                    selected,
                    key=(lambda row: row["group_key"])
                    if args.group_by == "date"
                    else lambda row: (-int(row["total"]), row["group_key"]),
                )
                display = selected if args.group_by == "date" else selected[:20]
                if len(display) < len(selected):
                    display = list(display) + [
                        {
                            "group_key": None,
                            "group_name": "其余分组",
                            "total": sum(int(row["total"]) for row in selected[20:]),
                            "count": sum(row["count"] for row in selected[20:]),
                        }
                    ]
                for index, row in enumerate(display):
                    prefix = f"{entry_type}.g{index}"
                    refs = [
                        bundle.add(
                            prefix + ".total", int(row["total"]), "cents", "分组金额", "total"
                        ),
                        bundle.add(
                            prefix + ".count", int(row["count"]), "count", "分组笔数", "count"
                        ),
                    ]
                    if total:
                        refs.append(
                            bundle.add(
                                prefix + ".share",
                                basis_points(row["total"], total),
                                "basis_points",
                                "分组占比",
                                "share",
                            )
                        )
                    key = row["group_key"]
                    label = (
                        "其余分组"
                        if key is None
                        else group_label(key, row["group_name"], args.group_by)
                    )
                    rows.append(
                        ResultRow(
                            label=label,
                            group_key=key,
                            fact_ids=refs,
                            entry_type=entry_type,
                            date=date.fromisoformat(key) if args.group_by == "date" else None,
                            is_other=key is None,
                        )
                    )
            note = FOOTNOTE + (
                "按日期仅列出有匹配记录的日期。"
                if args.group_by == "date"
                else "最多单列前二十组，其余合并；总额包含全部匹配记录。"
            )
            cards.append(
                bundle.card(
                    entry_type,
                    "trend" if args.group_by == "date" else "summary",
                    TYPE_LABELS[entry_type] + "汇总",
                    totals,
                    rows,
                    note,
                )
            )
        return ToolResult(
            call_id=call_id,
            name="summarize_entries",
            status="complete",
            cards=cards,
            fact_refs=bundle.facts,
            notices=list(prepared.notices),
        )

    async def search_entries(self, call_id, book_id, args):
        prepared = await self.filters.prepare(book_id, args.filters)
        scope = self.cursor.scope(
            self.ledger.user_id, book_id, prepared.filters, args.sort, args.limit
        )
        position = (
            self.cursor.decode(args.cursor, scope, args.sort, self.clock()) if args.cursor else None
        )
        base = (
            prepared.entries_statement().with_only_columns(*LedgerEntry.__table__.c).cte("matches")
        )
        totals = select(
            *[
                expr
                for entry_type in TYPE_LABELS
                for expr in (
                    func.coalesce(
                        func.sum(case((base.c.type == entry_type, base.c.amount), else_=0)), 0
                    ).label(entry_type + "_total"),
                    func.count(case((base.c.type == entry_type, 1))).label(entry_type + "_count"),
                )
            ]
        ).cte("totals")
        sort_columns = (
            [base.c.date, func.coalesce(base.c.time, ""), base.c.id]
            if args.sort == "date_desc"
            else [base.c.amount, base.c.date, base.c.id]
        )
        page = select(base)
        if position is not None:
            page = page.where(tuple_(*sort_columns) < position)
        page = (
            page.order_by(*(column.desc() for column in sort_columns))
            .limit(args.limit + 1)
            .cte("page")
        )
        page_order = (
            [page.c.date, func.coalesce(page.c.time, ""), page.c.id]
            if args.sort == "date_desc"
            else [page.c.amount, page.c.date, page.c.id]
        )
        statement = (
            select(totals, page)
            .select_from(totals.outerjoin(page, true()))
            .order_by(*(column.desc() for column in page_order))
        )
        result = (await self.ledger.session.execute(statement)).mappings().all()
        records = [row for row in result if row["id"] is not None]
        bundle = Bundle(call_id, prepared.filters, self.clock())
        refs = [
            ref
            for entry_type in self._types(prepared.filters)
            for ref in bundle.totals(
                entry_type, result[0][entry_type + "_total"], result[0][entry_type + "_count"]
            )
        ]
        rows = [self._entry_row(bundle, row, prepared.filters) for row in records[: args.limit]]
        next_cursor = (
            self.cursor.encode(scope, args.sort, records[args.limit - 1], self.clock())
            if len(records) > args.limit
            else None
        )
        card = bundle.card(
            "entries",
            "entries",
            "匹配账单",
            refs,
            rows,
            FOOTNOTE + "总额与笔数包含全部匹配记录，每页最多二十笔；翻页按当前账单重新读取。",
        )
        return ToolResult(
            call_id=call_id,
            name="search_entries",
            status="complete",
            cards=[card],
            fact_refs=bundle.facts,
            next_cursor=next_cursor,
            notices=list(prepared.notices),
        )

    async def get_entry(self, call_id, book_id, args):
        await self.ledger.require_book(book_id)
        row = (
            (
                await self.ledger.session.execute(
                    select(*LedgerEntry.__table__.c)
                    .join(Book, Book.id == LedgerEntry.book_id)
                    .where(
                        Book.id == book_id,
                        self.ledger.access_condition(),
                        LedgerEntry.id == args.entry_id,
                        LedgerEntry.is_deleted.is_(False),
                        LedgerEntry.date <= self.today,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ServiceError(404, "账单不存在或无权访问")
        filters = EntryFilter(
            date_range=DateRange(start=row["date"], end=row["date"]), entry_type=row["type"]
        )
        bundle = Bundle(call_id, filters, self.clock())
        refs = bundle.totals(row["type"], row["amount"], 1)
        card = bundle.card(
            "entry", "entries", "账单详情", refs, [self._entry_row(bundle, row, filters)]
        )
        return ToolResult(
            call_id=call_id,
            name="get_entry",
            status="complete",
            cards=[card],
            fact_refs=bundle.facts,
        )

    @staticmethod
    def _entry_row(bundle, row, filters):
        prefix = "e" + row["id"].hex
        amount_ref = bundle.add(
            prefix + ".amount", int(row["amount"]), "cents", "账单金额", "amount"
        )
        date_ref = bundle.add(prefix + ".date", row["date"].isoformat(), "date", "账单日期")
        category = CATEGORIES.get(row["category_id"], ("未识别分类",))[0]
        return ResultRow(
            label=row["note"] or category,
            entry_id=row["id"],
            date=row["date"],
            time=row["time"],
            entry_type=row["type"],
            category_id=row["category_id"],
            account=row["account"],
            creator_id=row["created_by"],
            fact_ids=[amount_ref, date_ref],
            matched_keywords=[
                word for word in filters.keywords.include if word.lower() in row["note"].lower()
            ],
            detail=f"{category} · {row['account']}；按日期、收支及所列筛选条件匹配",
        )

    async def compare_periods(self, call_id, book_id, args):
        current, previous = await self.filters.prepare_compare(book_id, args)
        current_groups = grouped_statement(current, args.group_by).subquery("current_groups")
        previous_groups = grouped_statement(previous, args.group_by).subquery("previous_groups")
        statement = union_all(
            select(literal("current").label("period"), *current_groups.c),
            select(literal("previous").label("period"), *previous_groups.c),
        )
        groups = (await self.ledger.session.execute(statement)).mappings().all()
        bundle = Bundle(call_id, current.filters, self.clock(), previous.filters.date_range)
        cards = []
        for entry_type in self._types(current.filters):
            periods = {"current": {}, "previous": {}}
            for row in groups:
                if row["entry_type"] != entry_type:
                    continue
                key = row["group_key"]
                if args.group_by == "date":
                    period_range = (
                        current.filters.date_range
                        if row["period"] == "current"
                        else previous.filters.date_range
                    )
                    key = str((date.fromisoformat(key) - period_range.start).days)
                periods[row["period"]][key] = row
            cur, prev = periods["current"], periods["previous"]
            total, count = (
                sum(int(row["total"]) for row in cur.values()),
                sum(row["count"] for row in cur.values()),
            )
            old, old_count = (
                sum(int(row["total"]) for row in prev.values()),
                sum(row["count"] for row in prev.values()),
            )
            totals = bundle.totals(entry_type, total, count)
            totals += self._comparison_refs(bundle, entry_type, total, count, old, old_count)
            differences = []
            for key in cur.keys() | prev.keys():
                new_row, old_row = cur.get(key), prev.get(key)
                new_amount, old_amount = (
                    int(new_row["total"]) if new_row else 0,
                    int(old_row["total"]) if old_row else 0,
                )
                differences.append(
                    (
                        key,
                        new_row or old_row,
                        new_amount,
                        old_amount,
                        new_row["count"] if new_row else 0,
                        old_row["count"] if old_row else 0,
                    )
                )
            differences.sort(key=lambda item: (-abs(item[2] - item[3]), item[0]))
            display = differences if args.group_by == "date" else differences[:20]
            if len(display) < len(differences):
                rest = differences[20:]
                display = list(display) + [
                    (
                        None,
                        {"group_name": "其余分组"},
                        sum(item[2] for item in rest),
                        sum(item[3] for item in rest),
                        sum(item[4] for item in rest),
                        sum(item[5] for item in rest),
                    )
                ]
            rows = []
            for index, (key, row, amount, old_amount, row_count, old_row_count) in enumerate(
                display
            ):
                prefix = f"{entry_type}.g{index}"
                refs = [
                    bundle.add(prefix + ".total", amount, "cents", "分组本期金额", "total"),
                    bundle.add(prefix + ".count", row_count, "count", "分组本期笔数", "count"),
                ]
                refs += self._comparison_refs(
                    bundle, prefix, amount, row_count, old_amount, old_row_count
                )
                if total != old:
                    refs.append(
                        bundle.add(
                            prefix + ".share",
                            basis_points(amount - old_amount, total - old),
                            "basis_points",
                            "该组对总差额的贡献比例",
                            "share",
                        )
                    )
                label = (
                    "其余分组"
                    if key is None
                    else group_label(key, row["group_name"], args.group_by)
                )
                detail, row_date = None, None
                if args.group_by == "date":
                    offset = int(key)
                    day, old_day = (
                        current.filters.date_range.start + timedelta(days=offset),
                        previous.filters.date_range.start + timedelta(days=offset),
                    )
                    row_date = day if day <= current.filters.date_range.end else None
                    label = f"第 {offset + 1} 天"
                    current_label = str(day) if row_date else "无对应日期"
                    previous_label = (
                        str(old_day) if old_day <= previous.filters.date_range.end else "无对应日期"
                    )
                    detail = f"本期 {current_label}；上期 {previous_label}"
                rows.append(
                    ResultRow(
                        label=label,
                        group_key=key,
                        entry_type=entry_type,
                        fact_ids=refs,
                        detail=detail,
                        date=row_date,
                        is_other=key is None,
                    )
                )
            note = FOOTNOTE + "差额仅表示账单数值贡献，不代表实际原因；比例按基点四舍五入。"
            if not old:
                note += "上期金额为零，未计算增长率。"
            cards.append(
                bundle.card(
                    entry_type + ".compare",
                    "comparison",
                    TYPE_LABELS[entry_type] + "期间比较",
                    totals,
                    rows,
                    note,
                )
            )
        return ToolResult(
            call_id=call_id,
            name="compare_periods",
            status="complete",
            cards=cards,
            fact_refs=bundle.facts,
            notices=list(current.notices + previous.notices),
        )

    @staticmethod
    def _comparison_refs(bundle, prefix, total, count, previous, previous_count):
        refs = [
            bundle.add(
                prefix + ".previous_total",
                previous,
                "cents",
                "上期金额",
                "previous_total",
                previous=True,
            ),
            bundle.add(
                prefix + ".previous_count",
                previous_count,
                "count",
                "上期笔数",
                "previous_count",
                previous=True,
            ),
            bundle.add(
                prefix + ".difference", total - previous, "cents", "本期与上期差额", "difference"
            ),
        ]
        if previous:
            refs.append(
                bundle.add(
                    prefix + ".change_rate",
                    basis_points(total - previous, previous),
                    "basis_points",
                    "相对上期变化率",
                    "change_rate",
                )
            )
        return refs

    @staticmethod
    def _types(filters):
        return list(TYPE_LABELS) if filters.entry_type == "both" else [filters.entry_type]
