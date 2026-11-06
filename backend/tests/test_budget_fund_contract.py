"""用途资金输入约束和余额口径，不访问外部服务。"""

from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.budget import CreateBudgetFund, CreateBudgetGoal
from app.services.budget_funds import fund_summary


def fund_body():
    return {
        "requestId": str(uuid4()),
        "fundVersion": 0,
        "id": str(uuid4()),
        "kind": "deposit",
        "amount": 420000,
        "date": "2026-11-06",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": 0},
        {"amount": -1},
        {"amount": True},
        {"amount": 1.1},
        {"amount": "420000"},
        {"amount": 1000000000},
        {"kind": "use"},
        {"fundVersion": False},
        {"date": "2026-02-30"},
        {"date": "2101-01-01"},
        {"date": "2026-11-06T00:00:00"},
        {"date": datetime(2026, 11, 6)},
        {"userId": str(uuid4())},
        {"entryId": str(uuid4())},
        {"goalPeriodId": str(uuid4())},
    ],
)
def test_fund_input_rejects_coercion_forged_attribution_and_manual_use(changes):
    with pytest.raises(ValidationError):
        CreateBudgetFund.model_validate({**fund_body(), **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"targetAmount": 0},
        {"targetAmount": "5000000"},
        {"carryInAmount": -1},
        {"carryInAmount": True},
        {"startDate": "2028-01-01"},
        {"availableAmount": 999999},
        {"usedAmount": 100},
        {"state": "closed"},
    ],
)
def test_goal_input_does_not_accept_client_balance_or_completed_state(changes):
    with pytest.raises(ValidationError):
        CreateBudgetGoal.model_validate(
            {
                "requestId": str(uuid4()),
                "fundVersion": 1,
                "id": str(uuid4()),
                "startDate": "2026-11-06",
                "targetDate": "2027-11-05",
                "targetAmount": 5000000,
                **changes,
            }
        )


def test_fund_summary_counts_opening_once_ignores_voided_and_future_and_keeps_deficit():
    rows = [
        SimpleNamespace(kind=kind, amount=amount, date=when, voided_at=voided)
        for kind, amount, when, voided in (
            ("opening", 5040000, date(2026, 11, 1), None),
            ("deposit", 420000, date(2026, 11, 6), True),
            ("use", 5000000, date(2026, 11, 6), None),
            ("deposit", 420000, date(2026, 12, 1), None),
        )
    ]
    assert fund_summary(rows, date(2026, 11, 30)).available_amount == 40000
    assert fund_summary(rows, date(2026, 12, 31)).available_amount == 460000
    assert fund_summary(rows[2:3], date(2026, 11, 30)).available_amount == -5000000
