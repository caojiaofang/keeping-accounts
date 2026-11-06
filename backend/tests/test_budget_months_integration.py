"""本机 PostgreSQL 验证月快照、覆盖、精确均摊、回滚和并发；令牌解析模拟。"""

import asyncio
import copy
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from test_budget_plan_integration import local_api, run_case

from app.models.budget import (
    BudgetFundRecord,
    BudgetMonth,
    BudgetMutation,
    BudgetPlan,
    BudgetPreview,
)
from app.models.shared import BookActivity
from app.schemas.budget import MAX_SAFE_INTEGER
from app.services.budget_months import BudgetMonthService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用本机月预算测试",
    ),
]


async def setup_plan(api, book=0, month="2026-11"):
    preview = await api.preview(api.plan, 0, month, book)
    return await api.request(
        "PUT", book, body=api.save_body(api.plan, 0, preview["impactToken"], month)
    )


async def read_month(api, month, book=0, who=0, expected=200):
    return await api.request("GET", book, f"/months/{month}", who=who, expected=expected)


def confirmation(value):
    return {
        "requestId": str(uuid4()),
        "sourceRevisionId": value["sourceRevisionId"],
        "planVersion": value["planVersion"],
    }


async def confirm(api, month, book=0):
    value = await read_month(api, month, book)
    return await api.request("POST", book, f"/months/{month}/confirm", confirmation(value))


def equalization(api, version=1, start="2026-11", end="2027-10", total=610000):
    return {
        "version": version,
        "itemId": api.plan["items"][3]["id"],
        "totalAmount": total,
        "startMonth": start,
        "endMonth": end,
    }


async def batch_body(api, body, book=0):
    preview = await api.request("POST", book, "/equalizations/preview", body)
    return {**body, "requestId": str(uuid4()), "impactToken": preview["impactToken"]}, preview


def test_month_states_history_and_future_versions(monkeypatch):
    run_case(monkeypatch, states_case)


async def states_case(clock):
    async with local_api() as api:
        assert (await read_month(api, "2026-11"))["state"] == "not_set"
        saved = await setup_plan(api)
        assert (await read_month(api, "2026-10"))["state"] == "not_set"
        december = await read_month(api, "2026-12")
        assert december["state"] == "preview" and december["snapshotId"] is None
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 0
        frozen = await confirm(api, "2026-12")
        body = confirmation(frozen)
        assert (await api.request("POST", suffix="/months/2026-12/confirm", body=body))[
            "version"
        ] == 1
        changed = copy.deepcopy(api.plan)
        changed["items"][1]["monthlyAmount"] = 120000
        preview = await api.preview(changed, 1, "2027-01")
        await api.request("PUT", body=api.save_body(changed, 1, preview["impactToken"], "2027-01"))
        assert (await read_month(api, "2027-01"))["plan"]["items"][1]["monthlyAmount"] == 120000
        assert (await read_month(api, "2026-12"))["plan"] == frozen["plan"]
        await api.request(
            "POST",
            suffix="/months/2027-02/confirm",
            body={
                "requestId": str(uuid4()),
                "sourceRevisionId": saved["revision"]["id"],
                "planVersion": 1,
            },
            expected=409,
        )
        # 从未打开的已生效历史月份仍按原版本还原；可以固化，但不能修改额度。
        clock["now"] += timedelta(days=40)
        historical = await read_month(api, "2026-11")
        assert (
            historical["state"] == "preview"
            and historical["plan"]["items"][1]["monthlyAmount"] == 110000
        )
        assert (await confirm(api, "2026-11"))["version"] == 1
        await api.request(
            "PUT",
            suffix="/months/2026-11",
            body={
                "requestId": str(uuid4()),
                "version": 1,
                "override": {"plannedIncome": 0},
            },
            expected=422,
        )
        await api.request(
            "POST",
            suffix="/months/2026-10/confirm",
            body={
                "requestId": str(uuid4()),
                "sourceRevisionId": saved["revision"]["id"],
                "planVersion": 2,
            },
            expected=422,
        )


def test_single_month_complete_replacement_zero_and_historical_replay(monkeypatch):
    run_case(monkeypatch, override_case)


async def override_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await confirm(api, "2026-11")
        item = api.plan["items"][-1]["id"]
        body = {
            "requestId": str(uuid4()),
            "version": 1,
            "override": {
                "plannedIncome": 0,
                "itemAmounts": [{"itemId": item, "amount": 60000}],
            },
        }
        value = await api.request("PUT", suffix="/months/2026-11", body=body)
        assert value["version"] == 2 and value["plan"]["plannedIncome"] == 0
        assert value["plan"]["items"][-1]["monthlyAmount"] == 60000
        assert (await read_month(api, "2026-12"))["plan"]["items"][-1]["monthlyAmount"] == 40000
        assert await api.request("PUT", suffix="/months/2026-11", body=body) == value
        await api.request("PUT", suffix="/months/2026-12", body=body, expected=409)
        await api.request(
            "PUT",
            suffix="/months/2026-11",
            body={
                "requestId": str(uuid4()),
                "version": 2,
                "override": {"itemAmounts": [{"itemId": str(uuid4()), "amount": 1}]},
            },
            expected=422,
        )
        zero = await api.request(
            "PUT",
            suffix="/months/2026-11",
            body={
                "requestId": str(uuid4()),
                "version": 2,
                "override": {"itemAmounts": [{"itemId": item, "amount": 0}]},
            },
        )
        assert (
            zero["plan"]["plannedIncome"] == 1560000
            and zero["plan"]["items"][-1]["monthlyAmount"] == 0
        )
        restore_body = {"requestId": str(uuid4()), "version": 3, "override": {}}
        restored = await api.request("PUT", suffix="/months/2026-11", body=restore_body)
        assert restored["version"] == 4 and restored["plan"]["items"][-1]["monthlyAmount"] == 40000
        # 新月到来后，成功请求重放不再写入；新请求则禁止改历史。
        clock["now"] += timedelta(days=40)
        assert await api.request("PUT", suffix="/months/2026-11", body=restore_body) == restored
        await api.request(
            "PUT",
            suffix="/months/2026-11",
            body={
                "requestId": str(uuid4()),
                "version": 4,
                "override": {},
            },
            expected=422,
        )


def test_exact_batch_keeps_other_overrides_and_default_plan(monkeypatch):
    run_case(monkeypatch, precision_case)


async def precision_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        await confirm(api, "2026-11")
        other = api.plan["items"][-1]["id"]
        await api.request(
            "PUT",
            suffix="/months/2026-11",
            body={
                "requestId": str(uuid4()),
                "version": 1,
                "override": {
                    "plannedIncome": 0,
                    "itemAmounts": [{"itemId": other, "amount": 60000}],
                },
            },
        )
        body, preview = await batch_body(api, equalization(api))
        assert [row["amount"] for row in preview["schedule"]] == [50834] * 4 + [50833] * 8
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 1
        value = await api.request("POST", suffix="/equalizations", body=body)
        months = value["months"]
        assert [row["version"] for row in months] == [3] + [1] * 11
        assert sum(row["plan"]["items"][3]["monthlyAmount"] for row in months) == 610000
        assert months[0]["plan"]["plannedIncome"] == 0
        assert months[0]["plan"]["items"][-1]["monthlyAmount"] == 60000
        assert (await read_month(api, "2027-11"))["plan"]["items"][3]["monthlyAmount"] == 52000
        assert (await api.request("GET"))["version"] == 1
        assert (await api.request("GET"))["revisions"][0]["plan"]["items"][3][
            "monthlyAmount"
        ] == 52000
        assert await api.request("POST", suffix="/equalizations", body=body) == value
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 12
            assert await session.scalar(select(func.count()).select_from(BudgetFundRecord)) == 0


def test_equalization_rejects_stale_tokens_changed_months_and_missing_project(monkeypatch):
    run_case(monkeypatch, stale_case)


async def stale_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        body, _ = await batch_body(api, equalization(api, end="2026-12"))
        await confirm(api, "2026-12")
        await api.request("POST", suffix="/equalizations", body=body, expected=409)
        body, _ = await batch_body(api, equalization(api, end="2026-12"))
        await api.request(
            "PUT",
            suffix="/months/2026-12",
            body={"requestId": str(uuid4()), "version": 1, "override": {}},
        )
        await api.request("POST", suffix="/equalizations", body=body, expected=409)
        body, _ = await batch_body(api, equalization(api, end="2026-12"))
        altered = {**body, "totalAmount": 610001}
        await api.request("POST", suffix="/equalizations", body=altered, expected=409)
        clock["now"] += timedelta(minutes=10)
        await api.request("POST", suffix="/equalizations", body=body, expected=409)
        # 末月不再配置该项目：整段预览拒绝，不只保存前面可用月份。
        removed = copy.deepcopy(api.plan)
        removed["items"].pop(3)
        preview = await api.preview(removed, 1, "2027-01")
        await api.request("PUT", body=api.save_body(removed, 1, preview["impactToken"], "2027-01"))
        await api.request(
            "POST",
            suffix="/equalizations/preview",
            body=equalization(api, version=2, end="2027-01"),
            expected=422,
        )
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 1


def test_equalization_rolls_back_partial_snapshots_and_allows_exact_retry(monkeypatch):
    run_case(monkeypatch, lambda clock: rollback_case(clock, monkeypatch))


async def rollback_case(clock, monkeypatch):
    async with local_api() as api:
        await setup_plan(api)
        body, _ = await batch_body(api, equalization(api, end="2027-01"))
        original = BudgetMonthService.create_snapshot
        calls = 0

        async def fail_second(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SQLAlchemyError("private-db-fault")
            return await original(self, *args, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(BudgetMonthService, "create_snapshot", fail_second)
            result = await api.request("POST", suffix="/equalizations", body=body, expected=503)
            assert "private-db-fault" not in str(result)
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 0
            assert await session.scalar(select(func.count()).select_from(BudgetMutation)) == 1
            token = await session.get(BudgetPreview, body["impactToken"])
            assert token.consumed_at is None
        assert len((await api.request("POST", suffix="/equalizations", body=body))["months"]) == 3


def test_concurrent_confirm_update_and_equalize_keep_unique_versions(monkeypatch):
    run_case(monkeypatch, concurrency_case)


async def concurrency_case(clock):
    async with local_api() as api:
        await setup_plan(api, book=1)
        preview = await read_month(api, "2026-11", book=1)
        body = confirmation(preview)
        responses = await asyncio.gather(
            *[api.request("POST", 1, "/months/2026-11/confirm", body) for _ in range(2)]
        )
        assert responses[0] == responses[1]
        await api.request("POST", 1, "/months/2026-11/confirm", confirmation(preview))
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 1
            assert await session.scalar(select(func.count()).select_from(BookActivity)) == 2
        bodies = [
            {"requestId": str(uuid4()), "version": 1, "override": {"plannedIncome": income}}
            for income in (1, 2)
        ]
        responses = await asyncio.gather(
            *[
                api.client.put(
                    f"/api/v1/books/{api.books[1]}/budgets/months/2026-11",
                    json=value,
                    headers={"Authorization": "Bearer user-0"},
                )
                for value in bodies
            ]
        )
        assert sorted(value.status_code for value in responses) == [200, 409]
        body, _ = await batch_body(api, equalization(api, end="2027-01"), book=1)
        values = await asyncio.gather(
            *[api.request("POST", 1, "/equalizations", body) for _ in range(2)]
        )
        assert values[0] == values[1]
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMonth)) == 3
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(BookActivity)
                    .where(BookActivity.action == "budget_equalize")
                )
                == 1
            )


def test_shared_permissions_and_plan_preview_invalidation(monkeypatch):
    run_case(monkeypatch, permissions_case)


async def permissions_case(clock):
    async with local_api() as api:
        await setup_plan(api, book=1)
        preview = await read_month(api, "2026-11", book=1, who=1)
        assert preview["state"] == "preview"
        await read_month(api, "2026-11", book=1, who=2, expected=404)
        await api.request(
            "POST", 1, "/months/2026-11/confirm", confirmation(preview), who=1, expected=403
        )
        await api.request(
            "POST", 1, "/equalizations/preview", equalization(api), who=1, expected=403
        )
        await api.request(
            "PUT",
            1,
            "/months/2026-11",
            {"requestId": str(uuid4()), "version": 1, "override": {}},
            who=1,
            expected=403,
        )
        plan_preview = await api.preview(api.plan, 1, book=1)
        await confirm(api, "2026-11", book=1)
        await api.request(
            "PUT", 1, body=api.save_body(api.plan, 1, plan_preview["impactToken"]), expected=409
        )


def test_equalization_does_not_increment_or_require_incrementable_plan_version(monkeypatch):
    run_case(monkeypatch, max_version_case)


async def max_version_case(clock):
    async with local_api() as api:
        await setup_plan(api)
        async with api.sessions() as session:
            await session.execute(update(BudgetPlan).values(version=MAX_SAFE_INTEGER))
            await session.commit()
        body, _ = await batch_body(
            api, equalization(api, version=MAX_SAFE_INTEGER, end="2026-11", total=0)
        )
        value = await api.request("POST", suffix="/equalizations", body=body)
        assert value["months"][0]["planVersion"] == MAX_SAFE_INTEGER
        assert value["months"][0]["plan"]["items"][3]["monthlyAmount"] == 0
