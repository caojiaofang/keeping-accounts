"""家庭预算方案、月份、资金、目标、账单关联与实际执行接口。"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import get_access_token, get_auth_service
from app.api.deps import get_db
from app.core.errors import ServiceError
from app.schemas.budget import (
    ApplyBudgetEqualization,
    BudgetEntryLinkState,
    BudgetEntryPage,
    BudgetEqualizationPreview,
    BudgetEqualizationResponse,
    BudgetExecutionDetail,
    BudgetFundingPreview,
    BudgetImpactResponse,
    BudgetMonthExecution,
    BudgetMonthResponse,
    BudgetMonthSummary,
    BudgetPlanResponse,
    BudgetProjectPage,
    ConfirmBudgetMonth,
    CorrectBudgetFund,
    CreateBudgetFund,
    CreateBudgetGoal,
    DeleteMonthlyBudget,
    PreviewBudgetEqualization,
    PreviewBudgetRevision,
    RemoveBudgetEntryLink,
    SaveBudgetRevision,
    SavedBudgetFund,
    SavedBudgetGoal,
    SavedBudgetRevision,
    SaveMonthlyBudget,
    SetBudgetEntryLink,
    UpdateBudgetGoal,
    UpdateBudgetMonth,
)
from app.schemas.budget import BudgetMonth as BudgetMonthKey
from app.services.auth import AuthService
from app.services.budget import BudgetService
from app.services.budget_execution import BudgetExecutionService
from app.services.budget_funds import BudgetFundService
from app.services.budget_months import BudgetMonthService
from app.services.ledger import LedgerService

router = APIRouter(prefix="/books/{book_id}/budgets", tags=["家庭预算"])


async def get_budget_service(
    response: Response,
    token: Annotated[str, Depends(get_access_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[BudgetService]:
    response.headers["Cache-Control"] = "no-store"
    user = await auth.current_user(token)
    try:
        yield BudgetService(LedgerService(session, user.id))
    except SQLAlchemyError:
        await session.rollback()
        raise ServiceError(503, "预算服务暂时不可用，请稍后重试") from None
    except ServiceError:
        await session.rollback()
        raise


Budget = Annotated[BudgetService, Depends(get_budget_service)]


def get_budget_month_service(service: Budget) -> BudgetMonthService:
    return BudgetMonthService(service.ledger, clock=service.clock)


MonthBudget = Annotated[BudgetMonthService, Depends(get_budget_month_service)]


def get_budget_fund_service(service: Budget) -> BudgetFundService:
    return BudgetFundService(service.ledger, clock=service.clock)


FundBudget = Annotated[BudgetFundService, Depends(get_budget_fund_service)]


def get_budget_execution_service(service: Budget) -> BudgetExecutionService:
    return BudgetExecutionService(service.ledger, clock=service.clock)


ExecutionBudget = Annotated[BudgetExecutionService, Depends(get_budget_execution_service)]


@router.get(
    "/monthly/{month}/summary",
    response_model=BudgetMonthSummary,
    summary="轻量读取卡片预算总额、已用、剩余和超支",
)
async def monthly_summary(book_id: UUID, month: BudgetMonthKey, service: MonthBudget):
    return await service.month_summary(book_id, month)


@router.get(
    "/monthly/{month}",
    response_model=BudgetMonthResponse,
    summary="读取独立月预算，未设置月份不自动沿用",
)
async def read_monthly(book_id: UUID, month: BudgetMonthKey, service: MonthBudget):
    return await service.read_month(book_id, month, snapshot_only=True)


@router.put(
    "/monthly/{month}", response_model=BudgetMonthResponse, summary="成员共同新增或修改该月完整预算"
)
async def save_monthly(
    book_id: UUID, month: BudgetMonthKey, body: SaveMonthlyBudget, service: MonthBudget
):
    return await service.save_monthly(book_id, month, body)


@router.delete(
    "/monthly/{month}",
    response_model=BudgetMonthResponse,
    summary="有效成员删除该月预算，保留原账单",
)
async def delete_monthly(
    book_id: UUID, month: BudgetMonthKey, body: DeleteMonthlyBudget, service: MonthBudget
):
    return await service.delete_monthly(book_id, month, body)


@router.get(
    "/monthly/{month}/execution",
    response_model=BudgetMonthExecution,
    summary="独立月预算已用、剩余与超支",
)
async def monthly_execution(book_id: UUID, month: BudgetMonthKey, service: ExecutionBudget):
    return await service.month_execution(book_id, month, snapshot_only=True)


@router.get("/plan", response_model=BudgetPlanResponse, summary="读取当前账本预算方案及生效版本")
async def read_plan(book_id: UUID, service: Budget):
    return await service.read_plan(book_id)


@router.post(
    "/plan/preview", response_model=BudgetImpactResponse, summary="预览预算修改的月份和总分配"
)
async def preview_plan(book_id: UUID, body: PreviewBudgetRevision, service: Budget):
    return await service.preview_plan(book_id, body)


@router.put(
    "/plan", response_model=SavedBudgetRevision, summary="保存预算方案，校验预览和版本并防重复"
)
async def save_plan(book_id: UUID, body: SaveBudgetRevision, service: Budget):
    return await service.save_plan(book_id, body)


@router.get(
    "/months/{month}",
    response_model=BudgetMonthResponse,
    summary="读取月预算，区分未设置、预览和已确认",
)
async def read_month(book_id: UUID, month: BudgetMonthKey, service: MonthBudget):
    return await service.read_month(book_id, month)


@router.post(
    "/months/{month}/confirm",
    response_model=BudgetMonthResponse,
    summary="确认唯一月预算快照，可安全重试",
)
async def confirm_month(
    book_id: UUID, month: BudgetMonthKey, body: ConfirmBudgetMonth, service: MonthBudget
):
    return await service.confirm_month(book_id, month, body)


@router.put(
    "/months/{month}", response_model=BudgetMonthResponse, summary="完整替换该月覆盖，不修改原方案"
)
async def update_month(
    book_id: UUID, month: BudgetMonthKey, body: UpdateBudgetMonth, service: MonthBudget
):
    return await service.update_month(book_id, month, body)


@router.post(
    "/equalizations/preview",
    response_model=BudgetEqualizationPreview,
    summary="预览精确均摊月份与金额",
)
async def preview_equalization(
    book_id: UUID, body: PreviewBudgetEqualization, service: MonthBudget
):
    return await service.preview_equalization(book_id, body)


@router.post(
    "/equalizations",
    response_model=BudgetEqualizationResponse,
    summary="原子保存均摊月份，保留其他项目覆盖",
)
async def apply_equalization(book_id: UUID, body: ApplyBudgetEqualization, service: MonthBudget):
    return await service.apply_equalization(book_id, body)


@router.get("/items", response_model=BudgetProjectPage, summary="分页读取用途，包括归档项目")
async def list_projects(
    book_id: UUID,
    service: FundBudget,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await service.list_projects(book_id, after=after, limit=limit)


@router.get(
    "/items/{item_id}",
    response_model=BudgetExecutionDetail,
    summary="读取用途资金、目标、流水与关联账单",
)
async def item_detail(
    book_id: UUID,
    item_id: UUID,
    service: ExecutionBudget,
    before_record_id: Annotated[UUID | None, Query(alias="beforeRecordId")] = None,
    record_limit: Annotated[int, Query(alias="recordLimit", ge=1, le=100)] = 20,
    before_entry_id: Annotated[UUID | None, Query(alias="beforeEntryId")] = None,
    entry_limit: Annotated[int, Query(alias="entryLimit", ge=1, le=100)] = 20,
):
    return await service.execution_detail(
        book_id,
        item_id,
        before_record_id=before_record_id,
        record_limit=record_limit,
        before_entry_id=before_entry_id,
        entry_limit=entry_limit,
    )


@router.post(
    "/items/{item_id}/funds",
    response_model=SavedBudgetFund,
    summary="记录已预留或已存入，不生成收支账单",
)
async def create_fund(book_id: UUID, item_id: UUID, body: CreateBudgetFund, service: FundBudget):
    return await service.create_fund(book_id, item_id, body)


@router.post(
    "/items/{item_id}/funds/{record_id}/correct",
    response_model=SavedBudgetFund,
    summary="作废并替代资金记录，保留修改轨迹",
)
async def correct_fund(
    book_id: UUID, item_id: UUID, record_id: UUID, body: CorrectBudgetFund, service: FundBudget
):
    return await service.correct_fund(book_id, item_id, record_id, body)


@router.post(
    "/items/{item_id}/goals",
    response_model=SavedBudgetGoal,
    summary="建立或续建储蓄目标，结转不重复增加余额",
)
async def create_goal(book_id: UUID, item_id: UUID, body: CreateBudgetGoal, service: FundBudget):
    return await service.save_goal(book_id, item_id, body)


@router.put(
    "/items/{item_id}/goals/{goal_id}",
    response_model=SavedBudgetGoal,
    summary="修改或关闭目标，检查日期、结转和执行依赖",
)
async def update_goal(
    book_id: UUID, item_id: UUID, goal_id: UUID, body: UpdateBudgetGoal, service: FundBudget
):
    return await service.save_goal(book_id, item_id, body, goal_id)


@router.get(
    "/items/{item_id}/funding-preview",
    response_model=BudgetFundingPreview,
    summary="按真实资金测算目标缺口",
)
async def funding_preview(
    book_id: UUID,
    item_id: UUID,
    request: Request,
    service: FundBudget,
    goal_id: Annotated[UUID, Query(alias="goalId")],
):
    if set(request.query_params) - {"goalId"}:
        raise ServiceError(422, "目标测算只接受 goalId，资金和日期由服务端读取")
    return await service.funding_preview(book_id, item_id, goal_id)


@router.get(
    "/entries/{entry_id}/link",
    response_model=BudgetEntryLinkState,
    summary="读取账单关联及防覆盖版本",
)
async def read_entry_link(book_id: UUID, entry_id: UUID, service: ExecutionBudget):
    return await service.read_link(book_id, entry_id)


@router.put(
    "/entries/{entry_id}/link",
    response_model=BudgetEntryLinkState,
    summary="将整笔支出关联至一个预算用途",
)
async def set_entry_link(
    book_id: UUID, entry_id: UUID, body: SetBudgetEntryLink, service: ExecutionBudget
):
    return await service.change_link(book_id, entry_id, body)


@router.delete(
    "/entries/{entry_id}/link",
    response_model=BudgetEntryLinkState,
    summary="取消归集并恢复派生用款，保留关联版本",
)
async def remove_entry_link(
    book_id: UUID, entry_id: UUID, body: RemoveBudgetEntryLink, service: ExecutionBudget
):
    return await service.change_link(book_id, entry_id, body)


@router.get(
    "/months/{month}/execution",
    response_model=BudgetMonthExecution,
    summary="读取月度计划、实际消费、存入及未关联支出",
)
async def month_execution(book_id: UUID, month: BudgetMonthKey, service: ExecutionBudget):
    return await service.month_execution(book_id, month)


@router.get(
    "/months/{month}/entries",
    response_model=BudgetEntryPage,
    summary="分页读取当月支出，可筛选用途或未关联项",
)
async def month_entries(
    book_id: UUID,
    month: BudgetMonthKey,
    service: ExecutionBudget,
    item_id: Annotated[UUID | None, Query(alias="itemId")] = None,
    unlinked_only: Annotated[bool, Query(alias="unlinkedOnly")] = False,
    before: Annotated[UUID | None, Query(alias="beforeEntryId")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    if item_id is not None and unlinked_only:
        raise ServiceError(422, "用途筛选和仅未关联不能同时使用")
    return await service.entry_page(
        book_id,
        month=month,
        item_id=item_id,
        unlinked_only=unlinked_only,
        before=before,
        limit=limit,
    )
