from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ledger import LedgerSchema


class CreateConversation(LedgerSchema):
    id: UUID
    book_id: UUID
    month: str = Field(pattern=r"^(20\d{2}|2100)-(0[1-9]|1[0-2])$")


class SendMessage(LedgerSchema):
    id: UUID
    text: str = Field(min_length=1, max_length=200)

    @field_validator("text", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "overview", "details", "compare", "large", "advice", "clarify", "record", "unsupported"
    ]
    month: str | None
    category_mode: Literal["all", "selected", "inherit"]
    category_id: str | None
    entry_type: Literal["expense", "income"] | None
    threshold: int | None = Field(strict=True)
    comparison: Literal["same_period", "full_month"] | None
    clarification: str | None = Field(max_length=300)


class QueryContext(LedgerSchema):
    month: str
    category_id: str | None = None
    entry_type: Literal["expense", "income"] = "expense"


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=800)


class AnalysisRow(LedgerSchema):
    label: str
    amount: int
    percent: float | None = None
    icon: str
    color: str
    detail: str | None = None


class AnalysisCard(LedgerSchema):
    title: str
    text: str
    total: int
    count: int
    entry_type: Literal["expense", "income"]
    scope: str
    rows: list[AnalysisRow]
    footnote: str


class TurnResponse(LedgerSchema):
    id: UUID
    sequence: int
    text: str
    answer: str
    result: AnalysisCard | None
    context: QueryContext
    created_at: int


class ConversationSummary(LedgerSchema):
    id: UUID
    book_id: UUID
    month: str
    title: str
    updated_at: int


class ConversationResponse(ConversationSummary):
    turns: list[TurnResponse]
    has_more: bool
