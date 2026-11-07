"""预算方案服务；在账本锁内处理版本、影响预览、项目身份和幂等。"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ServiceError
from app.models.budget import (
    BudgetMonth,
    BudgetMutation,
    BudgetPreview,
    BudgetProject,
)
from app.models.budget import (
    BudgetPlan as PlanRow,
)
from app.models.budget import (
    BudgetRevision as RevisionRow,
)
from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    BudgetImpactResponse,
    BudgetMonthOverride,
    BudgetPlanResponse,
    BudgetRevision,
    PreviewBudgetRevision,
    SaveBudgetRevision,
    SavedBudgetRevision,
)
from app.schemas.budget import (
    BudgetPlan as PlanData,
)
from app.services.budget_math import effective_revision, summarize_budget
from app.services.ledger import LedgerService

# 业务日期范围为 2000—2100 年，按北京时间 UTC+8 计算，避免依赖 Windows 时区数据库。
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
MutationResult = TypeVar("MutationResult", bound=BaseModel)


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def revision_response(row: RevisionRow) -> BudgetRevision:
    try:
        return BudgetRevision(
            id=row.id, effective_month=row.effective_month.strftime("%Y-%m"), plan=row.plan
        )
    except ValidationError:
        raise ServiceError(503, "预算数据暂时不可用，请稍后重试") from None


class BudgetService:
    def __init__(self, ledger: LedgerService, *, clock: Callable[[], datetime] | None = None):
        self.ledger = ledger
        self.session = ledger.session
        self.user_id = ledger.user_id
        self.clock = clock or utc_now

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("预算服务时钟必须包含时区")
        return value.astimezone(UTC)

    async def replay_mutation(
        self,
        book_id: UUID,
        request_id: UUID,
        operation: str,
        digest: str,
        result_type: type[MutationResult],
    ) -> MutationResult | None:
        """外层必须先锁账本并重新验证权限；重放先于版本和过期校验。"""
        previous = await self.session.get(
            BudgetMutation, (book_id, request_id), populate_existing=True
        )
        if previous is None:
            return None
        if (
            previous.actor_id != self.user_id
            or previous.operation != operation
            or previous.request_hash != digest
        ):
            raise ServiceError(409, "这次请求已使用其他内容处理，请刷新后重试")
        try:
            return result_type.model_validate(previous.result)
        except ValidationError:
            raise ServiceError(503, "预算数据暂时不可用，请稍后重试") from None

    def record_mutation(
        self,
        book_id: UUID,
        request_id: UUID,
        operation: str,
        digest: str,
        result: BaseModel,
    ):
        self.session.add(
            BudgetMutation(
                book_id=book_id,
                request_id=request_id,
                actor_id=self.user_id,
                operation=operation,
                request_hash=digest,
                result=result.model_dump(mode="json", by_alias=True),
            )
        )

    async def root(self, book_id: UUID) -> PlanRow | None:
        return await self.session.scalar(
            select(PlanRow)
            .where(PlanRow.book_id == book_id)
            .execution_options(populate_existing=True)
        )

    async def revisions(self, book_id: UUID) -> list[RevisionRow]:
        return list(
            await self.session.scalars(
                select(RevisionRow)
                .where(
                    RevisionRow.book_id == book_id,
                    RevisionRow.superseded_at.is_(None),
                    RevisionRow.monthly_only.is_(False),
                )
                .order_by(RevisionRow.effective_month)
                .execution_options(populate_existing=True)
            )
        )

    async def read_plan(self, book_id: UUID) -> BudgetPlanResponse:
        await self.ledger.require_book(book_id)
        root = await self.root(book_id)
        rows = await self.revisions(book_id)
        return BudgetPlanResponse(
            book_id=book_id,
            version=root.version if root else 0,
            revisions=[revision_response(row) for row in rows],
        )

    @staticmethod
    def validate_version(root: PlanRow | None, expected: int, *, increment: bool = True):
        if (root.version if root else 0) != expected:
            raise ServiceError(409, "预算方案已在其他设备更新，请刷新后重新预览")
        if increment and expected >= MAX_SAFE_INTEGER:
            raise ServiceError(409, "预算方案版本已达到上限，暂时无法修改")

    @staticmethod
    def validate_month(month: str, now: datetime) -> date:
        effective = date.fromisoformat(month + "-01")
        if effective < now.astimezone(SHANGHAI).date().replace(day=1):
            raise ServiceError(422, "不能修改过去月份的预算方案，请选择本月或未来月份")
        return effective

    async def validate_projects(self, book_id: UUID, plan: PlanData) -> dict[UUID, BudgetProject]:
        ids = [item.id for item in plan.items]
        rows = (
            list(
                await self.session.scalars(
                    select(BudgetProject)
                    .where(BudgetProject.id.in_(ids))
                    .order_by(BudgetProject.id)
                    .execution_options(populate_existing=True)
                )
            )
            if ids
            else []
        )
        existing = {row.id: row for row in rows}
        for item in plan.items:
            row = existing.get(item.id)
            if row is not None:
                if row.book_id != book_id:
                    raise ServiceError(404, "预算项目不存在或无权访问")
                if row.kind != item.kind:
                    raise ServiceError(409, "预算项目性质不可更改，请为新用途创建项目")
        return existing

    async def impact(
        self, book_id: UUID, body: PreviewBudgetRevision, now: datetime
    ) -> tuple[dict, dict]:
        rows = await self.revisions(book_id)
        next_month = next(
            (
                row.effective_month
                for row in rows
                if row.effective_month > date.fromisoformat(body.effective_month + "-01")
            ),
            None,
        )
        statement = select(BudgetMonth).where(
            BudgetMonth.book_id == book_id,
            BudgetMonth.month >= date.fromisoformat(body.effective_month + "-01"),
        )
        if next_month is not None:
            statement = statement.where(BudgetMonth.month < next_month)
        months = list(
            await self.session.scalars(
                statement.order_by(BudgetMonth.month).execution_options(populate_existing=True)
            )
        )
        confirmed, overridden, snapshots = [], [], []
        for row in months:
            month = row.month.strftime("%Y-%m")
            confirmed.append(month)
            try:
                override = BudgetMonthOverride.model_validate(row.override)
            except ValidationError:
                raise ServiceError(503, "预算数据暂时不可用，请稍后重试") from None
            if override.planned_income is not None or override.item_amounts:
                overridden.append(month)
            snapshots.append({"id": str(row.id), "month": month, "version": row.version})
        summary = {
            "version": body.version,
            "effectiveMonth": body.effective_month,
            "untilMonth": (next_month - timedelta(days=1)).strftime("%Y-%m")
            if next_month
            else None,
            "confirmedMonths": confirmed,
            "overriddenMonths": overridden,
            "totals": summarize_budget(body.plan).model_dump(mode="json", by_alias=True),
        }
        dependencies = {
            "version": body.version,
            "snapshots": snapshots,
            "businessMonth": now.astimezone(SHANGHAI).strftime("%Y-%m"),
        }
        return summary, dependencies

    async def preview_plan(
        self, book_id: UUID, body: PreviewBudgetRevision
    ) -> BudgetImpactResponse:
        body = PreviewBudgetRevision.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        now = self.now()
        self.validate_version(await self.root(book_id), body.version)
        self.validate_month(body.effective_month, now)
        await self.validate_projects(book_id, body.plan)
        summary, dependencies = await self.impact(book_id, body, now)
        preview = BudgetPreview(
            book_id=book_id,
            actor_id=self.user_id,
            operation="plan_save",
            request_hash=request_hash(body.model_dump(mode="json", by_alias=True)),
            dependencies=dependencies,
            impact=summary,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        self.session.add(preview)
        await self.session.flush()
        result = BudgetImpactResponse(
            impact_token=preview.token,
            expires_at=int(preview.expires_at.timestamp() * 1000),
            **summary,
        )
        await self.session.commit()
        return result

    async def register_projects(self, book_id: UUID, plan: PlanData, effective: date):
        existing = await self.validate_projects(book_id, plan)
        for item in sorted(plan.items, key=lambda value: str(value.id)):
            row = existing.get(item.id)
            if row is None:
                await self.session.execute(
                    insert(BudgetProject)
                    .values(id=item.id, book_id=book_id, kind=item.kind, enabled_month=effective)
                    .on_conflict_do_nothing(index_elements=[BudgetProject.id])
                )
                row = await self.session.get(BudgetProject, item.id, populate_existing=True)
                # 另一个账本可能在预览后抢先使用同一 UUID，不能将它认作本账本项目。
                if row.book_id != book_id:
                    raise ServiceError(404, "预算项目不存在或无权访问")
                if row.kind != item.kind:
                    raise ServiceError(409, "预算项目性质不可更改，请为新用途创建项目")
            if effective < row.enabled_month:
                row.enabled_month = effective

    async def active_project_ids(self, book_id: UUID, now: datetime) -> set[UUID]:
        month = now.astimezone(SHANGHAI).date().replace(day=1)
        revisions = [revision_response(row) for row in await self.revisions(book_id)]
        current = effective_revision(revisions, month.strftime("%Y-%m"))
        plans = [row.plan for row in revisions if row.effective_month > month.strftime("%Y-%m")]
        if current is not None:
            plans.append(current.plan)
        snapshots = await self.session.scalars(
            select(BudgetMonth.resolved_plan).where(
                BudgetMonth.book_id == book_id,
                BudgetMonth.month >= month,
                BudgetMonth.is_deleted.is_(False),
            )
        )
        try:
            plans.extend(PlanData.model_validate(value) for value in snapshots)
        except ValidationError:
            raise ServiceError(503, "预算数据暂时不可用，请稍后重试") from None
        return {item.id for plan in plans for item in plan.items}

    async def sync_project_status(self, book_id: UUID, now: datetime):
        active = await self.active_project_ids(book_id, now)
        for status, condition in (
            ("active", BudgetProject.id.in_(active)),
            ("archived", BudgetProject.id.not_in(active)),
        ):
            await self.session.execute(
                update(BudgetProject)
                .where(
                    BudgetProject.book_id == book_id,
                    condition,
                    BudgetProject.status != status,
                )
                .values(status=status)
            )

    async def save_plan(self, book_id: UUID, body: SaveBudgetRevision) -> SavedBudgetRevision:
        body = SaveBudgetRevision.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        digest = request_hash(body.model_dump(mode="json", by_alias=True))
        previous = await self.replay_mutation(
            book_id, body.request_id, "plan_save", digest, SavedBudgetRevision
        )
        if previous is not None:
            await self.session.commit()
            return previous

        now = self.now()
        root = await self.root(book_id)
        self.validate_version(root, body.version)
        effective = self.validate_month(body.effective_month, now)
        normalized = PreviewBudgetRevision(
            version=body.version, effective_month=body.effective_month, plan=body.plan
        )
        preview = await self.session.scalar(
            select(BudgetPreview)
            .where(
                BudgetPreview.token == body.impact_token,
                BudgetPreview.book_id == book_id,
                BudgetPreview.actor_id == self.user_id,
                BudgetPreview.operation == "plan_save",
            )
            .execution_options(populate_existing=True)
        )
        if (
            preview is None
            or preview.consumed_at is not None
            or preview.expires_at <= now
            or preview.request_hash
            != request_hash(normalized.model_dump(mode="json", by_alias=True))
        ):
            raise ServiceError(409, "预算预览已失效，请重新预览后保存")
        summary, dependencies = await self.impact(book_id, normalized, now)
        if preview.dependencies != dependencies or preview.impact != summary:
            raise ServiceError(409, "预算影响范围已变化，请重新预览后保存")
        if root is None:
            root = PlanRow(id=uuid4(), book_id=book_id, version=0)
            self.session.add(root)
            await self.session.flush()
        await self.register_projects(book_id, body.plan, effective)
        await self.session.execute(
            update(RevisionRow)
            .where(
                RevisionRow.plan_id == root.id,
                RevisionRow.effective_month == effective,
                RevisionRow.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )
        revision = RevisionRow(
            id=uuid4(),
            plan_id=root.id,
            book_id=book_id,
            effective_month=effective,
            plan=body.plan.model_dump(mode="json", by_alias=True),
            created_by=self.user_id,
            created_at=now,
        )
        self.session.add(revision)
        root.version += 1
        preview.consumed_at = now
        await self.session.flush()
        await self.sync_project_status(book_id, now)
        result = SavedBudgetRevision(
            book_id=book_id, version=root.version, revision=revision_response(revision)
        )
        self.record_mutation(book_id, body.request_id, "plan_save", digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_plan",
                revision.id,
                {"version": root.version, "effectiveMonth": body.effective_month},
            )
        await self.session.commit()
        return result
