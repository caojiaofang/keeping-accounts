"""预算接口边界、错误脱敏和业务时间规则；不访问外部服务。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.auth_deps import get_auth_service
from app.api.deps import get_db
from app.api.v1.budget import get_budget_service
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.schemas.budget import BudgetPlanResponse
from app.services.budget import BudgetService, request_hash

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/family-budget-plan.json"


def test_budget_routes_pass_book_scope_and_reject_forged_identity():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    book_id = uuid4()
    response = BudgetPlanResponse(book_id=book_id, version=0, revisions=[])
    service = SimpleNamespace(read_plan=AsyncMock(return_value=response), preview_plan=AsyncMock())
    app.dependency_overrides[get_budget_service] = lambda: service
    with TestClient(app) as client:
        result = client.get(f"/api/v1/books/{book_id}/budgets/plan")
        assert result.json() == {
            "schemaVersion": 1,
            "bookId": str(book_id),
            "version": 0,
            "revisions": [],
        }
        service.read_plan.assert_awaited_once_with(book_id)
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        for changes in ({"userId": "do-not-echo"}, {"version": True}):
            result = client.post(
                f"/api/v1/books/{book_id}/budgets/plan/preview",
                json={
                    "version": 0,
                    "effectiveMonth": "2026-11",
                    "plan": payload,
                    **changes,
                },
            )
            assert result.status_code == 422 and "do-not-echo" not in result.text
        service.preview_plan.assert_not_awaited()


def test_budget_dependency_requires_token_and_hides_database_error():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    auth = SimpleNamespace(current_user=AsyncMock(return_value=SimpleNamespace(id=uuid4())))
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=SQLAlchemyError("private-connection-detail")),
        rollback=AsyncMock(),
    )
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        path = f"/api/v1/books/{uuid4()}/budgets/plan"
        missing = client.get(path)
        assert missing.status_code == 401 and missing.headers["Cache-Control"] == "no-store"
        auth.current_user.assert_not_awaited()
        result = client.get(path, headers={"Authorization": "Bearer test-token"})
        assert result.status_code == 503 and "private-connection-detail" not in result.text
        assert result.headers["Cache-Control"] == "no-store"
        session.rollback.assert_awaited_once()


@pytest.mark.parametrize("hour,allowed", [(15, True), (16, False)])
def test_plan_date_uses_beijing_month_boundary(hour, allowed):
    now = datetime(2026, 11, 30, hour, 0, tzinfo=UTC)
    if allowed:
        assert BudgetService.validate_month("2026-11", now).month == 11
    else:
        with pytest.raises(ServiceError) as error:
            BudgetService.validate_month("2026-11", now)
        assert error.value.status_code == 422


def test_request_hash_is_stable_for_object_key_order_and_sensitive_to_values():
    assert request_hash({"version": 0, "plan": {"name": "家庭", "amount": 52000}}) == request_hash(
        {"plan": {"amount": 52000, "name": "家庭"}, "version": 0}
    )
    assert request_hash({"amount": 52000}) != request_hash({"amount": 52001})
