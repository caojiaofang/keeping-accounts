"""本机真实 PostgreSQL 的用途资金、目标、审计、并发及原子回滚验证。"""

import asyncio
import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from test_budget_months_integration import setup_plan
from test_budget_plan_integration import local_api, run_case

from app.models.budget import (
    BudgetFundRecord,
    BudgetGoalPeriod,
    BudgetMonth,
    BudgetMutation,
    BudgetProject,
)
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookActivity, BookMember
from app.services.budget_funds import BudgetFundService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用本机资金与目标测试",
    ),
]


def item_id(api, index=5):
    return api.plan["items"][index]["id"]


async def detail(api, index=5, book=0, who=0, suffix="", expected=200):
    return await api.request(
        "GET", book, f"/items/{item_id(api, index)}" + suffix, who=who, expected=expected
    )


def fund_body(version, *, kind="deposit", amount=420000, when="2026-11-06"):
    return {
        "requestId": str(uuid4()),
        "fundVersion": version,
        "id": str(uuid4()),
        "kind": kind,
        "amount": amount,
        "date": when,
        "note": "实际存入",
    }


async def fund(api, version, *, index=5, book=0, **kwargs):
    return await api.request(
        "POST", book, f"/items/{item_id(api, index)}/funds", fund_body(version, **kwargs)
    )


def goal_body(version, *, carry=0, start="2026-11-01", end="2027-10-31", target=5000000):
    return {
        "requestId": str(uuid4()),
        "fundVersion": version,
        "id": str(uuid4()),
        "startDate": start,
        "targetDate": end,
        "targetAmount": target,
        "carryInAmount": carry,
    }


async def seed_use(api, goal_id=None, amount=5000000, version=0):
    entry = uuid4()
    async with api.sessions() as session:
        await session.execute(
            insert(LedgerEntry).values(
                id=entry,
                book_id=api.books[0],
                created_by=api.users[0],
                type="expense",
                category_id="house",
                amount=amount,
                date=date(2026, 11, 6),
                account="测试用途",
                note="验证既有用款计算",
            )
        )
        await session.execute(
            insert(BudgetFundRecord).values(
                id=uuid4(),
                project_id=UUID(item_id(api)),
                book_id=api.books[0],
                kind="use",
                amount=amount,
                date=date(2026, 11, 6),
                entry_id=entry,
                goal_period_id=UUID(goal_id) if goal_id else None,
                created_by=api.users[0],
                updated_by=api.users[0],
            )
        )
        await session.execute(
            update(BudgetProject)
            .where(BudgetProject.id == UUID(item_id(api)))
            .values(fund_version=version + 1)
        )
        await session.commit()


def test_records_do_not_grow_with_months_and_archived_projects_remain_discoverable(monkeypatch):
    run_case(monkeypatch, discovery_case)


async def discovery_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        first = await detail(api)
        assert first["balance"]["availableAmount"] == 0 and first["item"]["fundVersion"] == 0
        clock["now"] += timedelta(days=40)
        assert (await detail(api))["balance"]["availableAmount"] == 0
        seen, cursor = [], None
        while True:
            page = await api.request(
                "GET", suffix="/items?limit=5" + ("&after=" + cursor if cursor else "")
            )
            seen.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        assert len(set(seen)) == len(seen) == 14
        saved = await fund(api, 0, amount=10000)
        assert saved["balance"]["availableAmount"] == 10000
        for when in ("2026-10-31", "2027-01-01"):
            await api.request(
                "POST",
                suffix=f"/items/{item_id(api)}/funds",
                body=fund_body(1, when=when),
                expected=422,
            )
        await api.request(
            "POST", suffix=f"/items/{item_id(api, 0)}/funds", body=fund_body(0), expected=422
        )
        empty = {"plannedIncome": 1560000, "groups": [], "items": []}
        preview = await api.preview(empty, 1, "2026-12")
        await api.request("PUT", body=api.save_body(empty, 1, preview["impactToken"], "2026-12"))
        archived = await detail(api)
        assert (
            archived["item"]["status"] == "archived"
            and archived["balance"]["availableAmount"] == 10000
        )
        assert archived["item"]["configuration"]["name"] == "提前还贷"
        await api.request(
            "POST", suffix=f"/items/{item_id(api)}/goals", body=goal_body(1), expected=409
        )
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == 0
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 1


def test_fund_corrections_keep_audit_and_versions_without_double_count(monkeypatch):
    run_case(monkeypatch, corrections_case)


async def corrections_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        opening = await fund(api, 0, kind="opening", amount=5040000, when="2026-11-01")
        saved = await fund(api, 1)
        assert saved["balance"]["availableAmount"] == 5460000
        await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/funds",
            body=fund_body(2, kind="opening"),
            expected=409,
        )
        path = f"/items/{item_id(api)}/funds/{saved['record']['id']}/correct"
        body = {
            "requestId": str(uuid4()),
            "fundVersion": 2,
            "recordVersion": saved["record"]["version"],
            "replacement": {
                "id": str(uuid4()),
                "amount": 400000,
                "date": "2026-11-05",
                "note": "金额核对",
            },
        }
        corrected = await api.request("POST", suffix=path, body=body)
        assert corrected["balance"]["availableAmount"] == 5440000
        assert corrected["record"]["voidedAt"] is not None
        assert corrected["replacement"]["replacesId"] == saved["record"]["id"]
        assert await api.request("POST", suffix=path, body=body) == corrected
        opening_path = f"/items/{item_id(api)}/funds/{opening['record']['id']}/correct"
        await api.request(
            "POST",
            suffix=opening_path,
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "recordVersion": opening["record"]["version"],
                "replacement": None,
            },
            expected=409,
        )
        await api.request(
            "POST",
            suffix=opening_path,
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "recordVersion": opening["record"]["version"],
                "replacement": {"id": str(uuid4()), "amount": 1, "date": "2026-11-06"},
            },
            expected=409,
        )
        ids, cursor = [], None
        while True:
            page = await detail(
                api, suffix="?recordLimit=1" + ("&beforeRecordId=" + cursor if cursor else "")
            )
            ids.extend(row["id"] for row in page["records"])
            cursor = page["nextRecordCursor"]
            if cursor is None:
                break
        assert len(ids) == len(set(ids)) == 3
        voided = await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/funds/{corrected['replacement']['id']}/correct",
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "recordVersion": corrected["replacement"]["version"],
                "replacement": None,
            },
        )
        assert voided["fundVersion"] == 4 and voided["balance"]["availableAmount"] == 5040000


def test_goal_prepayment_surplus_rollover_and_closed_history(monkeypatch):
    run_case(monkeypatch, rollover_case)


async def rollover_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, kind="opening", amount=5040000)
        body = goal_body(1, carry=5040000)
        created = await api.request("POST", suffix=f"/items/{item_id(api)}/goals", body=body)
        goal = created["goal"]
        assert (
            goal["forecast"]["fundedAmount"] == 5040000
            and created["balance"]["availableAmount"] == 5040000
        )
        assert (
            await api.request("POST", suffix=f"/items/{item_id(api)}/goals", body=body) == created
        )
        await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/goals",
            body=goal_body(2, start="2027-11-01", end="2028-04-30"),
            expected=409,
        )
        # 第 06 步才开放账单关联，此处只植入带真实账单外键的合法用款验证聚合。
        await seed_use(api, goal["id"], version=2)
        current = await detail(api)
        assert current["balance"]["availableAmount"] == 40000
        assert current["goals"][0]["usedAmount"] == 5000000
        assert current["goals"][0]["forecast"]["gapAmount"] == 0
        closing = {
            key: body[key] for key in ("startDate", "targetDate", "targetAmount", "carryInAmount")
        }
        closing.update(requestId=str(uuid4()), fundVersion=3, version=1, state="closed")
        await api.request(
            "PUT", suffix=f"/items/{item_id(api)}/goals/{goal['id']}", body=closing, expected=422
        )
        closing["targetDate"] = "2026-11-06"
        closed = await api.request(
            "PUT", suffix=f"/items/{item_id(api)}/goals/{goal['id']}", body=closing
        )
        assert closed["goal"]["state"] == "closed" and closed["fundVersion"] == 4
        clock["now"] += timedelta(days=1)
        next_goal = goal_body(4, carry=40001, start="2026-11-07", end="2027-05-06", target=2500000)
        await api.request(
            "POST", suffix=f"/items/{item_id(api)}/goals", body=next_goal, expected=409
        )
        next_goal["carryInAmount"] = 40000
        next_goal["requestId"] = str(uuid4())
        renewed = await api.request("POST", suffix=f"/items/{item_id(api)}/goals", body=next_goal)
        assert (
            renewed["balance"]["availableAmount"] == 40000
            and renewed["goal"]["collectedAmount"] == 40000
        )
        await fund(api, 5, when="2026-11-07")
        current = await detail(api)
        assert current["balance"]["availableAmount"] == 460000
        assert current["goals"][0]["collectedAmount"] == 5040000
        assert current["goals"][0]["availableAmount"] == 40000
        assert current["goals"][1]["collectedAmount"] == 460000
        assert current["goals"][1]["forecast"]["gapAmount"] == 2040000
        await api.request(
            "PUT",
            suffix=f"/items/{item_id(api)}/goals/{goal['id']}",
            body={**closing, "requestId": str(uuid4()), "fundVersion": 6, "version": 2},
            expected=409,
        )


def test_existing_deposits_are_attributed_once_and_goal_date_edits_respect_records(monkeypatch):
    run_case(monkeypatch, attribution_case)


async def attribution_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        first = await fund(api, 0, amount=100000, when="2026-11-02")
        await fund(api, 1, amount=200000)
        await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/goals",
            body=goal_body(2, carry=100000),
            expected=409,
        )
        created = await api.request(
            "POST", suffix=f"/items/{item_id(api)}/goals", body=goal_body(2)
        )
        assert created["goal"]["collectedAmount"] == 300000
        assert created["balance"]["availableAmount"] == 300000
        current = await detail(api)
        assert {row["goalPeriodId"] for row in current["records"]} == {created["goal"]["id"]}
        await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/funds/{first['record']['id']}/correct",
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "recordVersion": first["record"]["version"],
                "replacement": None,
            },
            expected=409,
        )
        goal = created["goal"]
        await api.request(
            "PUT",
            suffix=f"/items/{item_id(api)}/goals/{goal['id']}",
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "version": 1,
                "startDate": "2026-11-03",
                "targetDate": goal["targetDate"],
                "targetAmount": goal["targetAmount"],
                "carryInAmount": 0,
                "state": "active",
            },
            expected=409,
        )
        await detail(
            api, suffix=f"/funding-preview?goalId={goal['id']}&availableAmount=999999", expected=422
        )
        forecast = await detail(api, suffix=f"/funding-preview?goalId={goal['id']}")
        assert forecast["gapAmount"] == 4700000


def test_deficit_is_visible_and_closed_goal_receipts_can_be_corrected(monkeypatch):
    run_case(monkeypatch, deficit_case)


async def deficit_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await seed_use(api, amount=50000)
        assert (await detail(api))["balance"]["availableAmount"] == -50000
        created = await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/goals",
            body=goal_body(1, start="2026-11-06", target=100000),
        )
        assert created["goal"]["forecast"]["gapAmount"] == 150000
        deposit = await fund(api, 2, amount=150000)
        assert deposit["balance"]["availableAmount"] == 100000
        goal = created["goal"]
        closed = await api.request(
            "PUT",
            suffix=f"/items/{item_id(api)}/goals/{goal['id']}",
            body={
                "requestId": str(uuid4()),
                "fundVersion": 3,
                "version": 1,
                "startDate": goal["startDate"],
                "targetDate": "2026-11-06",
                "targetAmount": goal["targetAmount"],
                "carryInAmount": 0,
                "state": "closed",
            },
        )
        assert closed["goal"]["availableAmount"] == 100000
        corrected = await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/funds/{deposit['record']['id']}/correct",
            body={
                "requestId": str(uuid4()),
                "fundVersion": 4,
                "recordVersion": deposit["record"]["version"],
                "replacement": {"id": str(uuid4()), "amount": 100000, "date": "2026-11-06"},
            },
        )
        assert corrected["balance"]["availableAmount"] == 50000
        current = await detail(api)
        assert (
            current["goals"][0]["state"] == "closed"
            and current["goals"][0]["targetAmount"] == 100000
        )
        assert current["goals"][0]["forecast"]["gapAmount"] == 50000


def test_fund_permissions_concurrency_and_transferred_owner_replay(monkeypatch):
    run_case(monkeypatch, permissions_case)


async def permissions_case(clock):
    async with local_api() as api:
        await setup_plan(api, book=1)
        assert (await detail(api, book=1, who=1))["balance"]["availableAmount"] == 0
        await detail(api, book=1, who=2, expected=404)
        path = f"/items/{item_id(api)}/funds"
        body = fund_body(0, amount=10000)
        await api.request("POST", 1, path, body, who=1, expected=403)
        values = await asyncio.gather(*[api.request("POST", 1, path, body) for _ in range(2)])
        assert values[0] == values[1]
        assert values[0]["fundVersion"] == 1
        candidates = [fund_body(1, amount=amount) for amount in (100, 200)]
        values = await asyncio.gather(
            *[
                api.client.post(
                    f"/api/v1/books/{api.books[1]}/budgets{path}",
                    json=value,
                    headers={"Authorization": "Bearer user-0"},
                )
                for value in candidates
            ]
        )
        assert sorted(value.status_code for value in values) == [200, 409]
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(BookActivity)
                    .where(BookActivity.action == "budget_fund")
                )
                == 2
            )
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[0])
                .values(role="member")
            )
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[1])
                .values(role="owner")
            )
            await session.execute(
                update(Book).where(Book.id == api.books[1]).values(owner_id=api.users[1])
            )
            await session.commit()
        await api.request("POST", 1, path, body, expected=403)


def test_fund_and_goal_failure_roll_back_records_snapshots_and_attribution(monkeypatch):
    run_case(monkeypatch, lambda clock: rollback_case(clock, monkeypatch))


async def rollback_case(clock, monkeypatch):
    async with local_api() as api:
        await setup_plan(api)
        body = fund_body(0, amount=10000)
        original = BudgetFundService.freeze_execution_month

        async def fail_after_snapshot(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            raise SQLAlchemyError("private-fund-fault")

        with monkeypatch.context() as fault:
            fault.setattr(BudgetFundService, "freeze_execution_month", fail_after_snapshot)
            value = await api.request(
                "POST", suffix=f"/items/{item_id(api)}/funds", body=body, expected=503
            )
            assert "private-fund-fault" not in str(value)
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 0
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 0
            assert await session.scalar(select(func.count()).select_from(BudgetMutation)) == 1
            assert (await session.get(BudgetProject, UUID(item_id(api)))).fund_version == 0
        saved = await api.request("POST", suffix=f"/items/{item_id(api)}/funds", body=body)
        target = goal_body(1)
        assign = BudgetFundService.assign_deposits

        async def fail_after_assignment(self, *args, **kwargs):
            await assign(self, *args, **kwargs)
            raise SQLAlchemyError("private-goal-fault")

        with monkeypatch.context() as fault:
            fault.setattr(BudgetFundService, "assign_deposits", fail_after_assignment)
            await api.request(
                "POST", suffix=f"/items/{item_id(api)}/goals", body=target, expected=503
            )
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetGoalPeriod)) == 0
            row = await session.get(BudgetFundRecord, UUID(saved["record"]["id"]))
            assert row.goal_period_id is None and row.version == saved["record"]["version"]
        value = await api.request("POST", suffix=f"/items/{item_id(api)}/goals", body=target)
        assert (
            value["goal"]["collectedAmount"] == 10000
            and value["balance"]["availableAmount"] == 10000
        )
