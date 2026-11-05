"""可信日期、边界、比较口径和筛选规范化，不调用模型或数据库。"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.schemas.chat_v2 import ComparisonSelection, DateRange, DateSelection, EntryFilter
from app.services.analysis_v2 import (
    normalize_filter,
    resolve_comparison_range,
    resolve_date_range,
    validate_date_range,
)

TODAY = date(2026, 10, 27)


@pytest.mark.parametrize(
    "selection,start,end",
    [
        ({"kind": "today"}, "2026-10-27", "2026-10-27"),
        ({"kind": "yesterday"}, "2026-10-26", "2026-10-26"),
        ({"kind": "last_n_days", "days": 7}, "2026-10-21", "2026-10-27"),
        ({"kind": "last_n_days", "days": 1}, "2026-10-27", "2026-10-27"),
        ({"kind": "this_week"}, "2026-10-26", "2026-10-27"),
        ({"kind": "last_week"}, "2026-10-19", "2026-10-25"),
        ({"kind": "this_month"}, "2026-10-01", "2026-10-27"),
        ({"kind": "last_month"}, "2026-09-01", "2026-09-30"),
        ({"kind": "this_year"}, "2026-01-01", "2026-10-27"),
        ({"kind": "last_year"}, "2025-01-01", "2025-12-31"),
        ({"kind": "month", "month": "2024-02"}, "2024-02-01", "2024-02-29"),
        ({"kind": "month", "month": "2026-10"}, "2026-10-01", "2026-10-27"),
        (
            {"kind": "range", "dateRange": {"start": "2025-12-31", "end": "2026-01-02"}},
            "2025-12-31",
            "2026-01-02",
        ),
    ],
)
def test_date_presets(selection, start, end):
    result = resolve_date_range(DateSelection.model_validate(selection), today=TODAY)
    assert result.date_range == DateRange(start=start, end=end)


@pytest.mark.parametrize(
    "today,selection,start,end",
    [
        (date(2026, 1, 1), {"kind": "last_month"}, "2025-12-01", "2025-12-31"),
        (date(2026, 1, 1), {"kind": "this_week"}, "2025-12-29", "2026-01-01"),
        (date(2024, 3, 1), {"kind": "last_month"}, "2024-02-01", "2024-02-29"),
        (date(2100, 3, 1), {"kind": "last_month"}, "2100-02-01", "2100-02-28"),
        (date(2025, 1, 1), {"kind": "last_year"}, "2024-01-01", "2024-12-31"),
        (date(2026, 10, 26), {"kind": "this_week"}, "2026-10-26", "2026-10-26"),
        (date(2026, 10, 25), {"kind": "this_week"}, "2026-10-19", "2026-10-25"),
    ],
)
def test_cross_year_leap_year_and_week_boundary(today, selection, start, end):
    assert resolve_date_range(DateSelection(**selection), today=today).date_range == DateRange(
        start=start, end=end
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("previous_period", ("2026-10-14", "2026-10-20")),
        ("previous_month_same_period", ("2026-09-21", "2026-09-27")),
        ("previous_month_full", ("2026-09-01", "2026-09-30")),
    ],
)
def test_comparison_preserves_concrete_ranges(kind, expected):
    current = DateRange(start="2026-10-21", end="2026-10-27")
    result = resolve_comparison_range(current, ComparisonSelection(kind=kind), today=TODAY)
    assert result.date_range == DateRange(start=expected[0], end=expected[1])


@pytest.mark.parametrize(
    "current,kind,expected",
    [
        (("2024-03-01", "2024-03-31"), "previous_month_same_period", ("2024-02-01", "2024-02-29")),
        (("2025-03-30", "2025-03-31"), "previous_month_same_period", ("2025-02-28", "2025-02-28")),
        (("2026-01-01", "2026-01-15"), "previous_month_same_period", ("2025-12-01", "2025-12-15")),
        (("2026-01-01", "2026-01-01"), "previous_period", ("2025-12-31", "2025-12-31")),
        (("2026-09-28", "2026-10-04"), "previous_period", ("2026-09-21", "2026-09-27")),
    ],
)
def test_comparison_short_months_and_cross_year(current, kind, expected):
    result = resolve_comparison_range(
        DateRange(start=current[0], end=current[1]), ComparisonSelection(kind=kind), today=TODAY
    )
    assert result.date_range == DateRange(start=expected[0], end=expected[1])


def test_future_end_is_visible_and_entire_future_is_rejected():
    result = validate_date_range(DateRange(start="2026-10-21", end="2026-11-01"), today=TODAY)
    assert result.date_range.end == TODAY
    assert "2026-10-27" in result.notices[0]
    with pytest.raises(ServiceError, match="未来"):
        resolve_date_range(DateSelection(kind="month", month="2026-11"), today=TODAY)
    previous = resolve_comparison_range(
        DateRange(start="2026-10-21", end="2026-10-27"),
        ComparisonSelection(
            kind="range", date_range=DateRange(start="2026-09-01", end="2026-09-30")
        ),
        today=TODAY,
    )
    assert previous.date_range.end == date(2026, 9, 30)


def test_range_limit_includes_both_endpoints_and_configuration():
    leap_year = DateRange(start="2024-01-01", end="2024-12-31")
    assert validate_date_range(leap_year, today=TODAY).date_range == leap_year
    for value, max_days in (
        (leap_year, 365),
        (DateRange(start="2023-12-31", end="2024-12-31"), 366),
    ):
        with pytest.raises(ServiceError, match="最多"):
            validate_date_range(value, today=TODAY, max_days=max_days)
    assert resolve_date_range(
        DateSelection(kind="last_n_days", days=7), today=TODAY, max_days=7
    ).date_range.start == date(2026, 10, 21)


def test_out_of_business_year_and_ambiguous_comparison():
    with pytest.raises(ServiceError, match="2000"):
        resolve_date_range(DateSelection(kind="yesterday"), today=date(2000, 1, 1))
    with pytest.raises(ServiceError, match="跨月"):
        resolve_comparison_range(
            DateRange(start="2026-09-28", end="2026-10-04"),
            ComparisonSelection(kind="previous_month_full"),
            today=TODAY,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "last_n_days"},
        {"kind": "last_n_days", "days": True},
        {"kind": "last_n_days", "days": 0},
        {"kind": "last_n_days", "days": 367},
        {"kind": "today", "days": 7},
        {"kind": "month", "month": "2026-13"},
        {"kind": "range"},
        {"kind": "today", "month": "2026-10"},
    ],
)
def test_date_selection_rejects_mismatched_or_invalid_arguments(changes):
    with pytest.raises(ValidationError):
        DateSelection.model_validate(changes)


@pytest.mark.parametrize(
    "value",
    [
        1793062800000,
        "20261027",
        "2026-10-27T00:00:00Z",
        datetime(2026, 10, 27),
        "1999-12-31",
        "2101-01-01",
    ],
)
def test_range_only_accepts_supported_calendar_dates(value):
    with pytest.raises(ValidationError):
        DateRange(start=value, end="2026-10-27")


def test_filter_normalization_deduplicates_without_broadening_scope():
    source = EntryFilter.model_validate(
        {
            "dateRange": {"start": "2026-10-21", "end": "2026-10-31"},
            "categoryIds": [" coffee ", "coffee", "food"],
            "keywords": {"include": [" 咖啡 ", "COFFEE", "coffee"], "exclude": ["退款", "退款"]},
            "accounts": [" 微信 ", "微信", "支付宝"],
            "weekdays": [7, 6, 7],
        }
    )
    value, notices = normalize_filter(source, today=TODAY)
    assert value.category_ids == ["coffee", "food"]
    assert value.keywords.include == ["咖啡", "COFFEE"]
    assert value.keywords.exclude == ["退款"]
    assert value.accounts == ["微信", "支付宝"]
    assert value.weekdays == [6, 7]
    assert value.date_range.end == TODAY and notices
    assert source.date_range.end == date(2026, 10, 31)


@pytest.mark.parametrize(
    "changes",
    [
        {"entryType": "expense", "categoryIds": ["salary"]},
        {"entryType": "income", "categoryIds": ["coffee"]},
        {"categoryIds": ["unknown"]},
        {"amountMin": 100, "amountMax": 100, "minInclusive": False},
    ],
)
def test_filter_business_errors(changes):
    with pytest.raises(ServiceError) as error:
        normalize_filter(
            EntryFilter.model_validate(
                {
                    "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
                    **changes,
                }
            ),
            today=TODAY,
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"keywords": {"include": ["   "]}},
        {"accounts": [" "]},
        {"categoryIds": [" "]},
        {"amountMax": 1000000000},
        {"minInclusive": "false"},
    ],
)
def test_invalid_filter_fields_do_not_turn_into_unrestricted_queries(changes):
    with pytest.raises(ValidationError):
        EntryFilter.model_validate(
            {"dateRange": {"start": "2026-10-21", "end": "2026-10-27"}, **changes}
        )
