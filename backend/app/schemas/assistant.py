from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ledger import EntryInput, LedgerSchema


class ParseRequest(LedgerSchema):
    text: str = Field(min_length=1, max_length=200)
    book_id: UUID


class ParsedDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["expense", "income"]
    amount: int | None = Field(strict=True)
    category_id: str | None
    date: str | None
    time: str | None
    note: str = Field(max_length=80)
    account: str | None


class ParsedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drafts: list[ParsedDraft] = Field(max_length=10)
    clarification: str | None = Field(max_length=300)


class DraftResponse(EntryInput):
    key: UUID
    status: Literal["pending"] = "pending"
    account_defaulted: bool


class ParseResponse(LedgerSchema):
    drafts: list[DraftResponse]
    clarification: str | None
