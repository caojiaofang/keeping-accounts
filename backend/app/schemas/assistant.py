from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class RecordHistory(LedgerSchema):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=500)


class RecordDraft(EntryInput):
    key: UUID
    account_defaulted: bool = False


class RecordRequest(LedgerSchema):
    text: str = Field(default="", max_length=200)
    book_id: UUID
    history: list[RecordHistory] = Field(default_factory=list, max_length=6)
    drafts: list[RecordDraft] = Field(default_factory=list, max_length=10)
    # 单张原图最多 4 MiB；只接受内联图片，禁止客户端让服务端访问任意 URL。
    image: str | None = Field(default=None, max_length=5_592_432, repr=False)

    @field_validator("text", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_context(self):
        if not self.text and not self.image:
            raise ValueError("请输入文字或选择图片")
        if any(draft.book_id != self.book_id for draft in self.drafts):
            raise ValueError("草稿必须属于当前账本")
        if len({draft.key for draft in self.drafts}) != len(self.drafts):
            raise ValueError("草稿标识不能重复")
        return self


class ParsedRecordDraft(ParsedDraft):
    # 已有草稿保留 key，新增草稿由服务端分配，模型不能创建正式账单 ID。
    key: UUID | None


class ParsedRecordResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drafts: list[ParsedRecordDraft] = Field(max_length=10)
    clarification: str | None = Field(max_length=300)


class RecordResponse(ParseResponse):
    reply: str
