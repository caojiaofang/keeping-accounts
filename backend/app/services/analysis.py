"""受控统计查询，模型仅选择查询参数，不生成或执行 SQL。"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.schemas.chat import AnalysisCard, AnalysisRow, QueryContext, QueryPlan
from app.schemas.ledger import EXPENSE_CATEGORIES, INCOME_CATEGORIES, MAX_CENTS
from app.services.ledger import LedgerService

CATEGORIES = {
    "food": ("餐饮", "food", "#fbefe1"),
    "coffee": ("咖啡茶饮", "coffee", "#eee9e3"),
    "transport": ("交通", "bus", "#e8eefb"),
    "shopping": ("购物", "bag", "#f6e8ec"),
    "daily": ("日用", "basket", "#f5eddf"),
    "house": ("住房", "home", "#e9e9f6"),
    "play": ("娱乐", "game", "#f0eafa"),
    "health": ("医疗", "health", "#e5f2ed"),
    "study": ("学习", "study", "#e8f0ed"),
    "gift": ("人情", "gift", "#faece5"),
    "travel": ("旅行", "plane", "#e7f0f8"),
    "other": ("其他", "more", "#eef0ef"),
    "salary": ("工资", "wallet", "#e3f2e9"),
    "bonus": ("奖金", "gift", "#fbefe1"),
    "parttime": ("兼职", "work", "#e8eefb"),
    "refund": ("退款", "return", "#f6e8ec"),
    "other-income": ("其他", "more", "#eef0ef"),
}


def month_range(month: str, today: date) -> tuple[date, date]:
    try:
        start = date.fromisoformat(month + "-01")
        if not 2000 <= start.year <= 2100 or start.strftime("%Y-%m") != month:
            raise ValueError("月份超出范围")
    except ValueError:
        raise ServiceError(502, "月份识别不明确，请使用如“2026年10月”的明确月份") from None
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, min(end, today) if month == today.strftime("%Y-%m") else end


def previous_range(start: date, end: date, same_period: bool) -> tuple[date, date]:
    year, month = (start.year - 1, 12) if start.month == 1 else (start.year, start.month - 1)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, min(end.day, last) if same_period else last)


def resolve_context(plan: QueryPlan, previous: QueryContext, today: date) -> QueryContext:
    month = plan.month or previous.month
    month_range(month, today)
    category = (
        previous.category_id
        if plan.category_mode == "inherit"
        else (plan.category_id if plan.category_mode == "selected" else None)
    )
    entry_type = plan.entry_type or previous.entry_type
    allowed = EXPENSE_CATEGORIES if entry_type == "expense" else INCOME_CATEGORIES
    if (plan.category_mode == "selected" and not category) or (
        category and category not in allowed
    ):
        raise ServiceError(502, "收支分类识别不明确，请说明要查询的分类和收支类型")
    if plan.threshold is not None and not 0 <= plan.threshold <= MAX_CENTS:
        raise ServiceError(502, "金额范围识别不明确，请补充有效金额")
    return QueryContext(month=month, category_id=category, entry_type=entry_type)


def yuan(amount: int) -> str:
    whole, fraction = divmod(abs(amount), 100)
    return f"{'−' if amount < 0 else ''}¥{whole:,}.{fraction:02}"


class AnalysisQueries:
    def __init__(self, ledger: LedgerService):
        self.ledger = ledger

    def filters(self, book_id, context: QueryContext, start: date, end: date, threshold=None):
        conditions = [
            self.ledger.access_condition(),
            Book.id == book_id,
            LedgerEntry.is_deleted.is_(False),
            LedgerEntry.type == context.entry_type,
            LedgerEntry.date >= start,
            LedgerEntry.date <= end,
        ]
        if context.category_id:
            conditions.append(LedgerEntry.category_id == context.category_id)
        if threshold is not None:
            conditions.append(LedgerEntry.amount > threshold)
        return conditions

    async def totals(self, conditions):
        total, count = (
            await self.ledger.session.execute(
                select(func.coalesce(func.sum(LedgerEntry.amount), 0), func.count(LedgerEntry.id))
                .join(Book, Book.id == LedgerEntry.book_id)
                .where(*conditions)
            )
        ).one()
        return int(total), count

    async def run(
        self, book_id, context: QueryContext, plan: QueryPlan, today: date
    ) -> AnalysisCard:
        await self.ledger.require_book(book_id)
        start, end = month_range(context.month, today)
        threshold = (
            (plan.threshold if plan.threshold is not None else 20000)
            if plan.action == "large"
            else None
        )
        conditions = self.filters(book_id, context, start, end, threshold)
        records, groups = [], []
        # 总额与明细/分组在同一条 SQL 中读取，避免并发记账造成卡片内部不一致。
        if plan.action == "compare":
            same_period = (
                context.month == today.strftime("%Y-%m") and plan.comparison != "full_month"
            )
            prev_start, prev_end = previous_range(start, end, same_period)
            current_dates = LedgerEntry.date.between(start, end)
            previous_dates = LedgerEntry.date.between(prev_start, prev_end)
            total, count, previous, previous_count = (
                await self.ledger.session.execute(
                    select(
                        func.coalesce(func.sum(LedgerEntry.amount).filter(current_dates), 0),
                        func.count(LedgerEntry.id).filter(current_dates),
                        func.coalesce(func.sum(LedgerEntry.amount).filter(previous_dates), 0),
                        func.count(LedgerEntry.id).filter(previous_dates),
                    )
                    .join(Book, Book.id == LedgerEntry.book_id)
                    .where(*self.filters(book_id, context, prev_start, end))
                )
            ).one()
            total, previous = int(total), int(previous)
        elif plan.action in {"details", "large"}:
            result = list(
                await self.ledger.session.execute(
                    select(LedgerEntry, func.sum(LedgerEntry.amount).over(), func.count().over())
                    .join(Book)
                    .where(*conditions)
                    .order_by(LedgerEntry.amount.desc(), LedgerEntry.date.desc(), LedgerEntry.id)
                    .limit(20)
                )
            )
            records = [row[0] for row in result]
            total, count = (int(result[0][1]), result[0][2]) if result else (0, 0)
        else:
            groups = list(
                await self.ledger.session.execute(
                    select(
                        LedgerEntry.category_id,
                        func.sum(LedgerEntry.amount),
                        func.count(LedgerEntry.id),
                    )
                    .join(Book)
                    .where(*conditions)
                    .group_by(LedgerEntry.category_id)
                    .order_by(func.sum(LedgerEntry.amount).desc(), LedgerEntry.category_id)
                )
            )
            total = sum(int(row[1]) for row in groups)
            count = sum(row[2] for row in groups)
        label = "支出" if context.entry_type == "expense" else "收入"
        category = CATEGORIES[context.category_id][0] if context.category_id else "全部"
        card = AnalysisCard(
            title=f"{category}{label}",
            text=f"共记录 {count} 笔{label}，合计 {yuan(total)}。",
            total=total,
            count=count,
            entry_type=context.entry_type,
            scope=f"{start.isoformat()} 至 {end.isoformat()}",
            rows=[],
            footnote="依据回答生成时的有效账单计算；没有记录不代表实际没有收支。",
        )
        if plan.action == "compare":
            card.title += " · 月份对比"
            card.rows = [
                AnalysisRow(label=context.month, amount=total, icon="calendar", color="#e1eee7"),
                AnalysisRow(
                    label=prev_start.strftime("%Y-%m"),
                    amount=previous,
                    icon="calendar",
                    color="#edf0ea",
                ),
            ]
            card.text = (
                f"本期 {yuan(total)}，上期 {yuan(previous)}，"
                f"{'增加' if total >= previous else '减少'} {yuan(abs(total - previous))}。"
            )
            if not previous_count:
                card.text += "上期没有对应记录，不能据此推算增长率或消费趋势。"
            card.footnote = (
                f"本期 {start} 至 {end}；上期 {prev_start} 至 {prev_end}。"
                + ("按上月同期比较。" if same_period else "上期按完整自然月比较。")
                + "结果来自已记录账单，可能不完整。"
            )
            return card
        if not count:
            card.text = "这个范围内还没有对应记录，暂时无法分析花费原因或趋势。"
            if threshold is not None:
                card.footnote += f"仅统计严格超过 {yuan(threshold)} 的{label}。"
            return card
        if plan.action in {"details", "large"}:
            card.title += " · " + ("大额明细" if plan.action == "large" else "明细")
            for record in records:
                name, icon, color = CATEGORIES[record.category_id]
                card.rows.append(
                    AnalysisRow(
                        label=record.note or name,
                        amount=record.amount,
                        icon=icon,
                        color=color,
                        detail=f"{record.date}{' ' + record.time if record.time else ''} · {name}",
                    )
                )
            card.footnote += f"按金额降序展示前 {len(card.rows)} 笔，总额与笔数包含全部匹配记录。"
            if threshold is not None:
                card.footnote += f"金额须严格超过 {yuan(threshold)}。"
            return card
        for category_id, amount, group_count in groups:
            name, icon, color = CATEGORIES[category_id]
            percent = float(
                (Decimal(amount) * 100 / Decimal(total)).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
            )
            card.rows.append(
                AnalysisRow(
                    label=name,
                    amount=int(amount),
                    percent=percent,
                    icon=icon,
                    color=color,
                    detail=f"{group_count} 笔{label}",
                )
            )
        card.title += " · " + ("消费建议" if plan.action == "advice" else "分类分布")
        card.footnote += "分类占比保留一位小数，四舍五入后合计可能不等于百分之百。"
        return card
