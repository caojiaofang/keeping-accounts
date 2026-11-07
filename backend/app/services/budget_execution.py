"""账单唯一归集与派生用款；账单钩子只更新当前事务，不自行提交。"""

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select

from app.core.errors import ServiceError
from app.models.budget import BudgetEntryLink, BudgetFundRecord, BudgetGoalPeriod, BudgetProject
from app.models.ledger import LedgerEntry
from app.schemas.budget import (
    MAX_SAFE_INTEGER,
    BudgetEntryLinkResponse,
    BudgetEntryLinkState,
    BudgetEntryPage,
    BudgetExecutionDetail,
    BudgetItemExecution,
    BudgetLinkedEntry,
    BudgetMonthExecution,
    BudgetProjectVersion,
    RemoveBudgetEntryLink,
    SetBudgetEntryLink,
)
from app.services.budget import SHANGHAI, request_hash
from app.services.budget_funds import BudgetFundService, fund_summary, milliseconds, safe_amount
from app.services.ledger import entry_response


def month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(month + "-01")
    # 用闭区间避免 2100-12 的查询依赖契约范围外的下一月。
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def link_response(row: BudgetEntryLink) -> BudgetEntryLinkResponse:
    return BudgetEntryLinkResponse(
        entry_id=row.entry_id,
        book_id=row.book_id,
        item_id=row.project_id,
        goal_period_id=row.goal_period_id,
        version=row.version,
        is_deleted=row.is_deleted,
        updated_at=milliseconds(row.updated_at),
        updated_by=row.updated_by,
    )


class BudgetExecutionService(BudgetFundService):
    async def save_entry_selection(self, entry, item_id, expected_version, *, replay=False):
        """账单和预算归集在同一事务内保存；不自行提交。"""
        link = await self.link_row(entry.id)
        current = link.project_id if link and not link.is_deleted else None
        if replay:
            if current != item_id:
                raise ServiceError(409, "此次记账的预算关联已变化，请刷新后重试")
            return
        if (link.version if link else 0) != expected_version:
            raise ServiceError(409, "预算关联已被其他成员修改，请重新打开账单")
        target = None
        if item_id is not None:
            target = await self.project(entry.book_id, item_id)
            month = await self.read_month(
                entry.book_id, entry.date.strftime("%Y-%m"), snapshot_only=True
            )
            if not month.plan or not any(item.id == item_id for item in month.plan.items):
                raise ServiceError(422, "请选择账单发生月份的预算项目")
            problem = await self.target_problem(target, entry)
            if problem:
                raise ServiceError(422, problem.replace("，已取消预算关联", "，不能关联该预算项目"))
        goal_id = link.goal_period_id if current == item_id and link else None
        await self.reconcile(entry, link, target, goal_id, force_link=True)

    async def link_row(self, entry_id: UUID) -> BudgetEntryLink | None:
        return await self.session.scalar(
            select(BudgetEntryLink)
            .where(BudgetEntryLink.entry_id == entry_id)
            .execution_options(populate_existing=True)
        )

    async def scoped_entry(self, book_id: UUID, entry_id: UUID, *, write=False) -> LedgerEntry:
        await self.ledger.require_book(book_id, write=write)
        # 先检查路径归属，再使用既有账单权限；不持有一个账本锁去追锁其他账本。
        found = await self.session.scalar(
            select(LedgerEntry.id).where(LedgerEntry.id == entry_id, LedgerEntry.book_id == book_id)
        )
        if found is None:
            raise ServiceError(404, "账单不存在或无权访问")
        return await self.ledger.require_entry(entry_id, include_deleted=True, write=write)

    async def read_link(self, book_id: UUID, entry_id: UUID) -> BudgetEntryLinkState:
        entry = await self.scoped_entry(book_id, entry_id)
        if entry.is_deleted:
            raise ServiceError(404, "账单不存在或无权访问")
        return self.link_state(entry, await self.link_row(entry_id))

    @staticmethod
    def link_state(entry, row, versions=None, notice=None) -> BudgetEntryLinkState:
        return BudgetEntryLinkState(
            book_id=entry.book_id,
            entry_id=entry.id,
            entry_version=entry.version,
            link_version=row.version if row else 0,
            link=link_response(row) if row and row.book_id == entry.book_id else None,
            fund_versions=versions or [],
            notice=notice,
        )

    async def target_problem(self, project: BudgetProject, entry: LedgerEntry) -> str | None:
        if entry.is_deleted or entry.type != "expense":
            return "账单已删除或改为收入，已取消预算关联"
        if entry.date > self.now().astimezone(SHANGHAI).date():
            return "账单日期晚于北京时间今天，已取消预算关联"
        if entry.date < project.enabled_month:
            return "账单日期早于预算用途启用月，已取消预算关联"
        month = entry.date.strftime("%Y-%m")
        snapshot = await self.snapshot(project.book_id, month)
        if snapshot and snapshot.is_deleted:
            return "账单发生月预算已删除，已取消预算关联"
        if project.kind == "expense":
            source = None if snapshot else await self.source_for_month(project.book_id, month)
            plan = self.snapshot_data(snapshot)[2] if snapshot else source.plan if source else None
            if plan is None or not any(item.id == project.id for item in plan.items):
                return "账单发生月未安排该预算用途，已取消预算关联"
        else:
            opening = await self.session.scalar(
                select(BudgetFundRecord.date).where(
                    BudgetFundRecord.project_id == project.id,
                    BudgetFundRecord.book_id == project.book_id,
                    BudgetFundRecord.kind == "opening",
                    BudgetFundRecord.voided_at.is_(None),
                )
            )
            if opening and entry.date < opening:
                return "账单日期早于有效期初，已取消预算关联"
        return None

    async def goal_for_link(self, project, goal_id):
        if goal_id is None:
            return None
        if project.kind != "saving":
            raise ServiceError(422, "只有储蓄用款可以关联储蓄目标")
        goal = await self.session.scalar(
            select(BudgetGoalPeriod).where(
                BudgetGoalPeriod.id == goal_id,
                BudgetGoalPeriod.project_id == project.id,
                BudgetGoalPeriod.book_id == project.book_id,
            )
        )
        if goal is None:
            raise ServiceError(404, "储蓄目标不存在或无权访问")
        return goal

    async def reconcile(
        self,
        entry: LedgerEntry,
        link: BudgetEntryLink | None,
        target: BudgetProject | None,
        goal_id: UUID | None,
        *,
        force_link=False,
    ) -> tuple[list[BudgetProjectVersion], bool]:
        """外层已持有所涉账本排他锁并验证权限；项目按 UUID 顺序锁定。"""
        use = await self.session.scalar(
            select(BudgetFundRecord).where(
                BudgetFundRecord.entry_id == entry.id,
                BudgetFundRecord.kind == "use",
                BudgetFundRecord.voided_at.is_(None),
            )
        )
        scopes = {}
        if link and not link.is_deleted:
            scopes[link.project_id] = link.book_id
        if use:
            scopes[use.project_id] = use.book_id
        if target:
            scopes[target.id] = target.book_id
        projects = {
            item_id: await self.project(scopes[item_id], item_id, write=True)
            for item_id in sorted(scopes, key=str)
        }
        desired_use = target is not None and target.kind in {"reserve", "saving"}
        same_use = bool(
            use
            and desired_use
            and use.project_id == target.id
            and use.book_id == target.book_id
            and use.goal_period_id == goal_id
            and use.amount == entry.amount
            and use.date == entry.date
            and use.note == entry.note
        )
        use_changed = bool(use or desired_use) and not same_use
        link_changed = (
            bool(link and not link.is_deleted)
            if target is None
            else link is None
            or link.is_deleted
            or link.project_id != target.id
            or link.book_id != target.book_id
            or link.goal_period_id != goal_id
        )
        link_changed = link_changed or bool(force_link and link and not link.is_deleted)
        if not use_changed and not link_changed:
            return [], False
        if link and link.version >= MAX_SAFE_INTEGER:
            raise ServiceError(409, "预算关联版本已达到上限，暂时无法修改")
        affected = set()
        if use_changed:
            if use:
                affected.add(use.project_id)
                if use.version >= MAX_SAFE_INTEGER:
                    raise ServiceError(409, "资金记录版本已达到上限，暂时无法修改")
                use.voided_at = self.now()
                use.version += 1
                use.updated_by = self.user_id
            if desired_use:
                affected.add(target.id)
            for item_id in affected:
                if projects[item_id].fund_version >= MAX_SAFE_INTEGER:
                    raise ServiceError(409, "资金版本已达到上限，暂时无法修改")
            # 释放有效 use 的部分唯一约束，再插入替代流水。
            await self.session.flush()
            if desired_use:
                self.session.add(
                    BudgetFundRecord(
                        id=uuid4(),
                        project_id=target.id,
                        book_id=target.book_id,
                        kind="use",
                        amount=entry.amount,
                        date=entry.date,
                        note=entry.note,
                        entry_id=entry.id,
                        goal_period_id=goal_id,
                        replaces_id=use.id if use and use.project_id == target.id else None,
                        created_by=self.user_id,
                        updated_by=self.user_id,
                    )
                )
            for item_id in affected:
                projects[item_id].fund_version += 1
        if target is not None:
            if link is None:
                link = BudgetEntryLink(
                    entry_id=entry.id,
                    project_id=target.id,
                    book_id=target.book_id,
                    version=1,
                    created_by=self.user_id,
                    updated_by=self.user_id,
                )
                self.session.add(link)
            else:
                link.version += 1
                link.project_id = target.id
                link.book_id = target.book_id
                link.updated_by = self.user_id
            link.goal_period_id = goal_id
            link.is_deleted = False
            await self.freeze_execution_month(target.book_id, target.id, entry.date)
        elif link and not link.is_deleted:
            link.is_deleted = True
            link.version += 1
            link.updated_by = self.user_id
        await self.session.flush()
        if link:
            await self.session.refresh(link)
        for item_id in affected:
            fund_summary(
                await self.records(projects[item_id].book_id, item_id),
                self.now().astimezone(SHANGHAI).date(),
            )
        return [
            BudgetProjectVersion(item_id=item_id, fund_version=projects[item_id].fund_version)
            for item_id in sorted(affected, key=str)
        ], True

    async def change_link(
        self,
        book_id: UUID,
        entry_id: UUID,
        body: SetBudgetEntryLink | RemoveBudgetEntryLink,
    ) -> BudgetEntryLinkState:
        setting = isinstance(body, SetBudgetEntryLink)
        body = (SetBudgetEntryLink if setting else RemoveBudgetEntryLink).model_validate(
            body.model_dump()
        )
        entry = await self.scoped_entry(book_id, entry_id, write=True)
        digest = request_hash(
            {"entryId": str(entry_id), "body": body.model_dump(mode="json", by_alias=True)}
        )
        operation = "entry_link" if setting else "entry_unlink"
        replay = await self.replay_mutation(
            book_id, body.request_id, operation, digest, BudgetEntryLinkState
        )
        if replay is not None:
            await self.session.commit()
            return replay
        if await self.root(book_id) is None:
            raise ServiceError(409, "该账本尚未设置家庭预算")
        link = await self.link_row(entry_id)
        if (
            entry.version != body.entry_version
            or (link.version if link else 0) != body.link_version
        ):
            raise ServiceError(409, "账单或预算关联已变化，请刷新后重试")
        target = None
        goal_id = None
        if setting:
            target = await self.project(book_id, body.item_id)
            problem = await self.target_problem(target, entry)
            if problem:
                raise ServiceError(422, problem.replace("，已取消预算关联", "，不能关联该预算用途"))
            goal_id = body.goal_period_id
            goal = await self.goal_for_link(target, goal_id)
            if goal and entry.date < goal.start_date:
                raise ServiceError(422, "用款日期不能早于目标开始日")
        versions, changed = await self.reconcile(entry, link, target, goal_id)
        result = self.link_state(entry, await self.link_row(entry_id), versions)
        self.record_mutation(book_id, body.request_id, operation, digest, result)
        book = await self.ledger.require_book(book_id, write=True)
        if changed and book.kind == "shared":
            self.ledger.activity(
                book_id,
                "budget_entry_link",
                entry_id,
                {"linkVersion": result.link_version, "isDeleted": target is None},
            )
        await self.session.commit()
        return result

    async def sync_entry(self, entry: LedgerEntry, old_book_id: UUID) -> str | None:
        """由既有账单编辑／删除调用；调用者持有原／目标账本锁，负责一次提交。"""
        link = await self.link_row(entry.id)
        if link is None or link.is_deleted:
            return None
        if link.book_id != old_book_id:
            raise ServiceError(503, "预算关联归属异常，请稍后重试")
        target = await self.project(link.book_id, link.project_id)
        notice = None
        goal_id = link.goal_period_id
        if entry.book_id != old_book_id:
            notice = "账单已转移到其他账本，已取消原预算关联"
        else:
            notice = await self.target_problem(target, entry)
        if notice:
            target, goal_id = None, None
        else:
            goal = await self.goal_for_link(target, goal_id)
            if goal and entry.date < goal.start_date:
                goal_id = None
                notice = "用款日期早于原目标开始日，已保留用途关联，请重新选择目标期"
        versions, changed = await self.reconcile(entry, link, target, goal_id, force_link=True)
        if changed:
            book = await self.ledger.require_book(old_book_id, write=True)
            if book.kind == "shared":
                self.ledger.activity(
                    old_book_id,
                    "budget_entry_sync",
                    entry.id,
                    {
                        "entryVersion": entry.version,
                        "linkVersion": link.version,
                        "notice": notice,
                        "fundVersions": [
                            v.model_dump(mode="json", by_alias=True) for v in versions
                        ],
                    },
                )
        return notice

    async def entry_page(
        self,
        book_id: UUID,
        *,
        month: str | None = None,
        item_id: UUID | None = None,
        unlinked_only=False,
        before: UUID | None = None,
        limit=20,
    ) -> BudgetEntryPage:
        await self.ledger.require_book(book_id)
        if item_id:
            await self.project(book_id, item_id)
        active = and_(BudgetEntryLink.is_deleted.is_(False), BudgetEntryLink.book_id == book_id)
        statement = (
            select(LedgerEntry, BudgetEntryLink)
            .outerjoin(BudgetEntryLink, BudgetEntryLink.entry_id == LedgerEntry.id)
            .where(
                LedgerEntry.book_id == book_id,
                LedgerEntry.type == "expense",
                LedgerEntry.is_deleted.is_(False),
            )
        )
        if month:
            start, end = month_bounds(month)
            statement = statement.where(LedgerEntry.date.between(start, end))
        if item_id:
            statement = statement.where(active, BudgetEntryLink.project_id == item_id)
        if unlinked_only:
            statement = statement.where(or_(BudgetEntryLink.entry_id.is_(None), ~active))
        if before:
            pivot = (await self.session.execute(statement.where(LedgerEntry.id == before))).first()
            if pivot is None:
                raise ServiceError(404, "账单分页游标不存在或不属于当前查询")
            statement = statement.where(
                or_(
                    LedgerEntry.date < pivot[0].date,
                    and_(LedgerEntry.date == pivot[0].date, LedgerEntry.id < before),
                )
            )
        rows = (
            await self.session.execute(
                statement.order_by(LedgerEntry.date.desc(), LedgerEntry.id.desc()).limit(limit + 1)
            )
        ).all()
        return BudgetEntryPage(
            book_id=book_id,
            entries=[
                BudgetLinkedEntry(
                    entry=entry_response(entry),
                    link=link_response(link) if link and link.book_id == book_id else None,
                    link_version=link.version if link else 0,
                )
                for entry, link in rows[:limit]
            ],
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
        )

    async def execution_detail(
        self, book_id, item_id, *, before_record_id, record_limit, before_entry_id, entry_limit
    ) -> BudgetExecutionDetail:
        detail = await self.detail(
            book_id, item_id, before_record_id=before_record_id, record_limit=record_limit
        )
        page = await self.entry_page(
            book_id, item_id=item_id, before=before_entry_id, limit=entry_limit
        )
        return BudgetExecutionDetail(
            **detail.model_dump(), entries=page.entries, next_entry_cursor=page.next_cursor
        )

    async def month_execution(
        self, book_id: UUID, month: str, *, snapshot_only=False
    ) -> BudgetMonthExecution:
        budget = await self.read_month(book_id, month, snapshot_only=snapshot_only)
        start, end = month_bounds(month)
        totals = (
            await self.session.execute(
                select(BudgetEntryLink.project_id, func.sum(LedgerEntry.amount))
                .select_from(LedgerEntry)
                .outerjoin(
                    BudgetEntryLink,
                    and_(
                        BudgetEntryLink.entry_id == LedgerEntry.id,
                        BudgetEntryLink.book_id == book_id,
                        BudgetEntryLink.is_deleted.is_(False),
                    ),
                )
                .where(
                    LedgerEntry.book_id == book_id,
                    LedgerEntry.type == "expense",
                    LedgerEntry.is_deleted.is_(False),
                    LedgerEntry.date.between(start, end),
                )
                .group_by(BudgetEntryLink.project_id)
            )
        ).all()
        expenses = {item_id: int(amount) for item_id, amount in totals}
        planned = {item.id: item for item in budget.plan.items} if budget.plan else {}
        now = self.now()
        today = now.astimezone(SHANGHAI).date()
        executed = set(
            await self.session.scalars(
                select(BudgetFundRecord.project_id)
                .where(
                    BudgetFundRecord.book_id == book_id,
                    BudgetFundRecord.voided_at.is_(None),
                    BudgetFundRecord.kind.in_(["deposit", "use"]),
                    BudgetFundRecord.date.between(start, end),
                    BudgetFundRecord.date <= today,
                )
                .distinct()
            )
        )
        identifiers = list(planned) + sorted(
            (set(expenses) - {None} | executed) - set(planned), key=str
        )
        active = await self.active_project_ids(book_id, now)
        items = []
        for item_id in identifiers:
            project = await self.project(book_id, item_id)
            item = planned.get(item_id)
            if item is None:
                item = (await self.project_response(project, active)).configuration
            records = await self.records(book_id, item_id)
            monthly = [row for row in records if start <= row.date <= end and row.date <= today]
            deposited = sum(row.amount for row in monthly if row.kind == "deposit")
            used = sum(row.amount for row in monthly if row.kind == "use")
            amount = planned[item_id].monthly_amount if item_id in planned else 0
            spent = expenses.get(item_id, 0)
            items.append(
                BudgetItemExecution(
                    item_id=item_id,
                    kind=project.kind,
                    name=item.name,
                    group_id=item.group_id,
                    in_plan=item_id in planned,
                    planned_amount=amount,
                    expense_amount=safe_amount(spent),
                    remaining_amount=safe_amount(amount - spent)
                    if project.kind == "expense"
                    else None,
                    deposited_amount=safe_amount(deposited),
                    used_amount=safe_amount(used),
                    available_amount=fund_summary(records, today).available_amount
                    if project.kind != "expense"
                    else None,
                    fund_version=project.fund_version,
                )
            )
        total = safe_amount(sum(expenses.values()))
        unlinked = expenses.get(None, 0)
        return BudgetMonthExecution(
            book_id=book_id,
            month=month,
            generated_at=milliseconds(now),
            budget=budget,
            expense_total=total,
            linked_expense_total=safe_amount(total - unlinked),
            unlinked_expense_total=safe_amount(unlinked),
            items=items,
        )
