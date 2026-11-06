"""预算金额、月份与目标的离线业务验证，不访问数据库或读取系统日期。"""

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    BudgetMonthOverride,
    BudgetPlan,
    BudgetRevision,
    BudgetTarget,
)
from app.schemas.ledger import MAX_CENTS
from app.services.budget_math import (
    allocate_cents,
    apply_month_override,
    due_date_in_month,
    effective_revision,
    months_between,
    recurring_due_date,
    summarize_budget,
    target_funding_preview,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/family-budget-plan.json"


@pytest.fixture
def family_plan():
    return BudgetPlan.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))


def test_family_totals_keep_spending_reserve_and_saving_separate(family_plan):
    totals = summarize_budget(family_plan)
    assert totals.planned_income == totals.total_allocated == 1560000
    assert totals.unallocated == 0
    assert totals.expense_amount == 888000
    assert totals.reserve_amount == 52000
    assert totals.saving_amount == 620000
    assert [group.amount for group in totals.group_totals] == [484000, 52000, 620000, 404000]
    assert totals.reserve_amount * 12 - 610000 == 14000
    assert family_plan.items[5].monthly_amount * 12 - 5000000 == 40000
    assert family_plan.items[5].monthly_amount * 6 == 2520000


@pytest.mark.parametrize("income,remaining", [(0, -1560000), (1600000, 40000)])
def test_overallocated_and_unallocated_plans_are_preserved(family_plan, income, remaining):
    value = family_plan.model_copy(update={"planned_income": income})
    assert summarize_budget(value).unallocated == remaining


def test_zero_plan_and_large_totals_do_not_overflow_or_require_fake_income(family_plan):
    assert summarize_budget(BudgetPlan(planned_income=0)).total_allocated == 0
    payload = family_plan.model_dump()
    for item in payload["items"]:
        item["monthly_amount"] = MAX_CENTS
    assert summarize_budget(BudgetPlan.model_validate(payload)).total_allocated == 14 * MAX_CENTS


def test_exact_annual_allocation_preserves_all_cents():
    schedule = allocate_cents(610000, "2026-11", "2027-10")
    assert len(schedule) == 12
    assert [item.amount for item in schedule] == [50834] * 4 + [50833] * 8
    assert schedule[0].month == "2026-11" and schedule[-1].month == "2027-10"
    assert sum(item.amount for item in schedule) == 610000


@pytest.mark.parametrize("total", [0, 1, 11, 12, 13, 5000000, MAX_SAFE_INTEGER])
def test_allocations_are_integer_balanced_and_keep_zero_months(total):
    amounts = [item.amount for item in allocate_cents(total, "2026-01", "2026-12")]
    assert sum(amounts) == total and len(amounts) == 12
    assert all(type(value) is int and value >= 0 for value in amounts)
    assert max(amounts) - min(amounts) <= 1
    assert amounts == sorted(amounts, reverse=True)


@pytest.mark.parametrize("amount", [-1, True, "610000", 1.5, MAX_SAFE_INTEGER + 1])
def test_allocation_rejects_coercion_and_unsafe_money(amount):
    with pytest.raises(ValueError):
        allocate_cents(amount, "2026-01", "2026-12")


def test_month_ranges_are_inclusive_and_handle_year_boundaries():
    assert months_between("2026-12", "2027-02") == ("2026-12", "2027-01", "2027-02")
    assert months_between("2024-02", "2024-02") == ("2024-02",)
    assert len(months_between("2000-01", "2100-12")) == 1212


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-12", "2026-11"),
        ("2026-1", "2026-12"),
        ("1999-12", "2026-01"),
        ("2100-01", "2101-01"),
        ("2026-00", "2026-01"),
        (None, "2026-12"),
    ],
)
def test_month_ranges_reject_invalid_values(start, end):
    with pytest.raises(ValueError):
        months_between(start, end)


@pytest.mark.parametrize(
    "month,day,expected",
    [
        ("2024-02", 31, date(2024, 2, 29)),
        ("2026-02", 31, date(2026, 2, 28)),
        ("2100-02", 29, date(2100, 2, 28)),
        ("2026-04", 31, date(2026, 4, 30)),
        ("2026-03", 31, date(2026, 3, 31)),
        ("2026-01", 1, date(2026, 1, 1)),
    ],
)
def test_month_end_due_days(month, day, expected):
    assert due_date_in_month(month, day) == expected


def test_recurring_dates_keep_original_anchor_after_short_months():
    anchor = date(2024, 2, 29)
    assert recurring_due_date(anchor, 12, 1) == date(2025, 2, 28)
    assert recurring_due_date(anchor, 12, 4) == date(2028, 2, 29)
    anchor = date(2026, 1, 31)
    assert recurring_due_date(anchor, 1, 1) == date(2026, 2, 28)
    assert recurring_due_date(anchor, 1, 2) == date(2026, 3, 31)
    assert recurring_due_date(date(2026, 8, 31), 6, 1) == date(2027, 2, 28)
    assert recurring_due_date(date(2026, 8, 31), 6, 2) == date(2027, 8, 31)


@pytest.mark.parametrize(
    "anchor,cycle,occurrence",
    [
        (date(2100, 12, 31), 1, 1),
        (date(2026, 1, 1), True, 0),
        (date(2026, 1, 1), 3, 0),
        (date(2026, 1, 1), 12, -1),
        (date(2026, 1, 1), 12, False),
        (datetime(2026, 1, 1), 12, 0),
    ],
)
def test_recurring_dates_reject_invalid_or_out_of_bounds_values(anchor, cycle, occurrence):
    with pytest.raises(ValueError):
        recurring_due_date(anchor, cycle, occurrence)


def test_single_month_override_does_not_modify_source_or_next_month(family_plan):
    original = family_plan.model_dump(mode="json")
    changes = BudgetMonthOverride.model_validate(
        {
            "plannedIncome": 0,
            "itemAmounts": [{"itemId": str(family_plan.items[-1].id), "amount": 60000}],
        }
    )
    current = apply_month_override(family_plan, changes)
    assert current.planned_income == 0 and current.items[-1].monthly_amount == 60000
    assert summarize_budget(current).total_allocated == 1580000
    assert family_plan.model_dump(mode="json") == original
    following = apply_month_override(family_plan, BudgetMonthOverride())
    assert following.model_dump(mode="json") == original
    current.items[0].name = "仅修改副本"
    assert family_plan.items[0].name == "房贷"


def test_unknown_month_override_item_is_not_silently_ignored(family_plan):
    with pytest.raises(ValueError, match="本方案"):
        apply_month_override(
            family_plan,
            BudgetMonthOverride.model_validate(
                {
                    "itemAmounts": [{"itemId": str(uuid4()), "amount": 0}],
                }
            ),
        )


def test_revisions_preserve_history_and_respect_later_scheduled_changes(family_plan):
    revisions = [
        BudgetRevision(id=uuid4(), effective_month=month, plan=family_plan)
        for month in ("2027-03", "2026-11", "2027-01")
    ]
    assert effective_revision(revisions, "2026-10") is None
    assert effective_revision(revisions, "2026-12").id == revisions[1].id
    assert effective_revision(revisions, "2027-02").id == revisions[2].id
    assert effective_revision(revisions, "2027-03").id == revisions[0].id
    assert effective_revision([], "2026-11") is None
    with pytest.raises(ValueError, match="生效月份"):
        effective_revision([revisions[0], revisions[0]], "2027-03")


@pytest.mark.parametrize(
    "as_of,available,count,gap,surplus,overdue",
    [
        (date(2026, 1, 1), 0, 12, 5000000, 0, False),
        (date(2026, 7, 15), 2000000, 6, 3000000, 0, False),
        (date(2026, 12, 31), 0, 1, 5000000, 0, False),
        (date(2027, 1, 1), 1000000, 0, 4000000, 0, True),
        (date(2026, 7, 15), 5040000, 6, 0, 40000, False),
        (date(2026, 7, 15), -10000, 6, 5010000, 0, False),
    ],
)
def test_target_funding_uses_actual_available_money(as_of, available, count, gap, surplus, overdue):
    target = BudgetTarget(
        start_date=date(2026, 1, 1),
        target_date=date(2026, 12, 31),
        target_amount=5000000,
        available_amount=available,
    )
    preview = target_funding_preview(target, as_of=as_of)
    assert (preview.remaining_months, preview.gap_amount, preview.surplus_amount) == (
        count,
        gap,
        surplus,
    )
    assert preview.overdue is overdue
    assert sum(item.amount for item in preview.additional_schedule) == (gap if count else 0)
    if as_of == date(2026, 7, 15) and available == 2000000:
        assert [item.amount for item in preview.additional_schedule] == [500000] * 6


def test_target_does_not_allocate_before_start_date_or_after_due_day():
    target = BudgetTarget.model_validate(
        {
            "startDate": "2026-11-20",
            "targetDate": "2027-04-05",
            "targetAmount": 2500000,
        }
    )
    preview = target_funding_preview(target, as_of=date(2026, 10, 1))
    assert preview.remaining_months == 6
    assert preview.additional_schedule[0].month == "2026-11"
    preview = target_funding_preview(target, as_of=date(2027, 4, 6))
    assert preview.remaining_months == 0 and preview.overdue
    assert preview.gap_amount == 2500000 and preview.additional_schedule == []


def test_pure_functions_revalidate_internal_model_copies(family_plan):
    invalid = family_plan.model_copy(update={"planned_income": True})
    with pytest.raises(ValidationError):
        summarize_budget(invalid)
    invalid = BudgetTarget.model_construct(
        start_date=date(2027, 1, 1),
        target_date=date(2026, 1, 1),
        target_amount=100,
        available_amount=0,
    )
    with pytest.raises(ValidationError):
        target_funding_preview(invalid, as_of=date(2026, 1, 1))


@pytest.mark.parametrize("day", [0, 32, True, "15", 15.0])
def test_monthly_due_day_rejects_non_integer_or_out_of_range_values(day):
    with pytest.raises(ValueError):
        due_date_in_month("2026-11", day)


def test_target_gap_rejects_unsafe_derived_result():
    target = BudgetTarget.model_validate(
        {
            "startDate": "2026-01-01",
            "targetDate": "2026-12-31",
            "targetAmount": 100,
            "availableAmount": -MAX_SAFE_INTEGER,
        }
    )
    with pytest.raises(ValueError, match="目标缺口"):
        target_funding_preview(target, as_of=date(2026, 11, 6))


def test_prepayment_keeps_goal_funding_and_surplus_after_using_saved_money():
    target = BudgetTarget.model_validate(
        {
            "startDate": "2026-01-01",
            "targetDate": "2026-12-31",
            "targetAmount": 5000000,
            "availableAmount": 40000,
            "usedAmount": 5000000,
        }
    )
    for today in (date(2026, 11, 6), date(2027, 1, 1)):
        value = target_funding_preview(target, as_of=today)
        assert value.funded_amount == 5040000
        assert value.gap_amount == 0 and value.surplus_amount == 40000
        assert not value.overdue
        assert all(item.amount == 0 for item in value.additional_schedule)


def test_target_funding_rejects_unsafe_combined_available_and_used_money():
    target = BudgetTarget.model_validate(
        {
            "startDate": "2026-01-01",
            "targetDate": "2026-12-31",
            "targetAmount": 100,
            "availableAmount": MAX_SAFE_INTEGER,
            "usedAmount": 1,
        }
    )
    with pytest.raises(ValueError, match="本期已筹集金额"):
        target_funding_preview(target, as_of=date(2026, 11, 6))
