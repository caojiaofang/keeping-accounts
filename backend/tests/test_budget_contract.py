"""固定预算请求与响应边界及新旧接口兼容，不访问真实数据库。"""

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.schemas.budget import (
    BudgetMonthOverride,
    BudgetMonthResponse,
    BudgetPlan,
    BudgetRevision,
    BudgetTarget,
    SaveBudgetRevision,
)
from app.services.budget_math import summarize_budget

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/family-budget-plan.json"


@pytest.fixture
def payload():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_documented_plan_and_save_contract_roundtrip(payload):
    body = SaveBudgetRevision.model_validate(
        {
            "requestId": str(uuid4()),
            "version": 0,
            "effectiveMonth": "2026-11",
            "impactToken": "server-preview-token",
            "plan": payload,
        }
    )
    saved = body.model_dump(mode="json", by_alias=True)
    assert saved["plan"]["plannedIncome"] == 1560000
    assert len(saved["plan"]["items"]) == 14
    assert "effective_month" not in saved and saved["version"] == 0
    assert SaveBudgetRevision.model_validate_json(json.dumps(saved)) == body


@pytest.mark.parametrize("field", ["userId", "ownerId", "createdBy", "bookId", "totals"])
def test_plan_mutation_does_not_accept_identity_or_trusted_totals(payload, field):
    with pytest.raises(ValidationError):
        SaveBudgetRevision.model_validate(
            {
                "requestId": str(uuid4()),
                "version": 0,
                "effectiveMonth": "2026-11",
                "impactToken": "server-preview-token",
                "plan": payload,
                field: str(uuid4()),
            }
        )


@pytest.mark.parametrize("value", [True, -1, 1.2, "52000", 1000000000, None])
def test_monthly_amount_is_strict_bounded_integer(payload, value):
    payload["items"][0]["monthlyAmount"] = value
    with pytest.raises(ValidationError):
        BudgetPlan.model_validate(payload)


@pytest.mark.parametrize(
    "value", ["1999-12", "2101-01", "2026-13", "2026-1", "2026-01-01", "２０２６-１１"]
)
def test_effective_month_has_real_supported_year_and_month(payload, value):
    with pytest.raises(ValidationError):
        BudgetRevision(id=uuid4(), effective_month=value, plan=payload)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_group", "duplicate_item", "unknown_group", "group_amount", "item_children"],
)
def test_group_and_leaf_structure_prevents_double_counting(payload, mutation):
    if mutation == "duplicate_group":
        payload["groups"].append(payload["groups"][0])
    elif mutation == "duplicate_item":
        payload["items"].append(payload["items"][0])
    elif mutation == "unknown_group":
        payload["items"][0]["groupId"] = str(uuid4())
    elif mutation == "group_amount":
        payload["groups"][0]["amount"] = 484000
    else:
        payload["items"][0]["children"] = []
    with pytest.raises(ValidationError):
        BudgetPlan.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "missing_reserve",
        "saving_with_reserve",
        "reserve_due_day",
        "boolean_cycle",
        "string_cycle",
        "unsupported_cycle",
        "invalid_due_day",
    ],
)
def test_kind_and_payment_cycle_are_distinct_and_consistent(payload, change):
    reserve = payload["items"][3]
    if change == "missing_reserve":
        reserve.pop("reserve")
    elif change == "saving_with_reserve":
        reserve["kind"] = "saving"
    elif change == "reserve_due_day":
        reserve["dueDay"] = 15
    elif change == "invalid_due_day":
        payload["items"][0]["dueDay"] = 32
    else:
        reserve["reserve"]["cycleMonths"] = {
            "boolean_cycle": True,
            "string_cycle": "12",
            "unsupported_cycle": 3,
        }[change]
    with pytest.raises(ValidationError):
        BudgetPlan.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    ["2026-02-30", "1999-12-31", "2101-01-01", "2026-11-06T00:00:00", 0, datetime(2026, 11, 6)],
)
def test_target_date_rejects_invalid_days_timestamps_and_datetimes(value):
    with pytest.raises(ValidationError):
        BudgetTarget.model_validate(
            {
                "startDate": "2026-01-01",
                "targetDate": value,
                "targetAmount": 5000000,
            }
        )


def test_zero_monthly_amount_is_allowed_but_zero_or_reversed_target_is_not(payload):
    payload["items"][0]["monthlyAmount"] = 0
    assert BudgetPlan.model_validate(payload).items[0].monthly_amount == 0
    for changes in ({"targetAmount": 0}, {"startDate": "2027-01-01"}):
        with pytest.raises(ValidationError):
            BudgetTarget.model_validate(
                {
                    "startDate": "2026-01-01",
                    "targetDate": "2026-12-31",
                    "targetAmount": 5000000,
                    **changes,
                }
            )


def test_month_override_cannot_repeat_an_item(payload):
    item = {"itemId": payload["items"][0]["id"], "amount": 0}
    with pytest.raises(ValidationError):
        BudgetMonthOverride.model_validate({"itemAmounts": [item, item]})


def test_month_response_states_do_not_turn_preview_into_saved_budget(payload):
    plan = BudgetPlan.model_validate(payload)
    base = {
        "bookId": str(uuid4()),
        "month": "2026-11",
        "planVersion": 1,
        "snapshotId": None,
        "version": 0,
        "sourceRevisionId": str(uuid4()),
        "plan": payload,
        "override": {},
        "totals": summarize_budget(plan).model_dump(mode="json", by_alias=True),
        "generatedAt": 1793923200000,
    }
    preview = BudgetMonthResponse.model_validate({**base, "state": "preview"})
    assert preview.snapshot_id is None and preview.version == 0
    confirmed = BudgetMonthResponse.model_validate(
        {
            **base,
            "state": "confirmed",
            "snapshotId": str(uuid4()),
            "version": 1,
        }
    )
    assert confirmed.state == "confirmed" and confirmed.snapshot_id
    empty = BudgetMonthResponse.model_validate(
        {
            **base,
            "state": "not_set",
            "plan": None,
            "totals": None,
            "sourceRevisionId": None,
        }
    )
    assert empty.plan is None
    for changes in (
        {"state": "confirmed"},
        {"state": "not_set"},
        {"state": "preview", "snapshotId": str(uuid4())},
        {"state": "preview", "sourceRevisionId": None},
        {"state": "preview", "plan": None},
        {"state": "preview", "version": 1},
        {"state": "preview", "planVersion": 0},
        {"state": "preview", "override": {"plannedIncome": 0}},
    ):
        with pytest.raises(ValidationError):
            BudgetMonthResponse.model_validate({**base, **changes})


def test_budget_routes_keep_legacy_api_and_include_actual_execution():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    paths = app.openapi()["paths"]
    assert "/api/v1/books/{book_id}/budget" in paths
    prefix = "/api/v1/books/{book_id}/budgets"
    assert {path for path in paths if "/budgets" in path} == {
        prefix + "/plan",
        prefix + "/plan/preview",
        prefix + "/months/{month}",
        prefix + "/monthly/{month}",
        prefix + "/monthly/{month}/execution",
        prefix + "/monthly/{month}/summary",
        prefix + "/months/{month}/confirm",
        prefix + "/equalizations/preview",
        prefix + "/equalizations",
        prefix + "/items",
        prefix + "/items/{item_id}",
        prefix + "/items/{item_id}/funds",
        prefix + "/items/{item_id}/funds/{record_id}/correct",
        prefix + "/items/{item_id}/goals",
        prefix + "/items/{item_id}/goals/{goal_id}",
        prefix + "/items/{item_id}/funding-preview",
        prefix + "/entries/{entry_id}/link",
        prefix + "/months/{month}/execution",
        prefix + "/months/{month}/entries",
    }
    assert set(paths[prefix + "/plan"]) == {"get", "put"}
    assert set(paths[prefix + "/months/{month}"]) == {"get", "put"}
    assert set(paths[prefix + "/entries/{entry_id}/link"]) == {"get", "put", "delete"}
