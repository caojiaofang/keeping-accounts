"""账单关联防伪造、严格版本以及 HTTP 正文／路径绑定，不访问外部服务。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.budget import get_budget_execution_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    BudgetEntryLinkState,
    BudgetEntryPage,
    RemoveBudgetEntryLink,
    SetBudgetEntryLink,
)


@pytest.mark.parametrize("schema", [SetBudgetEntryLink, RemoveBudgetEntryLink])
@pytest.mark.parametrize(
    "changes",
    [
        {"entryVersion": 0},
        {"entryVersion": True},
        {"entryVersion": "1"},
        {"entryVersion": 1.5},
        {"entryVersion": MAX_SAFE_INTEGER + 1},
        {"linkVersion": -1},
        {"linkVersion": False},
        {"linkVersion": "0"},
        {"linkVersion": MAX_SAFE_INTEGER + 1},
        {"amount": 100},
        {"date": "2026-11-06"},
        {"createdBy": str(uuid4())},
        {"fundVersion": 0},
        {"bookId": str(uuid4())},
    ],
)
def test_link_mutations_reject_stale_shapes_and_forged_facts(schema, changes):
    body = {"requestId": str(uuid4()), "entryVersion": 1, "linkVersion": 0}
    if schema is SetBudgetEntryLink:
        body["itemId"] = str(uuid4())
    with pytest.raises(ValidationError):
        schema.model_validate({**body, **changes})


def test_link_api_binds_scope_and_accepts_delete_body_without_trusting_money():
    book_id, entry_id, item_id = uuid4(), uuid4(), uuid4()
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    result = BudgetEntryLinkState(
        book_id=book_id, entry_id=entry_id, entry_version=1, link_version=0, link=None
    )
    service = SimpleNamespace(
        read_link=AsyncMock(return_value=result),
        change_link=AsyncMock(return_value=result),
        entry_page=AsyncMock(
            return_value=BudgetEntryPage(book_id=book_id, entries=[], next_cursor=None)
        ),
    )
    app.dependency_overrides[get_budget_execution_service] = lambda: service
    path = f"/api/v1/books/{book_id}/budgets/entries/{entry_id}/link"
    body = {"requestId": str(uuid4()), "entryVersion": 1, "linkVersion": 0}
    with TestClient(app) as client:
        assert client.get(path).status_code == 200
        service.read_link.assert_awaited_once_with(book_id, entry_id)
        assert client.put(path, json={**body, "itemId": str(item_id)}).status_code == 200
        args = service.change_link.call_args.args
        assert args[:2] == (book_id, entry_id) and isinstance(args[2], SetBudgetEntryLink)
        assert client.request("DELETE", path, json=body).status_code == 200
        assert isinstance(service.change_link.call_args.args[2], RemoveBudgetEntryLink)
        forged = client.put(
            path, json={**body, "itemId": str(item_id), "usedAmount": "private-fact"}
        )
        assert forged.status_code == 422 and "private-fact" not in forged.text
        prefix = f"/api/v1/books/{book_id}/budgets/months"
        assert client.get(prefix + "/2026-1/entries").status_code == 422
        assert client.get(prefix + "/2026-11/entries?limit=0").status_code == 422
        assert (
            client.get(prefix + f"/2026-11/entries?itemId={item_id}&unlinkedOnly=true").status_code
            == 422
        )
        assert client.get(prefix + "/2026-11/entries?unlinkedOnly=true&limit=5").status_code == 200
        service.entry_page.assert_awaited_once_with(
            book_id, month="2026-11", item_id=None, unlinked_only=True, before=None, limit=5
        )
