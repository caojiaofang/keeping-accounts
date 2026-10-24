"""离线校验业务输入和接口契约；真实 SQL 行为见集成测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.ledger import get_ledger_service
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.schemas.ledger import CreateBook, CreateEntry, EntryResponse, LedgerResponse


def entry_input():
    return dict(
        id=str(uuid4()),
        bookId=str(uuid4()),
        type="expense",
        categoryId="food",
        amount=800,
        date="2026-10-24",
        account="微信",
        note="早餐",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": 0},
        {"amount": -1},
        {"amount": 8.1},
        {"amount": True},
        {"amount": "800"},
        {"amount": 1000000000},
        {"categoryId": "salary"},
        {"date": "2026-02-30"},
        {"date": "1999-12-31"},
        {"date": "2101-01-01"},
        {"time": "24:00"},
        {"time": "8:00"},
        {"account": " "},
        {"note": "长" * 81},
        {"createdBy": str(uuid4())},
        {"ownerId": str(uuid4())},
    ],
)
def test_invalid_or_forged_entry_input(changes):
    with pytest.raises(ValidationError):
        CreateEntry.model_validate({**entry_input(), **changes})


def test_book_names_are_trimmed_and_cannot_be_blank():
    assert CreateBook(id=uuid4(), name="  旅行  ").name == "旅行"
    with pytest.raises(ValidationError):
        CreateBook(id=uuid4(), name=" ")


def test_ledger_api_uses_camel_case_and_does_not_echo_invalid_input():
    app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))
    body = CreateEntry.model_validate(entry_input())
    saved = EntryResponse(
        **body.model_dump(),
        created_at=int(datetime.now(UTC).timestamp() * 1000),
        created_by=uuid4(),
        version=1,
    )
    service = SimpleNamespace(
        create_entry=AsyncMock(return_value=saved),
        snapshot=AsyncMock(return_value=LedgerResponse(books=[], entries=[])),
    )
    app.dependency_overrides[get_ledger_service] = lambda: service
    with TestClient(app) as client:
        result = client.post("/api/v1/entries", json=body.model_dump(mode="json", by_alias=True))
        assert result.status_code == 200
        assert result.json()["bookId"] == str(body.book_id)
        assert "createdAt" in result.json() and "time" not in result.json()
        invalid = client.post(
            "/api/v1/entries", json={**entry_input(), "note": "secret-note" * 100}
        )
        assert invalid.status_code == 422
        assert "secret-note" not in invalid.text
        service.create_entry.side_effect = ServiceError(409, "账单已更新，请刷新")
        assert client.post("/api/v1/entries", json=entry_input()).status_code == 409
