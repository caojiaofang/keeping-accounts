"""家庭预算第一版契约；金额使用整数分，计划与实际执行分离。"""

import re
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, field_validator, model_validator

from app.schemas.ledger import MAX_CENTS, EntryResponse, LedgerSchema

MIN_BUDGET_DATE = date(2000, 1, 1)
MAX_BUDGET_DATE = date(2100, 12, 31)
MAX_SAFE_INTEGER = 2**53 - 1
MAX_BUDGET_GROUPS = 20
MAX_BUDGET_ITEMS = 200
MAX_EQUALIZATION_MONTHS = 120
MONTH_PATTERN = r"^(20[0-9]{2}|2100)-(0[1-9]|1[0-2])$"

BudgetMonth = Annotated[str, Field(strict=True, pattern=MONTH_PATTERN)]
BudgetMoney = Annotated[int, Field(strict=True, ge=0, le=MAX_CENTS)]
BudgetPositiveMoney = Annotated[int, Field(strict=True, gt=0, le=MAX_CENTS)]
BudgetInteger = Annotated[int, Field(strict=True, ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
BudgetUnsignedInteger = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)]
BudgetVersion = BudgetUnsignedInteger
BudgetItemKind = Literal["expense", "reserve", "saving"]


def _date_input(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return value
    raise ValueError("预算日期须为 YYYY-MM-DD，不接受时间戳或带时间的值")


BudgetDate = Annotated[
    date,
    BeforeValidator(_date_input),
    Field(ge=MIN_BUDGET_DATE, le=MAX_BUDGET_DATE),
]


class BudgetGroup(LedgerSchema):
    id: UUID
    name: str = Field(min_length=1, max_length=24)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReserveCycle(LedgerSchema):
    expected_amount: BudgetPositiveMoney
    anchor_date: BudgetDate
    cycle_months: Literal[1, 6, 12]

    @field_validator("cycle_months", mode="before")
    @classmethod
    def strict_cycle(cls, value):
        if type(value) is not int:
            raise ValueError("缴费周期须为整数 1、6 或 12 个月")
        return value


class BudgetItem(BudgetGroup):
    group_id: UUID
    kind: BudgetItemKind
    monthly_amount: BudgetMoney
    due_day: Annotated[int, Field(strict=True, ge=1, le=31)] | None = None
    reserve: ReserveCycle | None = None
    note: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def valid_terms(self):
        if (self.kind == "reserve") != (self.reserve is not None):
            raise ValueError("周期预留必须填写缴费规则，其他项目不能填写该规则")
        if self.kind != "expense" and self.due_day is not None:
            raise ValueError("只有当月支出项目可设置月缴费日")
        return self


class BudgetPlan(LedgerSchema):
    planned_income: BudgetMoney
    groups: list[BudgetGroup] = Field(default_factory=list, max_length=MAX_BUDGET_GROUPS)
    items: list[BudgetItem] = Field(default_factory=list, max_length=MAX_BUDGET_ITEMS)

    @model_validator(mode="after")
    def valid_structure(self):
        group_ids = {group.id for group in self.groups}
        if len(group_ids) != len(self.groups):
            raise ValueError("预算分组 ID 不能重复")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("预算项目 ID 不能重复")
        if any(item.group_id not in group_ids for item in self.items):
            raise ValueError("预算项目必须属于本方案的分组")
        return self


class BudgetRevision(LedgerSchema):
    id: UUID
    effective_month: BudgetMonth
    plan: BudgetPlan


class PreviewBudgetRevision(LedgerSchema):
    version: BudgetVersion
    effective_month: BudgetMonth
    plan: BudgetPlan


class SaveBudgetRevision(PreviewBudgetRevision):
    request_id: UUID
    impact_token: str = Field(min_length=1, max_length=512)


class BudgetItemAmount(LedgerSchema):
    item_id: UUID
    amount: BudgetMoney


class BudgetMonthOverride(LedgerSchema):
    planned_income: BudgetMoney | None = None
    item_amounts: list[BudgetItemAmount] = Field(default_factory=list, max_length=MAX_BUDGET_ITEMS)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.item_id for item in self.item_amounts}) != len(self.item_amounts):
            raise ValueError("单月覆盖的预算项目不能重复")
        return self


class ConfirmBudgetMonth(LedgerSchema):
    request_id: UUID
    source_revision_id: UUID
    plan_version: BudgetVersion


class UpdateBudgetMonth(LedgerSchema):
    request_id: UUID
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
    override: BudgetMonthOverride


class BudgetGroupTotal(LedgerSchema):
    group_id: UUID
    amount: BudgetUnsignedInteger


class BudgetTotals(LedgerSchema):
    planned_income: BudgetUnsignedInteger
    expense_amount: BudgetUnsignedInteger
    reserve_amount: BudgetUnsignedInteger
    saving_amount: BudgetUnsignedInteger
    total_allocated: BudgetUnsignedInteger
    unallocated: BudgetInteger
    group_totals: list[BudgetGroupTotal]


class BudgetPlanResponse(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    version: BudgetVersion
    revisions: list[BudgetRevision]


class BudgetImpactResponse(LedgerSchema):
    impact_token: str
    expires_at: BudgetUnsignedInteger
    version: BudgetVersion
    effective_month: BudgetMonth
    until_month: BudgetMonth | None
    confirmed_months: list[BudgetMonth]
    overridden_months: list[BudgetMonth]
    totals: BudgetTotals


class SavedBudgetRevision(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
    revision: BudgetRevision


class BudgetMonthResponse(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    month: BudgetMonth
    state: Literal["not_set", "preview", "confirmed"]
    plan_version: BudgetVersion
    snapshot_id: UUID | None
    version: BudgetVersion
    source_revision_id: UUID | None
    plan: BudgetPlan | None
    override: BudgetMonthOverride
    totals: BudgetTotals | None
    generated_at: BudgetUnsignedInteger

    @model_validator(mode="after")
    def valid_state(self):
        if self.state == "not_set":
            if any(
                value is not None for value in (self.plan, self.totals, self.source_revision_id)
            ):
                raise ValueError("未设置预算时不能返回虚构的方案或汇总")
            if self.override.planned_income is not None or self.override.item_amounts:
                raise ValueError("未设置预算时不能有月份覆盖")
        elif (
            self.plan is None
            or self.totals is None
            or self.source_revision_id is None
            or self.plan_version < 1
        ):
            raise ValueError("预算预览和快照必须包含方案、来源和汇总")
        if self.state == "confirmed":
            if self.snapshot_id is None or self.version < 1:
                raise ValueError("已确认预算必须有快照 ID 和正整数版本")
        elif self.snapshot_id is not None or self.version != 0:
            raise ValueError("未确认的月份不能带快照 ID 或快照版本")
        if self.state != "confirmed" and (
            self.override.planned_income is not None or self.override.item_amounts
        ):
            raise ValueError("只有已确认的月份可保存单月覆盖")
        return self


class BudgetMonthAmount(LedgerSchema):
    month: BudgetMonth
    amount: BudgetUnsignedInteger


class PreviewBudgetEqualization(LedgerSchema):
    version: BudgetVersion
    item_id: UUID
    total_amount: BudgetMoney
    start_month: BudgetMonth
    end_month: BudgetMonth

    @model_validator(mode="after")
    def valid_window(self):
        first_year, first_month = map(int, self.start_month.split("-"))
        last_year, last_month = map(int, self.end_month.split("-"))
        count = (last_year - first_year) * 12 + last_month - first_month + 1
        if not 1 <= count <= MAX_EQUALIZATION_MONTHS:
            raise ValueError(f"均摊区间须按先后顺序设置，单次最多 {MAX_EQUALIZATION_MONTHS} 个月")
        return self


class ApplyBudgetEqualization(PreviewBudgetEqualization):
    request_id: UUID
    impact_token: str = Field(min_length=1, max_length=512)


class BudgetEqualizationPreview(LedgerSchema):
    impact_token: str
    expires_at: BudgetUnsignedInteger
    schedule: list[BudgetMonthAmount]
    affected_months: list[BudgetMonth]


class BudgetEqualizationResponse(LedgerSchema):
    schema_version: Literal[1] = 1
    months: list[BudgetMonthResponse]


class BudgetTarget(LedgerSchema):
    start_date: BudgetDate
    target_date: BudgetDate
    target_amount: BudgetPositiveMoney
    available_amount: BudgetInteger = 0
    used_amount: BudgetUnsignedInteger = 0

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.start_date > self.target_date:
            raise ValueError("目标开始日期不能晚于目标日期")
        return self


class BudgetFundingPreview(LedgerSchema):
    funded_amount: BudgetInteger
    gap_amount: BudgetUnsignedInteger
    surplus_amount: BudgetUnsignedInteger
    remaining_months: Annotated[int, Field(strict=True, ge=0)]
    overdue: bool
    additional_schedule: list[BudgetMonthAmount]


class BudgetFundMutation(LedgerSchema):
    request_id: UUID
    fund_version: BudgetVersion


class CreateBudgetFund(BudgetFundMutation):
    id: UUID
    kind: Literal["opening", "deposit"]
    amount: BudgetPositiveMoney
    date: BudgetDate
    note: str = Field(default="", max_length=80)


class BudgetFundReplacement(LedgerSchema):
    id: UUID
    amount: BudgetPositiveMoney
    date: BudgetDate
    note: str = Field(default="", max_length=80)


class CorrectBudgetFund(BudgetFundMutation):
    record_version: Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
    replacement: BudgetFundReplacement | None


class BudgetGoalTerms(LedgerSchema):
    start_date: BudgetDate
    target_date: BudgetDate
    target_amount: BudgetPositiveMoney
    carry_in_amount: BudgetMoney = 0

    @model_validator(mode="after")
    def ordered_goal_dates(self):
        if self.start_date > self.target_date:
            raise ValueError("目标开始日期不能晚于目标日期")
        return self


class CreateBudgetGoal(BudgetFundMutation, BudgetGoalTerms):
    id: UUID


class UpdateBudgetGoal(BudgetFundMutation, BudgetGoalTerms):
    version: Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
    state: Literal["active", "closed"]


class BudgetFundSummary(LedgerSchema):
    as_of_date: BudgetDate
    opening_amount: BudgetUnsignedInteger
    deposited_amount: BudgetUnsignedInteger
    used_amount: BudgetUnsignedInteger
    available_amount: BudgetInteger


class BudgetFundRecordResponse(LedgerSchema):
    id: UUID
    item_id: UUID
    kind: Literal["opening", "deposit", "use"]
    amount: BudgetPositiveMoney
    date: BudgetDate
    note: str
    entry_id: UUID | None
    goal_period_id: UUID | None
    replaces_id: UUID | None
    version: BudgetVersion
    voided_at: BudgetUnsignedInteger | None
    created_at: BudgetUnsignedInteger
    updated_at: BudgetUnsignedInteger
    created_by: UUID
    updated_by: UUID


class BudgetGoalResponse(BudgetGoalTerms):
    id: UUID
    item_id: UUID
    state: Literal["active", "closed"]
    version: BudgetVersion
    closed_at: BudgetUnsignedInteger | None
    collected_amount: BudgetUnsignedInteger
    used_amount: BudgetUnsignedInteger
    available_amount: BudgetInteger
    forecast: BudgetFundingPreview


class BudgetProjectResponse(LedgerSchema):
    id: UUID
    kind: BudgetItemKind
    status: Literal["active", "archived"]
    enabled_month: BudgetMonth
    fund_version: BudgetVersion
    configuration: BudgetItem


class BudgetProjectPage(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    items: list[BudgetProjectResponse]
    next_cursor: UUID | None


class BudgetItemDetail(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    generated_at: BudgetUnsignedInteger
    item: BudgetProjectResponse
    balance: BudgetFundSummary
    goals: list[BudgetGoalResponse]
    records: list[BudgetFundRecordResponse]
    next_record_cursor: UUID | None


class SavedBudgetFund(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    item_id: UUID
    fund_version: BudgetVersion
    record: BudgetFundRecordResponse
    replacement: BudgetFundRecordResponse | None = None
    balance: BudgetFundSummary


class SavedBudgetGoal(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    item_id: UUID
    fund_version: BudgetVersion
    goal: BudgetGoalResponse
    balance: BudgetFundSummary


class BudgetLinkMutation(LedgerSchema):
    request_id: UUID
    entry_version: Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
    link_version: BudgetVersion


class SetBudgetEntryLink(BudgetLinkMutation):
    item_id: UUID
    goal_period_id: UUID | None = None


class RemoveBudgetEntryLink(BudgetLinkMutation):
    pass


class BudgetEntryLinkResponse(LedgerSchema):
    entry_id: UUID
    book_id: UUID
    item_id: UUID
    goal_period_id: UUID | None
    version: BudgetVersion
    is_deleted: bool
    updated_at: BudgetUnsignedInteger
    updated_by: UUID


class BudgetProjectVersion(LedgerSchema):
    item_id: UUID
    fund_version: BudgetVersion


class BudgetEntryLinkState(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    entry_id: UUID
    entry_version: BudgetVersion
    link_version: BudgetVersion
    link: BudgetEntryLinkResponse | None
    fund_versions: list[BudgetProjectVersion] = Field(default_factory=list)
    notice: str | None = None


class BudgetLinkedEntry(LedgerSchema):
    entry: EntryResponse
    link: BudgetEntryLinkResponse | None
    link_version: BudgetVersion


class BudgetEntryPage(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    entries: list[BudgetLinkedEntry]
    next_cursor: UUID | None


class BudgetExecutionDetail(BudgetItemDetail):
    entries: list[BudgetLinkedEntry]
    next_entry_cursor: UUID | None


class BudgetItemExecution(LedgerSchema):
    item_id: UUID
    kind: BudgetItemKind
    name: str
    group_id: UUID
    in_plan: bool
    planned_amount: BudgetMoney
    expense_amount: BudgetUnsignedInteger
    remaining_amount: BudgetInteger | None
    deposited_amount: BudgetUnsignedInteger
    used_amount: BudgetUnsignedInteger
    available_amount: BudgetInteger | None
    fund_version: BudgetVersion


class BudgetMonthExecution(LedgerSchema):
    schema_version: Literal[1] = 1
    book_id: UUID
    month: BudgetMonth
    generated_at: BudgetUnsignedInteger
    budget: BudgetMonthResponse
    expense_total: BudgetUnsignedInteger
    linked_expense_total: BudgetUnsignedInteger
    unlinked_expense_total: BudgetUnsignedInteger
    items: list[BudgetItemExecution]
