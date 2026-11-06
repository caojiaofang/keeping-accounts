"""隔离本机 PostgreSQL 验证原账单、关联、派生资金与执行汇总的一致事务。"""

import asyncio
import copy
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from test_budget_funds_integration import detail, fund, goal_body, item_id
from test_budget_months_integration import setup_plan
from test_budget_plan_integration import local_api, run_case

from app.core.errors import ServiceError
from app.models.budget import (
    BudgetEntryLink,
    BudgetFundRecord,
    BudgetMonth,
    BudgetMutation,
    BudgetProject,
)
from app.models.ledger import LedgerEntry
from app.models.shared import BookActivity, BookMember
from app.schemas.budget import SetBudgetEntryLink
from app.services.budget_execution import BudgetExecutionService
from app.services.ledger import LedgerService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用本机账单预算关联测试"
    ),
]


async def ledger(api, method, path, body=None, who=0, expected=200):
    result = await api.client.request(
        method, "/api/v1" + path, json=body, headers={"Authorization": f"Bearer user-{who}"}
    )
    assert result.status_code == expected, (result.status_code, result.text)
    assert result.headers["Cache-Control"] == "no-store"
    return result.json() if expected != 204 else None


async def entry(api, amount=10000, *, book=0, who=0, when="2026-11-06", income=False):
    return await ledger(
        api,
        "POST",
        "/entries",
        {
            "id": str(uuid4()),
            "bookId": str(api.books[book]),
            "type": "income" if income else "expense",
            "categoryId": "refund" if income else "house",
            "amount": amount,
            "date": when,
            "account": "实际账单",
            "note": "原有记账流程",
        },
        who,
    )


def edit_body(saved, **changes):
    return {
        **{
            key: saved[key]
            for key in (
                "bookId",
                "type",
                "categoryId",
                "amount",
                "date",
                "account",
                "note",
                "version",
            )
        },
        **changes,
    }


def association(api, saved, version=0, index=5, **extra):
    return {
        "requestId": str(uuid4()),
        "entryVersion": saved["version"],
        "linkVersion": version,
        "itemId": item_id(api, index),
        **extra,
    }


async def link(api, saved, version=0, index=5, book=0, who=0, expected=200, **extra):
    return await api.request(
        "PUT",
        book,
        f"/entries/{saved['id']}/link",
        association(api, saved, version, index, **extra),
        who,
        expected,
    )


async def execution(api, month="2026-11", book=0, who=0):
    return await api.request("GET", book, f"/months/{month}/execution", who=who)


def test_reserve_payment_is_one_real_expense_and_unlink_restores_funds(monkeypatch):
    run_case(monkeypatch, reserve_case)


async def reserve_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, index=3, amount=52000)
        bill = await entry(api, 610000)
        await entry(api, 1560000, income=True)
        initial = await execution(api)
        assert (initial["expenseTotal"], initial["unlinkedExpenseTotal"]) == (610000, 610000)
        assert (await detail(api, index=3))["balance"]["availableAmount"] == 52000
        body = association(api, bill, index=3)
        first = await api.request("PUT", suffix=f"/entries/{bill['id']}/link", body=body)
        assert first["linkVersion"] == 1 and first["fundVersions"][0]["fundVersion"] == 2
        assert await api.request("PUT", suffix=f"/entries/{bill['id']}/link", body=body) == first
        view = await detail(api, index=3)
        assert view["balance"]["availableAmount"] == -558000
        await entry(api, 610000, income=True)
        assert (await detail(api, index=3))["balance"]["availableAmount"] == -558000
        assert view["entries"][0]["entry"]["id"] == bill["id"]
        month = await execution(api)
        assert (
            month["expenseTotal"],
            month["linkedExpenseTotal"],
            month["unlinkedExpenseTotal"],
        ) == (610000, 610000, 0)
        reserve = next(item for item in month["items"] if item["itemId"] == item_id(api, 3))
        assert (reserve["depositedAmount"], reserve["usedAmount"], reserve["remainingAmount"]) == (
            52000,
            610000,
            None,
        )
        remove = {"requestId": str(uuid4()), "entryVersion": 1, "linkVersion": 1}
        removed = await api.request("DELETE", suffix=f"/entries/{bill['id']}/link", body=remove)
        assert removed["linkVersion"] == 2 and removed["link"]["isDeleted"]
        assert (
            await api.request("DELETE", suffix=f"/entries/{bill['id']}/link", body=remove)
            == removed
        )
        assert (await detail(api, index=3))["balance"]["availableAmount"] == 52000
        await link(api, bill, index=3, expected=409)
        assert (await link(api, bill, version=2, index=3))["linkVersion"] == 3
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == 3
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(BudgetFundRecord)
                    .where(BudgetFundRecord.kind == "use", BudgetFundRecord.voided_at.is_(None))
                )
                == 1
            )
            assert await session.scalar(select(func.count()).select_from(BudgetEntryLink)) == 1


def test_real_mortgage_payment_rollover_and_closed_period_corrections(monkeypatch):
    run_case(monkeypatch, mortgage_case)


async def mortgage_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, kind="opening", amount=5040000, when="2026-11-01")
        goal = await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/goals",
            body=goal_body(1, carry=5040000, end="2026-11-05"),
        )
        goal_id = goal["goal"]["id"]
        bill = await entry(api, 5000000)
        await link(api, bill, goalPeriodId=goal_id)
        view = await detail(api)
        assert view["balance"]["availableAmount"] == 40000
        assert view["goals"][0]["forecast"]["gapAmount"] == 0
        close = {
            "requestId": str(uuid4()),
            "fundVersion": 3,
            "version": 1,
            "startDate": "2026-11-01",
            "targetDate": "2026-11-05",
            "targetAmount": 5000000,
            "carryInAmount": 5040000,
            "state": "closed",
        }
        await api.request("PUT", suffix=f"/items/{item_id(api)}/goals/{goal_id}", body=close)
        await api.request(
            "POST",
            suffix=f"/items/{item_id(api)}/goals",
            body=goal_body(4, carry=40000, start="2026-11-06", end="2027-05-05", target=2500000),
        )
        await fund(api, 5)
        view = await detail(api)
        assert view["balance"]["availableAmount"] == 460000
        assert view["goals"][0]["collectedAmount"] == 5040000
        assert view["goals"][1]["forecast"]["gapAmount"] == 2040000
        edited = await ledger(api, "PUT", f"/entries/{bill['id']}", edit_body(bill, amount=4950000))
        assert edited["version"] == 2
        view = await detail(api)
        assert view["balance"]["availableAmount"] == 510000
        assert view["goals"][0]["usedAmount"] == 4950000 and view["goals"][0]["state"] == "closed"
        await ledger(api, "DELETE", f"/entries/{bill['id']}?version=2", expected=204)
        await ledger(api, "DELETE", f"/entries/{bill['id']}?version=2", expected=204)
        assert (await detail(api))["balance"]["availableAmount"] == 5460000
        assert (await execution(api))["expenseTotal"] == 0


def test_expense_month_scope_history_and_change_to_income(monkeypatch):
    run_case(monkeypatch, expense_case)


async def expense_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        bill = await entry(api, 350000)
        await link(api, bill, index=0)
        month = await execution(api)
        assert month["items"][0]["remainingAmount"] == -16000
        future = copy.deepcopy(api.plan)
        future["items"] = future["items"][1:]
        preview = await api.preview(future, 1, "2026-12")
        await api.request("PUT", body=api.save_body(future, 1, preview["impactToken"], "2026-12"))
        clock["now"] += timedelta(days=30)
        moved = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(bill, date="2026-12-01")
        )
        assert "未安排" in moved["budgetNotice"]
        assert (await execution(api, "2026-12"))["unlinkedExpenseTotal"] == 350000
        assert (await execution(api))["expenseTotal"] == 0
        restored = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(moved, date="2026-11-06")
        )
        assert (await link(api, restored, version=2, index=0))["linkVersion"] == 3
        income = await ledger(
            api,
            "PUT",
            f"/entries/{bill['id']}",
            edit_body(restored, type="income", categoryId="refund"),
        )
        assert "改为收入" in income["budgetNotice"]
        assert (await execution(api))["expenseTotal"] == 0
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 0


def test_date_edits_clear_only_invalid_goal_and_do_not_block_normal_bills(monkeypatch):
    run_case(monkeypatch, date_case)


async def date_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, kind="opening", amount=100000, when="2026-11-01")
        goal = await api.request(
            "POST", suffix=f"/items/{item_id(api)}/goals", body=goal_body(1, start="2026-11-05")
        )
        bill = await entry(api, 50000)
        await link(api, bill, goalPeriodId=goal["goal"]["id"])
        earlier = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(bill, date="2026-11-03")
        )
        assert "重新选择目标期" in earlier["budgetNotice"]
        state = await api.request("GET", suffix=f"/entries/{bill['id']}/link")
        assert state["link"]["goalPeriodId"] is None and not state["link"]["isDeleted"]
        assert (await detail(api))["balance"]["availableAmount"] == 50000
        assert (await detail(api))["goals"][0]["usedAmount"] == 0
        future = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(earlier, date="2026-11-07")
        )
        assert "晚于" in future["budgetNotice"]
        assert (await detail(api))["balance"]["availableAmount"] == 100000
        await link(api, future, version=3, expected=422)
        early = await entry(api, when="2026-10-31")
        await link(api, early, expected=422)
        await link(api, earlier, version=3, index=3, goalPeriodId=goal["goal"]["id"], expected=409)
        # 补到合法日期后，可再次关联；原目标开始前不能强行指定目标归属。
        valid = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(future, date="2026-11-03")
        )
        await link(api, valid, version=3, goalPeriodId=goal["goal"]["id"], expected=422)
        await link(api, valid, version=3)
        income = await ledger(
            api,
            "PUT",
            f"/entries/{bill['id']}",
            edit_body(valid, type="income", categoryId="refund"),
        )
        assert "改为收入" in income["budgetNotice"]
        assert (await detail(api))["balance"]["availableAmount"] == 100000


def test_retargeting_updates_both_pools_and_does_not_make_a_second_expense(monkeypatch):
    run_case(monkeypatch, retarget_case)


async def retarget_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, index=3, amount=52000)
        await fund(api, 0, amount=420000)
        bill = await entry(api, 60000)
        first_body = association(api, bill)
        path = f"/entries/{bill['id']}/link"
        await api.request("PUT", suffix=path, body=first_body)
        assert (await detail(api))["balance"]["availableAmount"] == 360000
        second = await link(api, bill, version=1, index=3)
        assert len(second["fundVersions"]) == 2
        assert (await detail(api))["balance"]["availableAmount"] == 420000
        assert (await detail(api, index=3))["balance"]["availableAmount"] == -8000
        unchanged = await link(api, bill, version=2, index=3)
        assert unchanged["linkVersion"] == 2 and unchanged["fundVersions"] == []
        await link(api, bill, version=2, index=0)
        assert (await detail(api, index=3))["balance"]["availableAmount"] == 52000
        assert (await execution(api))["expenseTotal"] == 60000
        another = await entry(api)
        await api.request(
            "PUT", suffix=f"/entries/{another['id']}/link", body=first_body, expected=409
        )
        goal = await api.request("POST", suffix=f"/items/{item_id(api)}/goals", body=goal_body(3))
        await link(api, another, index=4, goalPeriodId=goal["goal"]["id"], expected=404)


@pytest.mark.parametrize("status", ["removed", "left"])
def test_membership_change_wins_while_link_waits_on_book_lock(monkeypatch, status):
    async def case(clock):
        async with local_api() as api:
            await setup_plan(api, book=1)
            bill = await entry(api, book=1, who=1)
            async with api.sessions() as writer, api.sessions() as reader:
                await LedgerService(writer, api.users[0]).require_book(api.books[1], write=True)
                await writer.execute(
                    update(BookMember)
                    .where(
                        BookMember.book_id == api.books[1],
                        BookMember.user_id == api.users[1],
                    )
                    .values(status=status)
                )
                pid = await reader.scalar(select(func.pg_backend_pid()))
                service = BudgetExecutionService(
                    LedgerService(reader, api.users[1]), clock=lambda: clock["now"]
                )
                task = asyncio.create_task(
                    service.change_link(
                        api.books[1],
                        UUID(bill["id"]),
                        SetBudgetEntryLink.model_validate(association(api, bill)),
                    )
                )
                blocked = False
                try:
                    async with api.sessions() as observer:
                        for _ in range(30):
                            if await observer.scalar(select(func.pg_blocking_pids(pid))):
                                blocked = True
                                break
                            await asyncio.sleep(0.02)
                finally:
                    await writer.commit()
                    with pytest.raises(ServiceError) as error:
                        await asyncio.wait_for(task, timeout=5)
                    assert error.value.status_code == 404
                assert blocked, "未观察到关联请求等待账本锁"
            async with api.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(BudgetEntryLink)) == 0
                assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 0

    run_case(monkeypatch, case)


def test_personal_book_move_preserves_tombstone_version_and_restores_source(monkeypatch):
    run_case(monkeypatch, move_case)


async def move_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, amount=100000)
        destination = uuid4()
        await ledger(api, "POST", "/books", {"id": str(destination), "name": "另一本个人账本"})
        api.books.append(destination)
        plan = copy.deepcopy(api.plan)
        for item in plan["items"]:
            item["id"] = str(uuid4())
        preview = await api.preview(plan, 0, book=3)
        await api.request("PUT", 3, body=api.save_body(plan, 0, preview["impactToken"]))
        bill = await entry(api, 10000)
        await link(api, bill)
        moved = await ledger(
            api, "PUT", f"/entries/{bill['id']}", edit_body(bill, bookId=str(destination))
        )
        assert "转移" in moved["budgetNotice"]
        assert (await detail(api))["balance"]["availableAmount"] == 100000
        await api.request("GET", suffix=f"/entries/{bill['id']}/link", expected=404)
        state = await api.request("GET", 3, f"/entries/{bill['id']}/link")
        assert state["link"] is None and state["linkVersion"] == 2
        body = association(api, moved, version=2, itemId=plan["items"][5]["id"])
        linked = await api.request("PUT", 3, f"/entries/{bill['id']}/link", body)
        assert linked["linkVersion"] == 3 and linked["link"]["bookId"] == str(destination)
        target = await api.request("GET", 3, f"/items/{plan['items'][5]['id']}")
        assert target["balance"]["availableAmount"] == -10000


def test_shared_members_can_link_own_bills_but_not_write_other_funds(monkeypatch):
    run_case(monkeypatch, permissions_case)


async def permissions_case(clock):
    async with local_api() as api:
        await setup_plan(api, book=1)
        await fund(api, 0, book=1, index=3, amount=52000)
        own = await entry(api, book=1, who=1)
        owner = await entry(api, book=1)
        body = association(api, own, index=3)
        await api.request("PUT", 1, f"/entries/{own['id']}/link", body, who=1)
        assert (await detail(api, book=1, index=3, who=1))["balance"]["availableAmount"] == 42000
        await link(api, owner, book=1, who=1, expected=403)
        await link(api, own, book=1, who=2, expected=404)
        await api.request("GET", 1, f"/entries/{owner['id']}/link", who=1)
        await api.request("GET", 0, f"/entries/{own['id']}/link", expected=404)
        income = await entry(api, book=1, who=1, income=True)
        await link(api, income, book=1, who=1, expected=422)
        await ledger(
            api,
            "PUT",
            f"/entries/{own['id']}",
            edit_body(own, bookId=str(api.books[0])),
            who=1,
            expected=404,
        )
        await ledger(
            api,
            "PUT",
            f"/entries/{owner['id']}",
            edit_body(owner, bookId=str(api.books[0])),
            expected=422,
        )
        await ledger(api, "DELETE", f"/entries/{owner['id']}?version=1", who=1, expected=403)
        async with api.sessions() as session:
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[1])
                .values(status="removed")
            )
            await session.commit()
        await api.request("PUT", 1, f"/entries/{own['id']}/link", body, who=1, expected=404)
        await api.request("GET", 1, "/months/2026-11/execution", who=1, expected=404)
        await ledger(api, "DELETE", f"/entries/{own['id']}?version=1", expected=204)
        assert (await detail(api, book=1, index=3))["balance"]["availableAmount"] == 52000


def test_concurrent_link_replay_retarget_and_delete_do_not_double_use(monkeypatch):
    run_case(monkeypatch, concurrent_case)


async def concurrent_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, amount=10000)
        bill = await entry(api)
        path = f"/entries/{bill['id']}/link"
        body = association(api, bill)
        first, second = await asyncio.gather(
            api.request("PUT", suffix=path, body=body), api.request("PUT", suffix=path, body=body)
        )
        assert first == second
        assert (await detail(api))["balance"]["availableAmount"] == 0
        url = f"/api/v1/books/{api.books[0]}/budgets" + path
        headers = {"Authorization": "Bearer user-0"}
        outcomes = await asyncio.gather(
            *[
                api.client.put(
                    url, json=association(api, bill, version=1, index=index), headers=headers
                )
                for index in (3, 4)
            ]
        )
        assert sorted(result.status_code for result in outcomes) == [200, 409]
        edit = await ledger(api, "PUT", f"/entries/{bill['id']}", edit_body(bill, amount=12000))
        state = await api.request("GET", suffix=path)
        assert state["linkVersion"] == 3 and edit["version"] == 2
        racing = await asyncio.gather(
            api.client.delete(f"/api/v1/entries/{bill['id']}?version=2", headers=headers),
            api.client.put(url, json=association(api, edit, version=3), headers=headers),
        )
        assert racing[0].status_code == 204 and racing[1].status_code in (200, 409)
        async with api.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(BudgetFundRecord)
                    .where(BudgetFundRecord.kind == "use", BudgetFundRecord.voided_at.is_(None))
                )
                == 0
            )
        assert (await execution(api))["expenseTotal"] == 0


def test_sql_failures_rollback_link_old_entry_edits_and_deletion(monkeypatch):
    async def case(clock):
        async with local_api() as api:
            await setup_plan(api, book=1)
            bill = await entry(api, book=1)
            path = f"/entries/{bill['id']}/link"
            body = association(api, bill)
            original_record = BudgetExecutionService.record_mutation

            def fail_record(*args, **kwargs):
                raise SQLAlchemyError("private-budget-test-secret")

            monkeypatch.setattr(BudgetExecutionService, "record_mutation", fail_record)
            failure = await api.request("PUT", 1, path, body, expected=503)
            assert "private-budget-test-secret" not in str(failure)
            async with api.sessions() as session:
                for model in (BudgetEntryLink, BudgetFundRecord, BudgetMonth):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
                assert (await session.get(BudgetProject, UUID(item_id(api)))).fund_version == 0
                assert (
                    await session.get(BudgetMutation, (api.books[1], UUID(body["requestId"])))
                    is None
                )
            monkeypatch.setattr(BudgetExecutionService, "record_mutation", original_record)
            await api.request("PUT", 1, path, body)
            original_sync = BudgetExecutionService.sync_entry

            async def fail_sync(*args, **kwargs):
                await original_sync(*args, **kwargs)
                raise SQLAlchemyError("private-entry-test-secret")

            monkeypatch.setattr(BudgetExecutionService, "sync_entry", fail_sync)
            await ledger(
                api, "PUT", f"/entries/{bill['id']}", edit_body(bill, amount=20000), expected=503
            )
            await ledger(api, "DELETE", f"/entries/{bill['id']}?version=1", expected=503)
            async with api.sessions() as session:
                stored = await session.get(LedgerEntry, UUID(bill["id"]))
                assert stored.version == 1 and stored.amount == 10000 and not stored.is_deleted
                stored_link = await session.get(BudgetEntryLink, UUID(bill["id"]))
                assert stored_link.version == 1 and not stored_link.is_deleted
                assert (await session.get(BudgetProject, UUID(item_id(api)))).fund_version == 1
                assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(BookActivity)
                        .where(BookActivity.action == "budget_entry_sync")
                    )
                    == 0
                )
            monkeypatch.setattr(BudgetExecutionService, "sync_entry", original_sync)
            updated = await ledger(
                api, "PUT", f"/entries/{bill['id']}", edit_body(bill, amount=20000)
            )
            assert updated["version"] == 2
            await ledger(api, "DELETE", f"/entries/{bill['id']}?version=2", expected=204)
            assert (await detail(api, book=1))["balance"]["availableAmount"] == 0

    run_case(monkeypatch, case)


def test_monthly_totals_entry_pagination_and_archived_deposits(monkeypatch):
    run_case(monkeypatch, pagination_case)


async def pagination_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await fund(api, 0, index=4, amount=200000)
        bills = [await entry(api, amount) for amount in (100, 200, 300, 400, 500)]
        for bill in bills[:3]:
            await link(api, bill, index=6)
        await entry(api, 100000, income=True)
        await entry(api, 50000, book=2, who=2)
        view = await execution(api)
        assert (view["expenseTotal"], view["linkedExpenseTotal"], view["unlinkedExpenseTotal"]) == (
            1500,
            600,
            900,
        )
        seen, before = [], None
        while True:
            suffix = "/months/2026-11/entries?unlinkedOnly=true&limit=1" + (
                "&beforeEntryId=" + before if before else ""
            )
            page = await api.request("GET", suffix=suffix)
            seen.extend(row["entry"]["id"] for row in page["entries"])
            before = page["nextCursor"]
            if not before:
                break
        assert set(seen) == {bill["id"] for bill in bills[3:]} and len(seen) == 2
        first = await detail(api, index=6, suffix="?entryLimit=2")
        second = await detail(
            api, index=6, suffix="?entryLimit=2&beforeEntryId=" + first["nextEntryCursor"]
        )
        assert (
            len(first["entries"]) == 2
            and len(second["entries"]) == 1
            and second["nextEntryCursor"] is None
        )
        await api.request(
            "GET",
            suffix=f"/months/2026-11/entries?unlinkedOnly=true&itemId={item_id(api)}",
            expected=422,
        )
        await api.request(
            "GET",
            suffix=f"/months/2026-11/entries?unlinkedOnly=true&beforeEntryId={bills[0]['id']}",
            expected=404,
        )
        future = copy.deepcopy(api.plan)
        future["items"] = [item for item in future["items"] if item["id"] != item_id(api, 4)]
        preview = await api.preview(future, 1, "2026-12")
        await api.request("PUT", body=api.save_body(future, 1, preview["impactToken"], "2026-12"))
        clock["now"] += timedelta(days=30)
        await fund(api, 1, index=4, amount=10000, when="2026-12-06")
        december = await execution(api, "2026-12")
        archived = next(item for item in december["items"] if item["itemId"] == item_id(api, 4))
        assert not archived["inPlan"] and archived["plannedAmount"] == 0
        assert (archived["depositedAmount"], archived["availableAmount"]) == (10000, 210000)
        assert (await execution(api))["expenseTotal"] == 1500
