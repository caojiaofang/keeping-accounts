"""月份路径、覆盖及均摊输入约束；不访问数据库或外部服务。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.budget import get_budget_month_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.budget import BudgetMonthResponse, PreviewBudgetEqualization


def equalization_body():
    return {
        "version": 1,
        "itemId": str(uuid4()),
        "totalAmount": 610000,
        "startMonth": "2026-11",
        "endMonth": "2027-10",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"totalAmount": 1.1},
        {"totalAmount": "610000"},
        {"totalAmount": -1},
        {"totalAmount": 1000000000},
        {"startMonth": "2026-1"},
        {"endMonth": "2026-10"},
        {"endMonth": "2101-01"},
        {"startMonth": "2026-11", "endMonth": "2036-11"},
        {"userId": "private-input"},
        {"schedule": []},
    ],
)
def test_equalization_rejects_unsafe_money_range_and_forged_schedule(changes):
    with pytest.raises(ValidationError):
        PreviewBudgetEqualization.model_validate({**equalization_body(), **changes})


def test_equalization_accepts_zero_and_at_most_120_inclusive_months():
    value = PreviewBudgetEqualization.model_validate(
        {
            **equalization_body(),
            "totalAmount": 0,
            "startMonth": "2000-01",
            "endMonth": "2009-12",
        }
    )
    assert value.total_amount == 0


def test_month_path_validation_and_scope_and_extra_fields():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    book_id = uuid4()
    response = BudgetMonthResponse(
        book_id=book_id,
        month="2026-11",
        state="not_set",
        plan_version=0,
        snapshot_id=None,
        version=0,
        source_revision_id=None,
        plan=None,
        override={},
        totals=None,
        generated_at=0,
    )
    service = SimpleNamespace(
        read_month=AsyncMock(return_value=response), confirm_month=AsyncMock()
    )
    app.dependency_overrides[get_budget_month_service] = lambda: service
    with TestClient(app) as client:
        prefix = f"/api/v1/books/{book_id}/budgets/months"
        assert client.get(prefix + "/2026-11").json()["state"] == "not_set"
        service.read_month.assert_awaited_once_with(book_id, "2026-11")
        for month in ("2026-1", "2026-13", "1999-12", "2101-01"):
            assert client.get(prefix + "/" + month).status_code == 422
        result = client.post(
            prefix + "/2026-11/confirm",
            json={
                "requestId": str(uuid4()),
                "sourceRevisionId": str(uuid4()),
                "planVersion": 1,
                "override": {"plannedIncome": 0},
                "userId": "private-input",
            },
        )
        assert result.status_code == 422 and "private-input" not in result.text
        service.confirm_month.assert_not_awaited()
