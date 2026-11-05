"""聊天 V2 契约；本模块不执行查询，也不向旧接口注册新路由。"""

import re
from datetime import date as Date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.chat import SendMessage
from app.schemas.ledger import MAX_CENTS, LedgerSchema

ToolName = Literal["summarize_entries", "search_entries", "compare_periods", "get_entry"]
GroupBy = Literal["category", "date", "account", "creator"]
FactId = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,95}$")]


class DateRange(LedgerSchema):
    start: Date = Field(ge=Date(2000, 1, 1), le=Date(2100, 12, 31))
    end: Date = Field(ge=Date(2000, 1, 1), le=Date(2100, 12, 31))

    @field_validator("start", "end", mode="before")
    @classmethod
    def iso_date(cls, value):
        if type(value) is Date or (
            isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        ):
            return value
        raise ValueError("日期必须使用 YYYY-MM-DD 格式，不接受时间戳或时刻")

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class DateSelection(LedgerSchema):
    """日期辅助解析输入；业务工具仍使用解析后的显式 DateRange。"""

    kind: Literal[
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "last_n_days",
        "this_month",
        "last_month",
        "this_year",
        "last_year",
        "month",
        "range",
    ]
    days: int | None = Field(default=None, strict=True, ge=1, le=366)
    month: str | None = Field(default=None, pattern=r"^(20\d{2}|2100)-(0[1-9]|1[0-2])$")
    date_range: DateRange | None = None

    @model_validator(mode="after")
    def arguments_match_kind(self):
        for kind, field in (("last_n_days", "days"), ("month", "month"), ("range", "date_range")):
            if (self.kind == kind) != (getattr(self, field) is not None):
                raise ValueError("日期选项与补充参数不一致")
        return self


class ComparisonSelection(LedgerSchema):
    kind: Literal["previous_period", "previous_month_same_period", "previous_month_full", "range"]
    date_range: DateRange | None = None

    @model_validator(mode="after")
    def arguments_match_kind(self):
        if (self.kind == "range") != (self.date_range is not None):
            raise ValueError("自定义比较必须提供日期，其他比较不能附带自定义日期")
        return self


Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class KeywordFilter(LedgerSchema):
    include: list[Keyword] = Field(default_factory=list, max_length=20)
    mode: Literal["any", "all"] = "any"
    exclude: list[Keyword] = Field(default_factory=list, max_length=20)


class EntryFilter(LedgerSchema):
    date_range: DateRange
    entry_type: Literal["expense", "income", "both"] = "expense"
    category_ids: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24)]
    ] = Field(default_factory=list, max_length=32)
    keywords: KeywordFilter = Field(default_factory=KeywordFilter)
    accounts: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24)]
    ] = Field(default_factory=list, max_length=20)
    creator_ids: list[UUID] = Field(default_factory=list, max_length=50)
    amount_min: Annotated[int, Field(strict=True, ge=0, le=MAX_CENTS)] | None = None
    amount_max: Annotated[int, Field(strict=True, ge=0, le=MAX_CENTS)] | None = None
    min_inclusive: bool = Field(default=True, strict=True)
    max_inclusive: bool = Field(default=True, strict=True)
    weekdays: list[Annotated[int, Field(strict=True, ge=1, le=7)]] = Field(
        default_factory=list, max_length=7
    )
    excluded_entry_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def amount_order(self):
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise ValueError("金额下限不能超过上限")
        return self


class SummaryArgs(LedgerSchema):
    filters: EntryFilter
    group_by: GroupBy | None = None


class SearchArgs(LedgerSchema):
    filters: EntryFilter
    sort: Literal["date_desc", "amount_desc"] = "date_desc"
    cursor: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    limit: int = Field(default=20, strict=True, ge=1, le=20)


class CompareArgs(LedgerSchema):
    filters: EntryFilter
    previous_range: DateRange
    group_by: GroupBy = "category"


class EntryArgs(LedgerSchema):
    entry_id: UUID


class SummaryCall(LedgerSchema):
    name: Literal["summarize_entries"]
    arguments: SummaryArgs


class SearchCall(LedgerSchema):
    name: Literal["search_entries"]
    arguments: SearchArgs


class CompareCall(LedgerSchema):
    name: Literal["compare_periods"]
    arguments: CompareArgs


class EntryCall(LedgerSchema):
    name: Literal["get_entry"]
    arguments: EntryArgs


ToolCall = Annotated[
    SummaryCall | SearchCall | CompareCall | EntryCall, Field(discriminator="name")
]


class ToolDecision(LedgerSchema):
    """结构化循环与原生调用归一化后的单步结果。"""

    action: Literal["tool", "answer", "clarify"]
    call: ToolCall | None = None
    text: Annotated[str, Field(max_length=2500)] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.action == "tool":
            if self.call is None or self.text is not None:
                raise ValueError("工具步骤只能包含一次工具调用")
        elif self.call is not None or not self.text or not self.text.strip():
            raise ValueError("回答或澄清步骤必须包含正文且不能调用工具")
        return self


class FactRef(LedgerSchema):
    id: FactId
    value: StrictInt | StrictStr
    unit: Literal["cents", "count", "basis_points", "date", "text"]
    label: str = Field(min_length=1, max_length=120)
    source_call_id: str = Field(min_length=1, max_length=96)
    metric: (
        Literal[
            "total",
            "count",
            "previous_total",
            "previous_count",
            "difference",
            "change_rate",
            "share",
            "amount",
        ]
        | None
    ) = None
    date_range: DateRange | None = None
    previous_range: DateRange | None = None
    generated_at: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def valid_unit(self):
        numeric = self.unit in {"cents", "count", "basis_points"}
        if numeric != isinstance(self.value, int):
            raise ValueError("事实值与单位不匹配")
        if self.unit == "count" and self.value < 0:
            raise ValueError("笔数不能为负数")
        if self.unit == "date":
            Date.fromisoformat(self.value)
        return self


class ResultRow(LedgerSchema):
    label: str = Field(min_length=1, max_length=120)
    fact_ids: list[FactId] = Field(default_factory=list)
    entry_id: UUID | None = None
    date: Date | None = None
    detail: str | None = Field(default=None, max_length=300)
    entry_type: Literal["expense", "income"] | None = None
    group_key: str | None = Field(default=None, max_length=120)
    category_id: str | None = None
    account: str | None = Field(default=None, max_length=24)
    creator_id: UUID | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    matched_keywords: list[str] = Field(default_factory=list, max_length=20)
    is_other: bool = False


class ResultCard(LedgerSchema):
    id: str = Field(min_length=1, max_length=96)
    type: Literal["summary", "entries", "trend", "comparison"]
    title: str = Field(min_length=1, max_length=120)
    filters: EntryFilter
    previous_range: DateRange | None = None
    generated_at: int = Field(strict=True, ge=0)
    fact_ids: list[FactId] = Field(default_factory=list)
    rows: list[ResultRow] = Field(default_factory=list)
    footnote: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def complete_rows(self):
        if self.type == "comparison" and self.previous_range is None:
            raise ValueError("比较卡必须明确上期日期")
        if self.type == "trend" and any(row.date is None for row in self.rows):
            raise ValueError("趋势行必须包含实际日期")
        if self.type == "entries":
            for row in self.rows:
                if any(
                    getattr(row, field) is None
                    for field in (
                        "entry_id",
                        "date",
                        "entry_type",
                        "category_id",
                        "account",
                        "creator_id",
                    )
                ):
                    raise ValueError("账单行缺少真实账单标识或必要字段")
        return self


class ToolError(LedgerSchema):
    code: Literal["invalid_arguments", "not_found", "permission_denied", "unavailable", "limit"]
    detail: str = Field(min_length=1, max_length=300)


class ToolResult(LedgerSchema):
    call_id: str = Field(min_length=1, max_length=96)
    name: ToolName
    status: Literal["complete", "partial", "error"]
    cards: list[ResultCard] = Field(default_factory=list)
    fact_refs: list[FactRef] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=2048)
    error: ToolError | None = None
    notices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def error_state(self):
        if (self.status == "error") != (self.error is not None):
            raise ValueError("工具失败状态必须与错误信息一致")
        if self.status == "error" and (self.cards or self.fact_refs or self.next_cursor):
            raise ValueError("失败工具不能同时提供统计结果")
        validate_fact_references(self.cards, self.fact_refs)
        if any(fact.source_call_id != self.call_id for fact in self.fact_refs):
            raise ValueError("工具事实必须属于当前调用")
        return self


def validate_fact_references(cards: list[ResultCard], facts: list[FactRef]):
    fact_ids = [fact.id for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("事实 ID 不能重复")
    card_ids = [card.id for card in cards]
    if len(set(card_ids)) != len(card_ids):
        raise ValueError("卡片 ID 不能重复")
    for card in cards:
        referenced = card.fact_ids + [ref for row in card.rows for ref in row.fact_ids]
        if not set(referenced).issubset(fact_ids):
            raise ValueError("结果卡引用了不存在的事实")


class FieldSource(LedgerSchema):
    field: str = Field(min_length=1, max_length=96)
    source: Literal["explicit", "inherited", "default"]
    restored_from: UUID | None = None


class ResultReference(LedgerSchema):
    turn_id: UUID
    card_id: str = Field(min_length=1, max_length=96)
    entry_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ChatContextV2(LedgerSchema):
    schema_version: Literal[2] = 2
    filters: EntryFilter
    previous_range: DateRange | None = None
    sources: list[FieldSource] = Field(default_factory=list)
    result_refs: list[ResultReference] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=2000)
    summary_through_sequence: int = Field(default=0, strict=True, ge=0)


ContextField = Literal[
    "dateRange",
    "entryType",
    "categoryIds",
    "keywords.include",
    "keywords.exclude",
    "keywords.mode",
    "accounts",
    "creatorIds",
    "amountMin",
    "amountMax",
    "minInclusive",
    "maxInclusive",
    "weekdays",
    "excludedEntryIds",
    "previousRange",
]


class ContextEdit(LedgerSchema):
    field: ContextField
    op: Literal["add", "replace", "clear", "restore"]
    value: DateRange | list[StrictStr | StrictInt] | StrictStr | StrictInt | StrictBool | None = (
        None
    )
    source_turn_id: UUID | None = None

    @model_validator(mode="after")
    def valid_operation(self):
        collections = {
            "categoryIds",
            "keywords.include",
            "keywords.exclude",
            "accounts",
            "creatorIds",
            "weekdays",
            "excludedEntryIds",
        }
        if self.op == "restore":
            if self.source_turn_id is None or self.value is not None:
                raise ValueError("恢复条件必须引用已保存轮次，不能同时提供新值")
        elif self.source_turn_id is not None:
            raise ValueError("只有恢复操作可以指定来源轮次")
        if self.op in {"clear", "restore"}:
            if self.value is not None:
                raise ValueError("清空或恢复操作不能附带新值")
        elif self.value is None:
            raise ValueError("追加或替换必须提供值，清空请使用 clear")
        if self.op == "add" and (self.field not in collections or not isinstance(self.value, list)):
            raise ValueError("只有集合筛选可以追加列表")
        return self


class ContextPatch(LedgerSchema):
    reset: bool = Field(default=False, strict=True)
    edits: list[ContextEdit] = Field(default_factory=list, max_length=20)


class ReferenceRequest(LedgerSchema):
    kind: Literal["entry", "entries"] = "entry"
    turn_id: UUID | None = None
    card_id: str | None = Field(default=None, min_length=1, max_length=96)
    entry_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def explicit_source(self):
        if (self.card_id or self.entry_ids) and self.turn_id is None:
            raise ValueError("指定卡片或账单时必须提供来源轮次")
        return self


class ReferenceCandidate(LedgerSchema):
    reference: ResultReference
    entry_id: UUID
    label: str = Field(min_length=1, max_length=120)
    date: Date
    entry_type: Literal["expense", "income"]


class ReferenceResolution(LedgerSchema):
    status: Literal["resolved", "clarify"]
    candidates: list[ReferenceCandidate] = Field(default_factory=list, max_length=20)
    detail: str


class ContextPlanV2(LedgerSchema):
    """模型只提出编辑与引用请求，实际来源和账单权限由服务端核对。"""

    patch: ContextPatch
    reference: ReferenceRequest | None = None
    reference_action: Literal["inspect", "exclude"] | None = None

    @model_validator(mode="after")
    def paired_reference(self):
        if (self.reference is None) != (self.reference_action is None):
            raise ValueError("引用请求必须说明查看或排除用途")
        return self


class FinishDecisionV2(LedgerSchema):
    action: Literal["answer", "clarify"]
    text: str = Field(min_length=1, max_length=2500)


class Suggestion(LedgerSchema):
    id: UUID
    text: str = Field(min_length=1, max_length=200)
    source_turn_id: UUID
    reference: ResultReference | None = None


class SendMessageV2(SendMessage):
    """只接收建议 ID，客户端不能注入工具调用、身份或事实。"""

    suggestion_id: UUID | None = None


class CreateConversationV2(LedgerSchema):
    id: UUID
    book_id: UUID
    initial_month: str = Field(pattern=r"^(20\d{2}|2100)-(0[1-9]|1[0-2])$")


class ConversationSummaryV2(CreateConversationV2):
    schema_version: Literal[2] = 2
    title: str = Field(max_length=40)
    updated_at: int = Field(strict=True, ge=0)


class AnswerPreviewV2(LedgerSchema):
    schema_version: Literal[2] = 2
    answer: str = Field(max_length=2500)
    completeness: Literal["complete", "partial"]
    missing: list[str] = Field(default_factory=list)
    cards: list[ResultCard] = Field(default_factory=list)
    fact_refs: list[FactRef] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def valid_completeness(self):
        if (self.completeness == "partial") != bool(self.missing):
            raise ValueError("部分结果必须说明缺失内容，完整结果不能声明缺失")
        validate_fact_references(self.cards, self.fact_refs)
        return self


class TurnResponseV2(AnswerPreviewV2):
    id: UUID
    sequence: int = Field(strict=True, ge=1)
    text: str = Field(min_length=1, max_length=200)
    context: ChatContextV2
    created_at: int = Field(strict=True, ge=0)


class ConversationResponseV2(ConversationSummaryV2):
    context: ChatContextV2
    turns: list[TurnResponseV2]
    has_more: bool


class CapabilitiesV2(LedgerSchema):
    schema_versions: list[Literal[2]] = Field(default_factory=lambda: [2])
    enabled: bool


class LegacyConversationResponseV2(ConversationResponseV2):
    readonly: Literal[True] = True
    source_schema_version: Literal[1] = 1


class StatusData(LedgerSchema):
    text: str = Field(min_length=1, max_length=200)


class ErrorData(LedgerSchema):
    status: int = Field(strict=True, ge=400, le=599)
    detail: str = Field(min_length=1, max_length=300)


class StatusEvent(LedgerSchema):
    event: Literal["status"]
    data: StatusData


class SnapshotEvent(LedgerSchema):
    event: Literal["snapshot"]
    data: AnswerPreviewV2


class DeltaData(LedgerSchema):
    text: str = Field(min_length=1, max_length=2500)


class DeltaEvent(LedgerSchema):
    event: Literal["delta"]
    data: DeltaData


class DoneEvent(LedgerSchema):
    event: Literal["done"]
    data: TurnResponseV2


class ErrorEvent(LedgerSchema):
    event: Literal["error"]
    data: ErrorData


class PingEvent(LedgerSchema):
    event: Literal["ping"]
    data: LedgerSchema


ChatStreamEventV2 = Annotated[
    StatusEvent | SnapshotEvent | DeltaEvent | DoneEvent | ErrorEvent | PingEvent,
    Field(discriminator="event"),
]
