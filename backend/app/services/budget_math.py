"""家庭预算纯计算；不读取数据库、系统时钟或实际账户余额。"""

import calendar
import re
from collections.abc import Sequence
from datetime import date

from app.schemas.budget import (
    MAX_BUDGET_DATE,
    MAX_SAFE_INTEGER,
    MIN_BUDGET_DATE,
    MONTH_PATTERN,
    BudgetFundingPreview,
    BudgetGroupTotal,
    BudgetMonthAmount,
    BudgetMonthOverride,
    BudgetPlan,
    BudgetRevision,
    BudgetTarget,
    BudgetTotals,
)


def _integer(value: int, *, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}须为 {minimum} 至 {maximum} 范围内的整数")
    return value


def _date(value: date) -> date:
    if type(value) is not date or not MIN_BUDGET_DATE <= value <= MAX_BUDGET_DATE:
        raise ValueError("日期仅支持 2000-01-01 至 2100-12-31 的自然日")
    return value


def _month(value: str) -> date:
    if not isinstance(value, str) or re.fullmatch(MONTH_PATTERN, value) is None:
        raise ValueError("月份须为 2000-01 至 2100-12 范围内的 YYYY-MM")
    return date.fromisoformat(value + "-01")


def months_between(start_month: str, end_month: str) -> tuple[str, ...]:
    """返回包含首尾的有效月份；拒绝颠倒区间，不按 30 天估算月份。"""
    start, end = _month(start_month), _month(end_month)
    first, last = start.year * 12 + start.month - 1, end.year * 12 + end.month - 1
    if first > last:
        raise ValueError("开始月份不能晚于结束月份")
    return tuple(f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(first, last + 1))


def due_date_in_month(month: str, due_day: int) -> date:
    """如 31 日在本月不存在，使用本月最后一天，不改变原约定日。"""
    start = _month(month)
    day = _integer(due_day, minimum=1, maximum=31, label="月缴费日")
    return start.replace(day=min(day, calendar.monthrange(start.year, start.month)[1]))


def recurring_due_date(anchor_date: date, cycle_months: int, occurrence: int) -> date:
    """从原始锚点计算第 N 期，避免二月截短后后续日期逐步漂移。"""
    anchor = _date(anchor_date)
    if type(cycle_months) is not int or cycle_months not in (1, 6, 12):
        raise ValueError("缴费周期须为整数 1、6 或 12 个月")
    number = _integer(occurrence, minimum=0, maximum=1211, label="缴费期序号")
    index = anchor.year * 12 + anchor.month - 1 + cycle_months * number
    year, month = index // 12, index % 12 + 1
    if not 2000 <= year <= 2100:
        raise ValueError("推算的缴费日期超出 2000 至 2100 年")
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def allocate_cents(total_amount: int, start_month: str, end_month: str) -> list[BudgetMonthAmount]:
    """按月份精确均摊，余分依次分配给较早月份；零金额也保留月份。"""
    total = _integer(total_amount, minimum=0, maximum=MAX_SAFE_INTEGER, label="待均摊金额")
    months = months_between(start_month, end_month)
    base, remainder = divmod(total, len(months))
    return [
        BudgetMonthAmount(month=month, amount=base + (index < remainder))
        for index, month in enumerate(months)
    ]


def summarize_budget(plan: BudgetPlan) -> BudgetTotals:
    """仅对叶子项目求和；储蓄和预留不混入当月支出额度。"""
    value = BudgetPlan.model_validate(plan.model_dump())
    by_kind = {"expense": 0, "reserve": 0, "saving": 0}
    by_group = dict.fromkeys((group.id for group in value.groups), 0)
    for item in value.items:
        by_kind[item.kind] += item.monthly_amount
        by_group[item.group_id] += item.monthly_amount
    total = sum(by_kind.values())
    return BudgetTotals(
        planned_income=value.planned_income,
        expense_amount=by_kind["expense"],
        reserve_amount=by_kind["reserve"],
        saving_amount=by_kind["saving"],
        total_allocated=total,
        unallocated=value.planned_income - total,
        group_totals=[
            BudgetGroupTotal(group_id=key, amount=amount) for key, amount in by_group.items()
        ],
    )


def apply_month_override(plan: BudgetPlan, override: BudgetMonthOverride) -> BudgetPlan:
    """返回单月计划副本，不修改方案；未覆盖字段继承，零元是有效覆盖。"""
    value = BudgetPlan.model_validate(plan.model_dump())
    changes = BudgetMonthOverride.model_validate(override.model_dump())
    amounts = {item.item_id: item.amount for item in changes.item_amounts}
    if amounts.keys() - {item.id for item in value.items}:
        raise ValueError("单月覆盖只能引用本方案的预算项目")
    payload = value.model_dump()
    if changes.planned_income is not None:
        payload["planned_income"] = changes.planned_income
    for item in payload["items"]:
        if item["id"] in amounts:
            item["monthly_amount"] = amounts[item["id"]]
    return BudgetPlan.model_validate(payload)


def effective_revision(revisions: Sequence[BudgetRevision], month: str) -> BudgetRevision | None:
    """按生效月份选择版本；首次生效前返回空，不借用未来方案补造历史。"""
    _month(month)
    values = [BudgetRevision.model_validate(value.model_dump()) for value in revisions]
    if len({value.effective_month for value in values}) != len(values):
        raise ValueError("同一账本的方案生效月份不能重复")
    if len({value.id for value in values}) != len(values):
        raise ValueError("预算方案版本 ID 不能重复")
    eligible = [value for value in values if value.effective_month <= month]
    return max(eligible, key=lambda value: value.effective_month) if eligible else None


def target_funding_preview(target: BudgetTarget, *, as_of: date) -> BudgetFundingPreview:
    """只测算查询日后仍需筹集的资金，不把月计划或时间流逝算作实际存入。"""
    value = BudgetTarget.model_validate(target.model_dump())
    today = _date(as_of)
    funded = _integer(
        value.available_amount + value.used_amount,
        minimum=-MAX_SAFE_INTEGER,
        maximum=MAX_SAFE_INTEGER,
        label="本期已筹集金额",
    )
    gap = _integer(
        max(value.target_amount - funded, 0),
        minimum=0,
        maximum=MAX_SAFE_INTEGER,
        label="目标缺口",
    )
    surplus = max(funded - value.target_amount, 0)
    deadline_passed = today > value.target_date
    start = max(value.start_date, today)
    schedule = (
        []
        if deadline_passed
        else allocate_cents(gap, start.strftime("%Y-%m"), value.target_date.strftime("%Y-%m"))
    )
    return BudgetFundingPreview(
        funded_amount=funded,
        gap_amount=gap,
        surplus_amount=surplus,
        remaining_months=len(schedule),
        overdue=deadline_passed and gap > 0,
        additional_schedule=schedule,
    )
