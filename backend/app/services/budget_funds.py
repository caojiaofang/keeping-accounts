"""用途资金与目标执行；余额只来自有效流水，结转不产生第二份资金。"""

from datetime import date, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ServiceError
from app.models.budget import BudgetFundRecord, BudgetGoalPeriod, BudgetProject
from app.models.budget import BudgetRevision as RevisionRow
from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    BudgetFundRecordResponse,
    BudgetFundSummary,
    BudgetGoalResponse,
    BudgetGoalTerms,
    BudgetItemDetail,
    BudgetProjectPage,
    BudgetProjectResponse,
    BudgetTarget,
    CorrectBudgetFund,
    CreateBudgetFund,
    CreateBudgetGoal,
    SavedBudgetFund,
    SavedBudgetGoal,
    UpdateBudgetGoal,
)
from app.services.budget import SHANGHAI, request_hash, revision_response
from app.services.budget_math import target_funding_preview
from app.services.budget_months import BudgetMonthService


def milliseconds(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value is not None else None


def safe_amount(value: int) -> int:
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        raise ServiceError(409, "资金合计超过支持范围，请检查记录")
    return value


def fund_summary(records: list[BudgetFundRecord], as_of: date) -> BudgetFundSummary:
    totals = {"opening": 0, "deposit": 0, "use": 0}
    for record in records:
        if record.voided_at is None and record.date <= as_of:
            totals[record.kind] += record.amount
    return BudgetFundSummary(
        as_of_date=as_of,
        opening_amount=safe_amount(totals["opening"]),
        deposited_amount=safe_amount(totals["deposit"]),
        used_amount=safe_amount(totals["use"]),
        available_amount=safe_amount(totals["opening"] + totals["deposit"] - totals["use"]),
    )


def fund_response(row: BudgetFundRecord) -> BudgetFundRecordResponse:
    return BudgetFundRecordResponse(
        id=row.id,
        item_id=row.project_id,
        kind=row.kind,
        amount=row.amount,
        date=row.date,
        note=row.note,
        entry_id=row.entry_id,
        goal_period_id=row.goal_period_id,
        replaces_id=row.replaces_id,
        version=row.version,
        voided_at=milliseconds(row.voided_at),
        created_at=milliseconds(row.created_at),
        updated_at=milliseconds(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


class BudgetFundService(BudgetMonthService):
    async def project(self, book_id: UUID, item_id: UUID, *, write=False) -> BudgetProject:
        statement = (
            select(BudgetProject)
            .where(
                BudgetProject.book_id == book_id,
                BudgetProject.id == item_id,
            )
            .execution_options(populate_existing=True)
        )
        if write:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise ServiceError(404, "预算项目不存在或无权访问")
        return row

    @staticmethod
    def validate_fund_version(project: BudgetProject, expected: int):
        if project.fund_version != expected:
            raise ServiceError(409, "用途资金已在其他设备更新，请刷新后重试")
        if project.fund_version >= MAX_SAFE_INTEGER:
            raise ServiceError(409, "资金版本已达到上限，暂时无法修改")
        if project.kind not in {"reserve", "saving"}:
            raise ServiceError(422, "只有预留和储蓄项目可以登记用途资金")

    async def records(self, book_id: UUID, item_id: UUID) -> list[BudgetFundRecord]:
        return list(
            await self.session.scalars(
                select(BudgetFundRecord)
                .where(
                    BudgetFundRecord.book_id == book_id,
                    BudgetFundRecord.project_id == item_id,
                    BudgetFundRecord.voided_at.is_(None),
                )
                .order_by(BudgetFundRecord.date, BudgetFundRecord.id)
                .execution_options(populate_existing=True)
            )
        )

    async def goals(self, book_id: UUID, item_id: UUID) -> list[BudgetGoalPeriod]:
        return list(
            await self.session.scalars(
                select(BudgetGoalPeriod)
                .where(
                    BudgetGoalPeriod.book_id == book_id,
                    BudgetGoalPeriod.project_id == item_id,
                )
                .order_by(BudgetGoalPeriod.start_date, BudgetGoalPeriod.id)
                .execution_options(populate_existing=True)
            )
        )

    async def project_response(
        self, row: BudgetProject, active: set[UUID]
    ) -> BudgetProjectResponse:
        source = await self.session.scalar(
            select(RevisionRow)
            .where(
                RevisionRow.book_id == row.book_id,
                RevisionRow.plan.contains({"items": [{"id": str(row.id)}]}),
            )
            .order_by(
                RevisionRow.effective_month.desc(),
                RevisionRow.superseded_at.is_(None).desc(),
                RevisionRow.created_at.desc(),
                RevisionRow.id.desc(),
            )
            .limit(1)
        )
        configuration = (
            next((item for item in revision_response(source).plan.items if item.id == row.id), None)
            if source
            else None
        )
        if configuration is None or configuration.kind != row.kind:
            raise ServiceError(503, "预算项目历史配置暂时不可用，请稍后重试")
        return BudgetProjectResponse(
            id=row.id,
            kind=row.kind,
            status="active" if row.id in active else "archived",
            enabled_month=row.enabled_month.strftime("%Y-%m"),
            fund_version=row.fund_version,
            configuration=configuration,
        )

    async def list_projects(
        self, book_id: UUID, *, after: UUID | None, limit: int
    ) -> BudgetProjectPage:
        await self.ledger.require_book(book_id)
        statement = select(BudgetProject).where(BudgetProject.book_id == book_id)
        if after is not None:
            statement = statement.where(BudgetProject.id > after)
        rows = list(
            await self.session.scalars(statement.order_by(BudgetProject.id).limit(limit + 1))
        )
        active = await self.active_project_ids(book_id, self.now())
        return BudgetProjectPage(
            book_id=book_id,
            items=[await self.project_response(row, active) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    def goal_response(
        self,
        goal: BudgetGoalPeriod,
        records: list[BudgetFundRecord],
        now: datetime,
    ) -> BudgetGoalResponse:
        today = now.astimezone(SHANGHAI).date()
        collected = safe_amount(
            goal.carry_in_amount
            + sum(
                row.amount
                for row in records
                if row.kind == "deposit" and row.goal_period_id == goal.id and row.date <= today
            )
        )
        used = safe_amount(
            sum(
                row.amount
                for row in records
                if row.kind == "use" and row.goal_period_id == goal.id and row.date <= today
            )
        )
        cutoff = goal.closed_at.astimezone(SHANGHAI).date() if goal.state == "closed" else today
        available = min(
            safe_amount(collected - used), fund_summary(records, cutoff).available_amount
        )
        try:
            forecast = target_funding_preview(
                BudgetTarget(
                    start_date=goal.start_date,
                    target_date=goal.target_date,
                    target_amount=goal.target_amount,
                    available_amount=available,
                    used_amount=used,
                ),
                as_of=cutoff,
            )
        except (ValueError, ValidationError):
            raise ServiceError(409, "目标测算超过支持范围，请检查资金记录") from None
        if goal.state == "closed":
            forecast = forecast.model_copy(
                update={"remaining_months": 0, "additional_schedule": []}
            )
        return BudgetGoalResponse(
            id=goal.id,
            item_id=goal.project_id,
            start_date=goal.start_date,
            target_date=goal.target_date,
            target_amount=goal.target_amount,
            carry_in_amount=goal.carry_in_amount,
            state=goal.state,
            version=goal.version,
            closed_at=milliseconds(goal.closed_at),
            collected_amount=collected,
            used_amount=used,
            available_amount=available,
            forecast=forecast,
        )

    async def detail(
        self,
        book_id: UUID,
        item_id: UUID,
        *,
        before_record_id: UUID | None,
        record_limit: int,
    ) -> BudgetItemDetail:
        await self.ledger.require_book(book_id)
        project = await self.project(book_id, item_id)
        now = self.now()
        records = await self.records(book_id, item_id)
        goals = await self.goals(book_id, item_id)
        statement = select(BudgetFundRecord).where(
            BudgetFundRecord.book_id == book_id,
            BudgetFundRecord.project_id == item_id,
        )
        if before_record_id is not None:
            pivot = await self.session.scalar(
                select(BudgetFundRecord).where(
                    BudgetFundRecord.id == before_record_id,
                    BudgetFundRecord.book_id == book_id,
                    BudgetFundRecord.project_id == item_id,
                )
            )
            if pivot is None:
                raise ServiceError(404, "资金记录不存在或无权访问")
            statement = statement.where(
                or_(
                    BudgetFundRecord.created_at < pivot.created_at,
                    and_(
                        BudgetFundRecord.created_at == pivot.created_at,
                        BudgetFundRecord.id < pivot.id,
                    ),
                )
            )
        page = list(
            await self.session.scalars(
                statement.order_by(
                    BudgetFundRecord.created_at.desc(),
                    BudgetFundRecord.id.desc(),
                ).limit(record_limit + 1)
            )
        )
        return BudgetItemDetail(
            book_id=book_id,
            generated_at=milliseconds(now),
            item=await self.project_response(project, await self.active_project_ids(book_id, now)),
            balance=fund_summary(records, now.astimezone(SHANGHAI).date()),
            goals=[self.goal_response(goal, records, now) for goal in goals],
            records=[fund_response(row) for row in page[:record_limit]],
            next_record_cursor=page[record_limit - 1].id if len(page) > record_limit else None,
        )

    async def funding_preview(self, book_id: UUID, item_id: UUID, goal_id: UUID):
        await self.ledger.require_book(book_id)
        await self.project(book_id, item_id)
        goal = await self.session.scalar(
            select(BudgetGoalPeriod).where(
                BudgetGoalPeriod.id == goal_id,
                BudgetGoalPeriod.book_id == book_id,
                BudgetGoalPeriod.project_id == item_id,
            )
        )
        if goal is None:
            raise ServiceError(404, "储蓄目标不存在或无权访问")
        return self.goal_response(goal, await self.records(book_id, item_id), self.now()).forecast

    async def assign_deposits(self, book_id: UUID, item_id: UUID):
        goals = await self.goals(book_id, item_id)
        for row in await self.records(book_id, item_id):
            if row.kind != "deposit":
                continue
            selected = next(
                (goal.id for goal in goals if goal.start_date <= row.date <= goal.target_date), None
            )
            if row.goal_period_id != selected:
                if row.version >= MAX_SAFE_INTEGER:
                    raise ServiceError(409, "资金记录版本已达到上限，暂时无法修改")
                row.goal_period_id = selected
                row.version += 1
                row.updated_by = self.user_id

    async def freeze_execution_month(self, book_id: UUID, item_id: UUID, when: date):
        month = when.strftime("%Y-%m")
        if await self.snapshot(book_id, month) is not None:
            return
        source = await self.source_for_month(book_id, month)
        if source and any(item.id == item_id for item in source.plan.items):
            await self.create_snapshot(book_id, month, source)

    @staticmethod
    def validate_history(project: BudgetProject, records: list[BudgetFundRecord], today: date):
        opening = [row for row in records if row.kind == "opening"]
        if len(opening) > 1:
            raise ServiceError(409, "该用途已经登记期初储备，请修正原记录")
        if any(row.date < project.enabled_month or row.date > today for row in records):
            raise ServiceError(422, "资金日期不能早于用途启用月或晚于北京时间今天")
        if opening and any(row.date < opening[0].date for row in records):
            raise ServiceError(409, "期初日期不能晚于已有资金记录，请先核对日期")
        fund_summary(records, today)

    async def insert_record(self, project: BudgetProject, values: dict) -> BudgetFundRecord:
        inserted = await self.session.scalar(
            insert(BudgetFundRecord)
            .values(
                project_id=project.id,
                book_id=project.book_id,
                created_by=self.user_id,
                updated_by=self.user_id,
                **values,
            )
            .on_conflict_do_nothing(index_elements=[BudgetFundRecord.id])
            .returning(BudgetFundRecord.id)
        )
        if inserted is None:
            row = await self.session.get(BudgetFundRecord, values["id"], populate_existing=True)
            if row.project_id != project.id or row.book_id != project.book_id:
                raise ServiceError(404, "资金记录不存在或无权访问")
            raise ServiceError(409, "该资金记录 ID 已经使用，请刷新后核对")
        return await self.session.get(BudgetFundRecord, inserted, populate_existing=True)

    async def create_fund(
        self, book_id: UUID, item_id: UUID, body: CreateBudgetFund
    ) -> SavedBudgetFund:
        body = CreateBudgetFund.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        project = await self.project(book_id, item_id, write=True)
        digest = request_hash(
            {"itemId": str(item_id), "body": body.model_dump(mode="json", by_alias=True)}
        )
        replay = await self.replay_mutation(
            book_id, body.request_id, "fund_create", digest, SavedBudgetFund
        )
        if replay is not None:
            await self.session.commit()
            return replay
        self.validate_fund_version(project, body.fund_version)
        now = self.now()
        # 先校验候选历史，避免部分唯一索引把可解释的业务冲突转成数据库错误。
        rows = await self.records(book_id, item_id)
        candidate = BudgetFundRecord(
            kind=body.kind, amount=body.amount, date=body.date, voided_at=None
        )
        self.validate_history(project, [*rows, candidate], now.astimezone(SHANGHAI).date())
        row = await self.insert_record(
            project, body.model_dump(exclude={"request_id", "fund_version"})
        )
        await self.assign_deposits(book_id, item_id)
        await self.freeze_execution_month(book_id, item_id, body.date)
        project.fund_version += 1
        await self.session.flush()
        await self.session.refresh(row)
        result = SavedBudgetFund(
            book_id=book_id,
            item_id=item_id,
            fund_version=project.fund_version,
            record=fund_response(row),
            balance=fund_summary(
                await self.records(book_id, item_id), now.astimezone(SHANGHAI).date()
            ),
        )
        self.record_mutation(book_id, body.request_id, "fund_create", digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_fund",
                row.id,
                {"itemId": str(item_id), "fundVersion": project.fund_version},
            )
        await self.session.commit()
        return result

    async def correct_fund(
        self, book_id: UUID, item_id: UUID, record_id: UUID, body: CorrectBudgetFund
    ) -> SavedBudgetFund:
        body = CorrectBudgetFund.model_validate(body.model_dump())
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        project = await self.project(book_id, item_id, write=True)
        digest = request_hash(
            {
                "itemId": str(item_id),
                "recordId": str(record_id),
                "body": body.model_dump(mode="json", by_alias=True),
            }
        )
        replay = await self.replay_mutation(
            book_id, body.request_id, "fund_correct", digest, SavedBudgetFund
        )
        if replay is not None:
            await self.session.commit()
            return replay
        self.validate_fund_version(project, body.fund_version)
        original = await self.session.scalar(
            select(BudgetFundRecord)
            .where(
                BudgetFundRecord.id == record_id,
                BudgetFundRecord.book_id == book_id,
                BudgetFundRecord.project_id == item_id,
            )
            .execution_options(populate_existing=True)
        )
        if original is None:
            raise ServiceError(404, "资金记录不存在或无权访问")
        if original.kind == "use":
            raise ServiceError(422, "用款记录须通过原账单或关联修正，不能直接改资金")
        if (
            original.voided_at is not None
            or original.version != body.record_version
            or original.version >= MAX_SAFE_INTEGER
        ):
            raise ServiceError(409, "资金记录已变化，请刷新后核对")
        now = self.now()
        remaining = [row for row in await self.records(book_id, item_id) if row.id != original.id]
        if body.replacement:
            remaining.append(
                BudgetFundRecord(
                    kind=original.kind,
                    amount=body.replacement.amount,
                    date=body.replacement.date,
                    voided_at=None,
                )
            )
        elif original.kind == "opening" and remaining:
            raise ServiceError(409, "已有资金执行时不能移除期初，请改用修正记录")
        self.validate_history(project, remaining, now.astimezone(SHANGHAI).date())
        original.voided_at = now
        original.version += 1
        original.updated_by = self.user_id
        await self.session.flush()
        replacement = None
        if body.replacement:
            replacement = await self.insert_record(
                project,
                {
                    **body.replacement.model_dump(),
                    "kind": original.kind,
                    "replaces_id": original.id,
                },
            )
            await self.freeze_execution_month(book_id, item_id, replacement.date)
        await self.assign_deposits(book_id, item_id)
        project.fund_version += 1
        await self.session.flush()
        await self.session.refresh(original)
        if replacement is not None:
            await self.session.refresh(replacement)
        result = SavedBudgetFund(
            book_id=book_id,
            item_id=item_id,
            fund_version=project.fund_version,
            record=fund_response(original),
            replacement=fund_response(replacement) if replacement else None,
            balance=fund_summary(
                await self.records(book_id, item_id), now.astimezone(SHANGHAI).date()
            ),
        )
        self.record_mutation(book_id, body.request_id, "fund_correct", digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_fund_correct",
                original.id,
                {"itemId": str(item_id), "fundVersion": project.fund_version},
            )
        await self.session.commit()
        return result

    async def validate_goal_terms(
        self,
        project: BudgetProject,
        body: BudgetGoalTerms,
        now: datetime,
        *,
        editing: BudgetGoalPeriod | None = None,
    ):
        if project.kind != "saving":
            raise ServiceError(422, "只有储蓄用途可以设置储蓄目标")
        if body.start_date < project.enabled_month:
            raise ServiceError(422, "目标开始日不能早于用途启用月")
        goals = await self.goals(project.book_id, project.id)
        others = [goal for goal in goals if editing is None or goal.id != editing.id]
        if any(goal.state == "active" for goal in others):
            raise ServiceError(409, "请先关闭当前目标，再续建下一期")
        if others and body.start_date <= max(goal.target_date for goal in others):
            raise ServiceError(409, "新一期须在已有目标截止日之后开始，不能重叠或回写历史")
        rows = await self.records(project.book_id, project.id)
        if editing and any(
            row.goal_period_id == editing.id
            and (
                row.date < body.start_date
                or (row.kind == "deposit" and row.date > body.target_date)
            )
            for row in rows
        ):
            raise ServiceError(409, "目标日期调整会移出已有执行记录，请先核对相关记录")
        today = now.astimezone(SHANGHAI).date()
        visible = [row for row in rows if row.date <= today]
        pool = sum(
            row.amount if row.kind != "use" else -row.amount
            for row in visible
            if (row.kind == "opening" and row.date <= body.target_date)
            or (row.kind != "opening" and row.date < body.start_date)
        )
        deposits = sum(
            row.amount
            for row in visible
            if row.kind == "deposit" and body.start_date <= row.date <= body.target_date
        )
        own_used = sum(
            row.amount
            for row in visible
            if row.kind == "use" and editing and row.goal_period_id == editing.id
        )
        eligible = max(
            min(pool, fund_summary(rows, today).available_amount + own_used - deposits), 0
        )
        if body.carry_in_amount > eligible:
            raise ServiceError(409, "结转金额超过实际可分配资金，不能重复计算本期存入")

    async def save_goal(
        self,
        book_id: UUID,
        item_id: UUID,
        body: CreateBudgetGoal | UpdateBudgetGoal,
        goal_id: UUID | None = None,
    ) -> SavedBudgetGoal:
        creating = goal_id is None
        body = (CreateBudgetGoal if creating else UpdateBudgetGoal).model_validate(
            body.model_dump()
        )
        operation = "goal_create" if creating else "goal_update"
        book = await self.ledger.require_book(book_id, write=True)
        self.ledger.require_owner(book)
        project = await self.project(book_id, item_id, write=True)
        digest = request_hash(
            {
                "itemId": str(item_id),
                "goalId": str(goal_id) if goal_id else None,
                "body": body.model_dump(mode="json", by_alias=True),
            }
        )
        replay = await self.replay_mutation(
            book_id, body.request_id, operation, digest, SavedBudgetGoal
        )
        if replay is not None:
            await self.session.commit()
            return replay
        self.validate_fund_version(project, body.fund_version)
        now = self.now()
        goal = None
        if not creating:
            goal = await self.session.scalar(
                select(BudgetGoalPeriod)
                .where(
                    BudgetGoalPeriod.id == goal_id,
                    BudgetGoalPeriod.project_id == item_id,
                    BudgetGoalPeriod.book_id == book_id,
                )
                .execution_options(populate_existing=True)
            )
            if goal is None:
                raise ServiceError(404, "储蓄目标不存在或无权访问")
            if (
                goal.state == "closed"
                or goal.version != body.version
                or goal.version >= MAX_SAFE_INTEGER
            ):
                raise ServiceError(409, "目标已关闭或已更新，请刷新后核对")
            if body.state == "closed" and body.target_date > now.astimezone(SHANGHAI).date():
                raise ServiceError(422, "提前关闭目标时，请把截止日调整到实际结束日")
        elif project.id not in await self.active_project_ids(book_id, now):
            raise ServiceError(409, "该用途已归档，请先在方案中恢复安排再创建目标")
        await self.validate_goal_terms(project, body, now, editing=goal)
        terms = {
            key: getattr(body, key)
            for key in ("start_date", "target_date", "target_amount", "carry_in_amount")
        }
        if creating:
            created = await self.session.scalar(
                insert(BudgetGoalPeriod)
                .values(
                    id=body.id,
                    project_id=item_id,
                    book_id=book_id,
                    created_by=self.user_id,
                    updated_by=self.user_id,
                    **terms,
                )
                .on_conflict_do_nothing(index_elements=[BudgetGoalPeriod.id])
                .returning(BudgetGoalPeriod.id)
            )
            if created is None:
                existing = await self.session.get(BudgetGoalPeriod, body.id)
                if existing.book_id != book_id or existing.project_id != item_id:
                    raise ServiceError(404, "储蓄目标不存在或无权访问")
                raise ServiceError(409, "该目标 ID 已经使用，请刷新后核对")
            goal = await self.session.get(BudgetGoalPeriod, created, populate_existing=True)
        else:
            for key, value in terms.items():
                setattr(goal, key, value)
            goal.state = body.state
            goal.closed_at = now if body.state == "closed" else None
            goal.updated_by = self.user_id
            goal.version += 1
        await self.session.flush()
        await self.assign_deposits(book_id, item_id)
        project.fund_version += 1
        await self.session.flush()
        rows = await self.records(book_id, item_id)
        result = SavedBudgetGoal(
            book_id=book_id,
            item_id=item_id,
            fund_version=project.fund_version,
            goal=self.goal_response(goal, rows, now),
            balance=fund_summary(rows, now.astimezone(SHANGHAI).date()),
        )
        self.record_mutation(book_id, body.request_id, operation, digest, result)
        if book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_goal",
                goal.id,
                {"itemId": str(item_id), "fundVersion": project.fund_version},
            )
        await self.session.commit()
        return result
