"""月度预算与精确均摊；查询不落快照，所有写入在账本锁内一次提交。"""

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.budget import BudgetMonth, BudgetPreview
from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    ApplyBudgetEqualization,
    BudgetEqualizationPreview,
    BudgetEqualizationResponse,
    BudgetItemAmount,
    BudgetMonthOverride,
    BudgetMonthResponse,
    BudgetRevision,
    ConfirmBudgetMonth,
    PreviewBudgetEqualization,
    UpdateBudgetMonth,
)
from app.schemas.budget import BudgetPlan as PlanData
from app.services.budget import SHANGHAI, BudgetService, request_hash, revision_response
from app.services.budget_math import allocate_cents, apply_month_override, summarize_budget


class BudgetMonthService(BudgetService):
    async def snapshot(self, book_id: UUID, month: str) -> BudgetMonth | None:
        return await self.session.scalar(
            select(BudgetMonth)
            .where(
                BudgetMonth.book_id == book_id,
                BudgetMonth.month == date.fromisoformat(month + "-01"),
            )
            .execution_options(populate_existing=True)
        )

    async def source_for_month(self, book_id: UUID, month: str) -> BudgetRevision | None:
        rows = await self.revisions(book_id)
        selected = next(
            (row for row in reversed(rows) if row.effective_month.strftime("%Y-%m") <= month), None
        )
        return revision_response(selected) if selected else None

    @staticmethod
    def snapshot_data(row: BudgetMonth) -> tuple[PlanData, BudgetMonthOverride, PlanData]:
        try:
            base = PlanData.model_validate(row.base_plan)
            override = BudgetMonthOverride.model_validate(row.override)
            resolved = apply_month_override(base, override)
            if PlanData.model_validate(row.resolved_plan) != resolved:
                raise ValueError("快照解析结果与原始方案和覆盖不一致")
            return base, override, resolved
        except (ValidationError, ValueError):
            raise ServiceError(503, "预算月份数据暂时不可用，请稍后重试") from None

    def month_response(
        self,
        book_id: UUID,
        month: str,
        plan_version: int,
        now: datetime,
        *,
        row: BudgetMonth | None = None,
        source: BudgetRevision | None = None,
    ) -> BudgetMonthResponse:
        override = BudgetMonthOverride()
        plan = source.plan if source else None
        if row is not None:
            _, override, plan = self.snapshot_data(row)
        return BudgetMonthResponse(
            book_id=book_id,
            month=month,
            plan_version=plan_version,
            state="confirmed" if row else "preview" if source else "not_set",
            snapshot_id=row.id if row else None,
            version=row.version if row else 0,
            source_revision_id=row.source_revision_id if row else source.id if source else None,
            plan=plan,
            override=override,
            totals=summarize_budget(plan) if plan is not None else None,
            generated_at=int(now.timestamp() * 1000),
        )

    async def read_month(self, book_id: UUID, month: str) -> BudgetMonthResponse:
        await self.ledger.require_book(book_id)
        root = await self.root(book_id)
        row = await self.snapshot(book_id, month)
        source = None if row else await self.source_for_month(book_id, month)
        return self.month_response(
            book_id, month, root.version if root else 0, self.now(), row=row, source=source
        )

    async def create_snapshot(
        self,
        book_id: UUID,
        month: str,
        source: BudgetRevision,
        override: BudgetMonthOverride | None = None,
    ) -> BudgetMonth:
        """调用方持有账本排他锁、核对该月尚无快照；本方法不提交事务。"""
        changes = override or BudgetMonthOverride()
        row = BudgetMonth(
            id=uuid4(),
            book_id=book_id,
            month=date.fromisoformat(month + "-01"),
            source_revision_id=source.id,
            base_plan=source.plan.model_dump(mode="json", by_alias=True),
            override=changes.model_dump(mode="json", by_alias=True),
            resolved_plan=apply_month_override(source.plan, changes).model_dump(
                mode="json", by_alias=True
            ),
            version=1,
            created_by=self.user_id,
            updated_by=self.user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def confirm_month(
        self, book_id: UUID, month: str, body: ConfirmBudgetMonth
    ) -> BudgetMonthResponse:
        body = ConfirmBudgetMonth.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        digest = request_hash({"month": month, "body": body.model_dump(mode="json", by_alias=True)})
        replay = await self.replay_mutation(
            book_id, body.request_id, "month_confirm", digest, BudgetMonthResponse
        )
        if replay is not None:
            await self.session.commit()
            return replay
        root = await self.root(book_id)
        # 确认并不递增方案根版本；只要求预览来自当前根版本。
        if (root.version if root else 0) != body.plan_version:
            raise ServiceError(409, "预算方案已更新，请刷新月份后重新确认")
        row = await self.snapshot(book_id, month)
        created = row is None
        if row is not None:
            if row.source_revision_id != body.source_revision_id:
                raise ServiceError(409, "该月已按其他方案确认，请读取已保存的预算")
        else:
            source = await self.source_for_month(book_id, month)
            if source is None:
                raise ServiceError(422, "该月尚未设置家庭预算，不能创建历史预算")
            if source.id != body.source_revision_id:
                raise ServiceError(409, "预算来源已变化，请刷新月份后重新确认")
            row = await self.create_snapshot(book_id, month, source)
        now = self.now()
        result = self.month_response(book_id, month, root.version, now, row=row)
        self.record_mutation(book_id, body.request_id, "month_confirm", digest, result)
        if created and book.kind == "shared":
            self.ledger.activity(
                book_id, "budget_confirm", row.id, {"month": month, "version": row.version}
            )
        await self.sync_project_status(book_id, now)
        await self.session.commit()
        return result

    def replace_override(self, row: BudgetMonth, override: BudgetMonthOverride):
        base, _, _ = self.snapshot_data(row)
        if row.version >= MAX_SAFE_INTEGER:
            raise ServiceError(409, "月预算版本已达到上限，暂时无法修改")
        try:
            resolved = apply_month_override(base, override)
        except ValueError:
            raise ServiceError(422, "单月覆盖只能选择该月原方案中的项目") from None
        row.override = override.model_dump(mode="json", by_alias=True)
        row.resolved_plan = resolved.model_dump(mode="json", by_alias=True)
        row.version += 1
        row.updated_by = self.user_id

    async def update_month(
        self, book_id: UUID, month: str, body: UpdateBudgetMonth
    ) -> BudgetMonthResponse:
        body = UpdateBudgetMonth.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        digest = request_hash({"month": month, "body": body.model_dump(mode="json", by_alias=True)})
        replay = await self.replay_mutation(
            book_id, body.request_id, "month_update", digest, BudgetMonthResponse
        )
        if replay is not None:
            await self.session.commit()
            return replay
        now = self.now()
        self.validate_month(month, now)
        row = await self.snapshot(book_id, month)
        if row is None:
            raise ServiceError(409, "该月预算尚未确认，请先读取并确认")
        if row.version != body.version:
            raise ServiceError(409, "月预算已在其他设备更新，请刷新后重试")
        self.replace_override(row, body.override)
        root = await self.root(book_id)
        result = self.month_response(book_id, month, root.version, now, row=row)
        self.record_mutation(book_id, body.request_id, "month_update", digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id, "budget_month", row.id, {"month": month, "version": row.version}
            )
        await self.session.commit()
        return result

    async def equalization_state(
        self,
        book_id: UUID,
        body: PreviewBudgetEqualization,
        now: datetime,
    ) -> tuple[list, dict, list]:
        self.validate_month(body.start_month, now)
        root = await self.root(book_id)
        self.validate_version(root, body.version, increment=False)
        schedule = allocate_cents(body.total_amount, body.start_month, body.end_month)
        rows = list(
            await self.session.scalars(
                select(BudgetMonth)
                .where(
                    BudgetMonth.book_id == book_id,
                    BudgetMonth.month >= date.fromisoformat(body.start_month + "-01"),
                    BudgetMonth.month <= date.fromisoformat(body.end_month + "-01"),
                )
                .order_by(BudgetMonth.month)
                .execution_options(populate_existing=True)
            )
        )
        snapshots = {row.month.strftime("%Y-%m"): row for row in rows}
        revisions = [revision_response(row) for row in await self.revisions(book_id)]
        index, source, targets, dependencies = 0, None, [], []
        for allocation in schedule:
            while index < len(revisions) and revisions[index].effective_month <= allocation.month:
                source = revisions[index]
                index += 1
            row = snapshots.get(allocation.month)
            if row is not None:
                base, override, _ = self.snapshot_data(row)
                if row.version >= MAX_SAFE_INTEGER:
                    raise ServiceError(409, "月预算版本已达到上限，暂时无法修改")
            else:
                if source is None:
                    raise ServiceError(422, "均摊区间含未设置预算的月份，请先安排方案")
                base, override = source.plan, BudgetMonthOverride()
            if body.item_id not in {item.id for item in base.items}:
                raise ServiceError(
                    422, f"{allocation.month} 的原方案中没有所选项目，请调整均摊区间"
                )
            amounts = [value for value in override.item_amounts if value.item_id != body.item_id]
            amounts.append(BudgetItemAmount(item_id=body.item_id, amount=allocation.amount))
            changes = BudgetMonthOverride(
                planned_income=override.planned_income, item_amounts=amounts
            )
            targets.append((allocation.month, row, source, changes))
            dependencies.append(
                {
                    "month": allocation.month,
                    "snapshotId": str(row.id) if row else None,
                    "version": row.version if row else 0,
                    "sourceRevisionId": str(row.source_revision_id if row else source.id),
                }
            )
        return (
            schedule,
            {
                "version": body.version,
                "businessMonth": now.astimezone(SHANGHAI).strftime("%Y-%m"),
                "months": dependencies,
            },
            targets,
        )

    async def preview_equalization(
        self, book_id: UUID, body: PreviewBudgetEqualization
    ) -> BudgetEqualizationPreview:
        body = PreviewBudgetEqualization.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        now = self.now()
        schedule, dependencies, _ = await self.equalization_state(book_id, body, now)
        impact = {
            "schedule": [value.model_dump(mode="json", by_alias=True) for value in schedule],
            "affectedMonths": [value.month for value in schedule],
        }
        preview = BudgetPreview(
            book_id=book_id,
            actor_id=self.user_id,
            operation="equalize",
            request_hash=request_hash(body.model_dump(mode="json", by_alias=True)),
            dependencies=dependencies,
            impact=impact,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        self.session.add(preview)
        await self.session.flush()
        result = BudgetEqualizationPreview(
            impact_token=preview.token,
            expires_at=int(preview.expires_at.timestamp() * 1000),
            **impact,
        )
        await self.session.commit()
        return result

    async def apply_equalization(
        self, book_id: UUID, body: ApplyBudgetEqualization
    ) -> BudgetEqualizationResponse:
        body = ApplyBudgetEqualization.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        digest = request_hash(body.model_dump(mode="json", by_alias=True))
        replay = await self.replay_mutation(
            book_id, body.request_id, "equalize", digest, BudgetEqualizationResponse
        )
        if replay is not None:
            await self.session.commit()
            return replay
        now = self.now()
        normalized = PreviewBudgetEqualization.model_validate(
            body.model_dump(exclude={"request_id", "impact_token"})
        )
        preview = await self.session.scalar(
            select(BudgetPreview)
            .where(
                BudgetPreview.token == body.impact_token,
                BudgetPreview.book_id == book_id,
                BudgetPreview.actor_id == self.user_id,
                BudgetPreview.operation == "equalize",
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
            raise ServiceError(409, "均摊预览已失效，请重新预览后保存")
        schedule, dependencies, targets = await self.equalization_state(book_id, normalized, now)
        impact = {
            "schedule": [value.model_dump(mode="json", by_alias=True) for value in schedule],
            "affectedMonths": [value.month for value in schedule],
        }
        if preview.dependencies != dependencies or preview.impact != impact:
            raise ServiceError(409, "均摊涉及的月预算已变化，请重新预览后保存")
        months = []
        for month, row, source, override in targets:
            if row is None:
                row = await self.create_snapshot(book_id, month, source, override)
            else:
                self.replace_override(row, override)
            months.append(self.month_response(book_id, month, body.version, now, row=row))
        result = BudgetEqualizationResponse(months=months)
        preview.consumed_at = now
        self.record_mutation(book_id, body.request_id, "equalize", digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_equalize",
                None,
                {
                    "startMonth": body.start_month,
                    "endMonth": body.end_month,
                    "itemId": str(body.item_id),
                    "totalAmount": body.total_amount,
                },
            )
        await self.sync_project_status(book_id, now)
        await self.session.commit()
        return result
