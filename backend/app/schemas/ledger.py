"""与小程序一致的账本契约；不接受客户端指定用户身份。"""

from datetime import date as Date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

EXPENSE_CATEGORIES = {
    "food",
    "coffee",
    "transport",
    "shopping",
    "daily",
    "house",
    "play",
    "health",
    "study",
    "gift",
    "travel",
    "other",
}
INCOME_CATEGORIES = {"salary", "bonus", "parttime", "refund", "other-income"}
MAX_CENTS = 999999999


class LedgerSchema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class CreateBook(LedgerSchema):
    id: UUID
    name: str = Field(min_length=1, max_length=24)
    kind: Literal["personal", "shared"] = "personal"
    member_name: str = Field(default="创建者", min_length=1, max_length=12)

    @field_validator("name", "member_name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class UpdateBudget(LedgerSchema):
    budget: int = Field(strict=True, ge=0, le=MAX_CENTS)
    version: int = Field(strict=True, ge=1)


class MemberResponse(LedgerSchema):
    user_id: UUID
    name: str
    role: Literal["owner", "member"]
    status: Literal["active", "removed", "left"]
    color: str = "#dcece1"


class BookResponse(LedgerSchema):
    id: UUID
    name: str
    description: str
    icon: str
    color: str
    budget: int
    version: int
    kind: Literal["personal", "shared"] = "personal"
    owner_id: UUID | None = None
    current_user_id: UUID | None = None
    members: list[MemberResponse] = Field(default_factory=list)


class EntryInput(LedgerSchema):
    book_id: UUID
    type: Literal["expense", "income"]
    category_id: str
    amount: int = Field(strict=True, gt=0, le=MAX_CENTS)
    date: Date = Field(ge=Date(2000, 1, 1), le=Date(2100, 12, 31))
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    note: str = Field(default="", max_length=80)
    account: str = Field(min_length=1, max_length=24)

    @field_validator("note", "account", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_category(self):
        allowed = EXPENSE_CATEGORIES if self.type == "expense" else INCOME_CATEGORIES
        if self.category_id not in allowed:
            raise ValueError("分类与收支类型不匹配")
        return self


class CreateEntry(EntryInput):
    id: UUID


class UpdateEntry(EntryInput):
    version: int = Field(strict=True, ge=1)


class EntryResponse(EntryInput):
    id: UUID
    created_at: int
    created_by: UUID
    version: int
    updated_by: UUID | None = None
    updated_at: int | None = None


class LedgerResponse(LedgerSchema):
    books: list[BookResponse]
    entries: list[EntryResponse]
