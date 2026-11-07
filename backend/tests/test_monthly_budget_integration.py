"""独立月预算、家庭成员协作与记账原子关联的隔离 PostgreSQL 回归。"""

import asyncio
import copy
import os
from uuid import uuid4

import pytest
from sqlalchemy import event, update
from test_budget_execution_integration import edit_body, ledger
from test_budget_plan_integration import local_api, run_case

from app.core.errors import ServiceError
from app.models.shared import BookMember
from app.services.budget_months import BudgetMonthService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用隔离月预算测试"
    ),
]


def plan_for(api):
    plan = copy.deepcopy(api.plan)
    plan["items"] = [plan["items"][0]]
    plan["items"][0]["monthlyAmount"] = 10000
    return plan


def monthly_body(plan, version=0):
    return {"requestId": str(uuid4()), "version": version, "plan": plan}


def bill_body(api, item, **changes):
    return {
        "id": str(uuid4()),
        "bookId": str(api.books[1]),
        "type": "expense",
        "categoryId": "house",
        "amount": 12000,
        "date": "2026-11-06",
        "account": "微信",
        "budgetItemId": item,
        "budgetLinkVersion": 0,
        **changes,
    }


def test_monthly_member_edit_copy_and_conflicts(monkeypatch):
    run_case(monkeypatch, member_case)


async def member_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        body = monthly_body(plan)
        saved = await api.request("PUT", 1, "/monthly/2026-11", body, who=1)
        assert saved["version"] == 1
        assert await api.request("PUT", 1, "/monthly/2026-11", body, who=1) == saved
        assert (await api.request("GET", 1, "/monthly/2026-12"))["state"] == "not_set"
        assert (await api.request("GET", 1, "/months/2026-12"))["state"] == "not_set"
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan), expected=409)
        changed = copy.deepcopy(plan)
        changed["items"][0]["monthlyAmount"] = 20000
        changed["items"].append({**changed["items"][0], "id": str(uuid4()), "name": "交通"})
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(changed, 1), who=1)
        await api.request("PUT", 1, "/monthly/2026-12", monthly_body(changed), who=1)
        await api.request(
            "PUT", 1, "/monthly/2026-12", monthly_body(changed, 1), who=2, expected=404
        )
        await api.request("PUT", 1, "/monthly/2026-10", monthly_body(plan), expected=422)
        assert (await api.request("GET", 1, "/monthly/2026-11"))["version"] == 2


def test_entry_budget_atomic_save_recalculate_and_delete(monkeypatch):
    run_case(monkeypatch, entry_case)


async def entry_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        item = plan["items"][0]["id"]
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan), who=1)
        body = bill_body(api, item)
        saved = await ledger(api, "POST", "/entries", body, who=1)
        assert (await ledger(api, "POST", "/entries", body, who=1))["id"] == saved["id"]
        execution = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert execution["expenseTotal"] == execution["linkedExpenseTotal"] == 12000
        assert execution["items"][0]["remainingAmount"] == -2000
        await ledger(
            api,
            "PUT",
            f"/entries/{saved['id']}",
            edit_body(saved, amount=5000, budgetItemId=item, budgetLinkVersion=0),
            who=1,
            expected=409,
        )
        execution = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert execution["expenseTotal"] == 12000  # 关联冲突时账单金额也回滚。
        saved = await ledger(
            api,
            "PUT",
            f"/entries/{saved['id']}",
            edit_body(saved, amount=5000, budgetItemId=item, budgetLinkVersion=1),
            who=1,
        )
        execution = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert execution["items"][0]["remainingAmount"] == 5000
        await api.request(
            "PUT", 1, "/monthly/2026-11", monthly_body({**plan, "items": []}, 1), who=1
        )
        unlinked = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert unlinked["expenseTotal"] == unlinked["unlinkedExpenseTotal"] == 5000
        assert unlinked["linkedExpenseTotal"] == 0
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan, 2), who=1)
        await api.request("PUT", 1, "/monthly/2026-12", monthly_body(plan))
        december = await api.request("GET", 1, "/monthly/2026-12/execution")
        assert december["expenseTotal"] == 0 and december["items"][0]["remainingAmount"] == 10000
        await ledger(
            api, "DELETE", f"/entries/{saved['id']}?version={saved['version']}", who=0, expected=204
        )
        assert (await api.request("GET", 1, "/monthly/2026-11/execution"))["items"][0][
            "remainingAmount"
        ] == 10000


def test_invalid_month_budget_rolls_back_new_bill(monkeypatch):
    run_case(monkeypatch, invalid_case)


async def invalid_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        item = plan["items"][0]["id"]
        await api.request("PUT", 1, "/monthly/2026-12", monthly_body(plan))
        await ledger(api, "POST", "/entries", bill_body(api, item), expected=422)
        data = await ledger(api, "GET", "/ledger")
        assert data["entries"] == []
        await ledger(
            api, "POST", "/entries", bill_body(api, item, bookId=str(api.books[2])), expected=404
        )


def test_family_concurrent_edits_preserve_one_version(monkeypatch):
    run_case(monkeypatch, concurrent_case)


async def concurrent_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan))

        async def change(who, amount):
            changed = copy.deepcopy(plan)
            changed["items"][0]["monthlyAmount"] = amount
            return await api.client.put(
                f"/api/v1/books/{api.books[1]}/budgets/monthly/2026-11",
                json=monthly_body(changed, 1),
                headers={"Authorization": f"Bearer user-{who}"},
            )

        results = await asyncio.gather(change(0, 20000), change(1, 30000))
        assert sorted(r.status_code for r in results) == [200, 409]
        value = await api.request("GET", 1, "/monthly/2026-11")
        assert value["version"] == 2
        assert value["plan"]["items"][0]["monthlyAmount"] in (20000, 30000)


def test_member_delete_recreate_preserves_bills_months_and_versions(monkeypatch):
    run_case(monkeypatch, deletion_case)


async def deletion_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        item = plan["items"][0]["id"]
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan))
        await api.request("PUT", 1, "/monthly/2026-12", monthly_body(plan))
        saved = await ledger(api, "POST", "/entries", bill_body(api, item), who=0)
        body = {"requestId": str(uuid4()), "version": 1}
        await api.request("DELETE", 1, "/monthly/2026-11", body, who=2, expected=404)
        deleted = await api.request("DELETE", 1, "/monthly/2026-11", body, who=1)
        assert deleted["state"] == "not_set" and deleted["version"] == 2
        assert await api.request("DELETE", 1, "/monthly/2026-11", body, who=1) == deleted
        execution = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert execution["expenseTotal"] == execution["unlinkedExpenseTotal"] == 12000
        assert execution["linkedExpenseTotal"] == 0 and execution["budget"]["plan"] is None
        assert (await ledger(api, "GET", "/ledger"))["entries"][0]["id"] == saved["id"]
        assert (await api.request("GET", 1, "/monthly/2026-12"))["state"] == "confirmed"
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan), who=1, expected=409)
        restored = await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan, 2), who=1)
        assert restored["version"] == 3
        await api.request(
            "DELETE",
            1,
            "/monthly/2026-11",
            {**body, "requestId": str(uuid4())},
            who=0,
            expected=409,
        )
        assert await api.request("DELETE", 1, "/monthly/2026-11", body, who=1) == deleted
        assert (await api.request("GET", 1, "/monthly/2026-11"))["version"] == 3
        for status in ("left", "removed"):
            async with api.sessions() as session:
                await session.execute(
                    update(BookMember)
                    .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[1])
                    .values(status=status)
                )
                await session.commit()
            await api.request("DELETE", 1, "/monthly/2026-11", body, who=1, expected=404)
        assert (
            await api.request(
                "DELETE", 1, "/monthly/2026-11", {"requestId": str(uuid4()), "version": 3}, who=0
            )
        )["state"] == "not_set"


def test_budget_deletion_rolls_back_links_and_month_on_failure(monkeypatch):
    original = BudgetMonthService.record_mutation

    def fail_delete(self, book_id, request_id, operation, digest, result):
        if operation == "monthly_delete":
            raise ServiceError(503, "模拟删除保存失败")
        return original(self, book_id, request_id, operation, digest, result)

    monkeypatch.setattr(BudgetMonthService, "record_mutation", fail_delete)
    run_case(monkeypatch, rollback_deletion_case)


async def rollback_deletion_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan))
        await ledger(api, "POST", "/entries", bill_body(api, plan["items"][0]["id"]))
        await api.request(
            "DELETE",
            1,
            "/monthly/2026-11",
            {"requestId": str(uuid4()), "version": 1},
            who=1,
            expected=503,
        )
        value = await api.request("GET", 1, "/monthly/2026-11/execution")
        assert value["budget"]["state"] == "confirmed" and value["budget"]["version"] == 1
        assert value["linkedExpenseTotal"] == 12000


def test_member_removes_reserve_item_without_deleting_bill_or_funds(monkeypatch):
    run_case(monkeypatch, reserve_deletion_case)


async def reserve_deletion_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        reserve = copy.deepcopy(api.plan["items"][3])
        plan["items"].append(reserve)
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan))
        await api.request(
            "POST",
            1,
            f"/items/{reserve['id']}/funds",
            {
                "requestId": str(uuid4()),
                "id": str(uuid4()),
                "fundVersion": 0,
                "kind": "opening",
                "amount": 100000,
                "date": "2026-11-01",
            },
        )
        await ledger(api, "POST", "/entries", bill_body(api, reserve["id"]))
        assert (await api.request("GET", 1, f"/items/{reserve['id']}"))["balance"][
            "availableAmount"
        ] == 88000
        plan["items"] = plan["items"][:1]
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan, 1), who=1)
        assert (await api.request("GET", 1, f"/items/{reserve['id']}"))["balance"][
            "availableAmount"
        ] == 100000
        assert (await api.request("GET", 1, "/monthly/2026-11/execution"))[
            "unlinkedExpenseTotal"
        ] == 12000


def test_summary_matches_execution_without_reading_project_history(monkeypatch):
    run_case(monkeypatch, summary_case)


async def summary_case(clock):
    async with local_api() as api:
        path = "/monthly/2026-11/summary"
        empty = await api.request("GET", 1, path, who=1)
        assert empty["state"] == "not_set" and empty["budgetAmount"] is None
        plan = plan_for(api)
        await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan), who=1)
        await ledger(api, "POST", "/entries", bill_body(api, plan["items"][0]["id"]), who=1)
        await ledger(api, "POST", "/entries", bill_body(api, None, amount=5000), who=1)
        await ledger(
            api,
            "POST",
            "/entries",
            bill_body(api, None, type="income", categoryId="refund", amount=90000),
            who=1,
        )
        removed = await ledger(api, "POST", "/entries", bill_body(api, None, amount=7000), who=1)
        await ledger(api, "DELETE", f"/entries/{removed['id']}?version=1", who=1, expected=204)
        value = await api.request("GET", 1, path, who=1)
        assert value["budgetAmount"] == 10000 and value["expenseAmount"] == 17000
        assert value["remainingAmount"] == 0 and value["overAmount"] == 7000
        assert (await api.request("GET", 1, "/monthly/2026-12/summary", who=1))[
            "expenseAmount"
        ] == 0
        await api.request("GET", 1, path, who=2, expected=404)
        await api.request("GET", 1, path, who=None, expected=401)
        await api.request(
            "DELETE", 1, "/monthly/2026-11", {"requestId": str(uuid4()), "version": 1}, who=1
        )
        deleted = await api.request("GET", 1, path, who=1)
        assert deleted["state"] == "not_set" and deleted["budgetAmount"] is None
        assert deleted["expenseAmount"] == 17000 and deleted["remainingAmount"] is None
        assert deleted["overAmount"] is None and deleted["version"] == 2


def test_summary_query_count_does_not_grow_with_budget_items(monkeypatch):
    run_case(monkeypatch, summary_cost_case)


async def summary_cost_case(clock):
    async with local_api() as api:
        plan = plan_for(api)
        engine = api.sessions.kw["bind"].sync_engine
        statements = []

        def capture(conn, cursor, statement, parameters, context, many):
            statements.append(statement)

        counts = []
        for size in (1, 100):
            plan["items"] = [
                {**plan["items"][0], "id": str(uuid4()), "name": f"项目{i}"} for i in range(size)
            ]
            await api.request("PUT", 1, "/monthly/2026-11", monthly_body(plan, len(counts)), who=1)
            event.listen(engine, "before_cursor_execute", capture)
            try:
                statements.clear()
                value = await api.request("GET", 1, "/monthly/2026-11/summary", who=1)
                counts.append(len(statements))
                assert value["budgetAmount"] == size * 10000
                assert not any(
                    name in " ".join(statements).lower()
                    for name in (
                        "budget_fund_records",
                        "budget_projects",
                        "budget_revisions",
                        "budget_entry_links",
                    )
                )
            finally:
                event.remove(engine, "before_cursor_execute", capture)
        assert counts == [4, 4]
