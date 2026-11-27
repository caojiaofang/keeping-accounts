"""只匹配日期、自带范围且没有歧义的简短支出问题。"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.category_catalog import CATEGORIES
from app.schemas.chat_v2 import ChatContextV2, ContextEdit, ContextPatch, DateSelection, EntryFilter
from app.schemas.ledger import EXPENSE_CATEGORIES
from app.services.analysis_v2 import resolve_date_range

PERIODS = {
    "本月": DateSelection(kind="this_month"),
    "这个月": DateSelection(kind="this_month"),
    "上月": DateSelection(kind="last_month"),
    "上个月": DateSelection(kind="last_month"),
    "最近七天": DateSelection(kind="last_n_days", days=7),
    "最近7天": DateSelection(kind="last_n_days", days=7),
}
EXPENSE_NAMES = {CATEGORIES[key][0]: key for key in EXPENSE_CATEGORIES}
PERIOD_PATTERN = "|".join(PERIODS)
CATEGORY_PATTERN = "|".join(re.escape(name) for name in sorted(EXPENSE_NAMES))
TOTAL_QUESTION = re.compile(
    rf"(?P<period>{PERIOD_PATTERN})的?(?:(?P<category>{CATEGORY_PATTERN})(?:分类)?)?"
    r"(?:花了多少|(?:花费|支出)(?:合计|一共)?多少)"
)
GROUP_QUESTION = re.compile(rf"(?P<period>{PERIOD_PATTERN})的花费主要集中在哪")


@dataclass(frozen=True)
class QuickSummary:
    patch: ContextPatch
    group_by: Literal["category"] | None = None


def quick_summary(
    question: str, context: ChatContextV2, *, today: date, max_days: int
) -> QuickSummary | None:
    # 有既有筛选时交给正常规划，避免遗漏账户、备注或比较等上下文。
    if context.previous_range or context.filters != EntryFilter(
        date_range=context.filters.date_range
    ):
        return None
    question = question.rstrip("？?。")
    grouped = GROUP_QUESTION.fullmatch(question)
    match = grouped or TOTAL_QUESTION.fullmatch(question)
    if match is None:
        return None

    dates = resolve_date_range(PERIODS[match["period"]], today=today, max_days=max_days)
    edits = [
        ContextEdit(field="dateRange", op="replace", value=dates.date_range.model_dump(mode="json"))
    ]
    category = match.groupdict().get("category")
    if category:
        edits.append(
            ContextEdit(field="categoryIds", op="replace", value=[EXPENSE_NAMES[category]])
        )
    return QuickSummary(ContextPatch(edits=edits), "category" if grouped else None)
