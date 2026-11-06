"""本机隔离 PostgreSQL 的预算 API、真实事务和并发验证；仅模拟登录令牌解析。"""

import asyncio
import copy
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.api.auth_deps import get_auth_service
from app.api.deps import get_db
from app.core.config import BACKEND_DIR, Settings
from app.main import create_app
from app.models.budget import (
    BudgetFundRecord,
    BudgetMonth,
    BudgetMutation,
    BudgetPlan,
    BudgetProject,
    BudgetRevision,
)
from app.models.ledger import Book
from app.models.shared import BookActivity, BookMember
from app.models.user import User
from app.services import budget as budget_module
from app.services.sessions import unauthorized

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用本机预算接口测试",
    ),
]
NOW = datetime(2026, 11, 6, 4, 0, tzinfo=UTC)


@asynccontextmanager
async def local_api():
    value = os.getenv("MOJI_BUDGET_MIGRATION_TEST_URL")
    if not value:
        pytest.skip("未提供本机预算测试数据库")
    url = make_url(value)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.database != "moji_budget_migration_test"
    ):
        raise RuntimeError("接口验证仅允许本机 moji_budget_migration_test")
    schema = "budget_api_" + uuid4().hex
    setup = create_async_engine(value, connect_args={"timeout": 10, "command_timeout": 30})
    engine = None
    created = False
    try:
        async with setup.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

            def migrate(sync_connection):
                config = Config(str(BACKEND_DIR / "alembic.ini"), stdout=StringIO())
                config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "0006_budget")

            await connection.run_sync(migrate)
        created = True
        engine = create_async_engine(
            value,
            connect_args={
                "timeout": 10,
                "command_timeout": 30,
                "server_settings": {"search_path": schema},
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        users, books = [uuid4() for _ in range(3)], [uuid4() for _ in range(3)]
        async with sessions() as session:
            for user in users:
                await session.execute(
                    insert(User).values(
                        id=user, wechat_app_id="wx0f90babab478b2b6", openid=str(user)
                    )
                )
            for index, book in enumerate(books):
                await session.execute(
                    insert(Book).values(
                        id=book,
                        owner_id=users[2] if index == 2 else users[0],
                        name=f"测试账本{index}",
                        kind="shared" if index == 1 else "personal",
                    )
                )
            for index in (0, 1):
                await session.execute(
                    insert(BookMember).values(
                        book_id=books[1],
                        user_id=users[index],
                        role="owner" if index == 0 else "member",
                        name=f"成员{index}",
                    )
                )
            await session.commit()
        app = create_app(Settings(_env_file=None, database_url=None, redis_url=None))

        async def database():
            async with sessions() as session:
                yield session

        async def current_user(token):
            tokens = {f"user-{index}": user for index, user in enumerate(users)}
            if token not in tokens:
                raise unauthorized()
            return SimpleNamespace(id=tokens[token])

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_auth_service] = lambda: SimpleNamespace(
            current_user=current_user
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:

            async def request(method, book=0, suffix="/plan", body=None, who=0, expected=200):
                response = await client.request(
                    method,
                    f"/api/v1/books/{books[book]}/budgets{suffix}",
                    json=body,
                    headers={} if who is None else {"Authorization": f"Bearer user-{who}"},
                )
                assert response.status_code == expected, (response.status_code, response.text)
                assert response.headers["Cache-Control"] == "no-store"
                return response.json()

            async def preview(plan, version, month="2026-11", book=0, who=0):
                return await request(
                    "POST",
                    book,
                    "/plan/preview",
                    {"version": version, "effectiveMonth": month, "plan": plan},
                    who,
                )

            def save_body(plan, version, token, month="2026-11"):
                return {
                    "requestId": str(uuid4()),
                    "version": version,
                    "effectiveMonth": month,
                    "plan": plan,
                    "impactToken": token,
                }

            payload = json.loads(
                (BACKEND_DIR / "docs/examples/family-budget-plan.json").read_text(encoding="utf-8")
            )
            yield SimpleNamespace(
                request=request,
                preview=preview,
                save_body=save_body,
                sessions=sessions,
                users=users,
                books=books,
                plan=payload,
                client=client,
                app=app,
            )
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            assert re.fullmatch(r"budget_api_[0-9a-f]{32}", schema)
            async with setup.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await setup.dispose()


def run_case(monkeypatch, scenario):
    clock = {"now": NOW}
    monkeypatch.setattr(budget_module, "utc_now", lambda: clock["now"])
    asyncio.run(scenario(clock))


def test_plan_roundtrip_future_versions_and_frozen_snapshots(monkeypatch):
    run_case(monkeypatch, roundtrip_case)


async def roundtrip_case(clock):
    async with local_api() as api:
        assert (await api.request("GET"))["revisions"] == []
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetPlan)) == 0
        preview = await api.preview(api.plan, 0)
        assert preview["totals"]["totalAllocated"] == 1560000
        assert preview["totals"]["groupTotals"][-1]["amount"] == 404000
        assert preview["untilMonth"] is None
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetPlan)) == 0
        body = api.save_body(api.plan, 0, preview["impactToken"])
        saved = await api.request("PUT", body=body)
        assert saved["version"] == 1
        saved_plan = saved["revision"]["plan"]
        assert saved_plan["items"][3]["monthlyAmount"] == 52000
        assert saved_plan["items"][5]["monthlyAmount"] == 420000
        assert (await api.request("GET"))["revisions"][0] == saved["revision"]
        future = copy.deepcopy(api.plan)
        future["items"][1]["monthlyAmount"] = 120000
        next_preview = await api.preview(future, 1, "2027-03")
        await api.request(
            "PUT", body=api.save_body(future, 1, next_preview["impactToken"], "2027-03")
        )
        revision_id = UUID(saved["revision"]["id"])
        async with api.sessions() as session:
            for month, override in (
                (date(2026, 12, 1), {"plannedIncome": 1600000, "itemAmounts": []}),
                (date(2027, 3, 1), {"plannedIncome": None, "itemAmounts": []}),
            ):
                await session.execute(
                    insert(BudgetMonth).values(
                        book_id=api.books[0],
                        month=month,
                        source_revision_id=revision_id,
                        base_plan=saved_plan,
                        resolved_plan=saved_plan,
                        override=override,
                        created_by=api.users[0],
                        updated_by=api.users[0],
                    )
                )
            await session.commit()
        changed = copy.deepcopy(api.plan)
        changed["groups"][0]["name"] = "每月缴费"
        changed["items"][5]["name"] = "提前还贷准备金"
        changed["items"][5]["groupId"] = changed["groups"][0]["id"]
        impact = await api.preview(changed, 2)
        assert impact["untilMonth"] == "2027-02"
        assert impact["confirmedMonths"] == ["2026-12"]
        assert impact["overriddenMonths"] == ["2026-12"]
        await api.request("PUT", body=api.save_body(changed, 2, impact["impactToken"]))
        state = await api.request("GET")
        assert [row["effectiveMonth"] for row in state["revisions"]] == ["2026-11", "2027-03"]
        assert state["version"] == 3
        assert state["revisions"][0]["plan"]["items"][5]["name"] == "提前还贷准备金"
        async with api.sessions() as session:
            assert (
                await session.scalar(
                    select(BudgetRevision.plan).where(BudgetRevision.id == revision_id)
                )
                == saved_plan
            )
            snapshots = (
                await session.scalars(select(BudgetMonth).order_by(BudgetMonth.month))
            ).all()
            assert (
                snapshots[0].base_plan == saved_plan
                and snapshots[0].override["plannedIncome"] == 1600000
            )
            assert await session.scalar(select(func.count()).select_from(BudgetProject)) == 14
            assert (
                await session.scalar(select(Book.budget).where(Book.id == api.books[0])) == 500000
            )


def test_plan_preview_dependencies_expiry_and_idempotent_replay(monkeypatch):
    run_case(monkeypatch, preview_case)


async def preview_case(clock):
    async with local_api() as api:
        p = await api.preview(api.plan, 0)
        body = api.save_body(api.plan, 0, p["impactToken"])
        altered = copy.deepcopy(body)
        altered["plan"]["items"][0]["monthlyAmount"] += 1
        await api.request("PUT", body=altered, expected=409)
        clock["now"] += timedelta(minutes=10)
        await api.request("PUT", body=body, expected=409)
        p = await api.preview(api.plan, 0)
        body = api.save_body(api.plan, 0, p["impactToken"])
        saved = await api.request("PUT", body=body)
        clock["now"] += timedelta(days=40)
        assert await api.request("PUT", body=body) == saved
        altered = copy.deepcopy(body)
        altered["plan"]["plannedIncome"] = 100
        await api.request("PUT", body=altered, expected=409)
        fresh = await api.preview(api.plan, 1, "2027-01")
        async with api.sessions() as session:
            await session.execute(
                insert(BudgetMonth).values(
                    book_id=api.books[0],
                    month=date(2027, 1, 1),
                    source_revision_id=UUID(saved["revision"]["id"]),
                    base_plan=saved["revision"]["plan"],
                    resolved_plan=saved["revision"]["plan"],
                    created_by=api.users[0],
                    updated_by=api.users[0],
                )
            )
            await session.commit()
        await api.request(
            "PUT", body=api.save_body(api.plan, 1, fresh["impactToken"], "2027-01"), expected=409
        )
        fresh = await api.preview(api.plan, 1, "2027-01")
        async with api.sessions() as session:
            await session.execute(update(BudgetMonth).values(version=2))
            await session.commit()
        await api.request(
            "PUT", body=api.save_body(api.plan, 1, fresh["impactToken"], "2027-01"), expected=409
        )
        await api.request(
            "POST",
            suffix="/plan/preview",
            body={"version": 1, "effectiveMonth": "2026-11", "plan": api.plan},
            expected=422,
        )


def test_plan_permissions_book_isolation_and_project_identity(monkeypatch):
    run_case(monkeypatch, permissions_case)


async def permissions_case(clock):
    async with local_api() as api:
        await api.request("GET", who=None, expected=401)
        await api.request("GET", who=2, expected=404)
        assert (await api.request("GET", book=1, who=1))["version"] == 0
        request = {"version": 0, "effectiveMonth": "2026-11", "plan": api.plan}
        await api.request("POST", 1, "/plan/preview", request, who=1, expected=403)
        p = await api.preview(api.plan, 0, book=1)
        body = api.save_body(api.plan, 0, p["impactToken"])
        await api.request("PUT", 1, body=body, who=1, expected=403)
        await api.request("PUT", 0, body=body, expected=409)
        saved = await api.request("PUT", 1, body=body)
        assert (await api.request("GET", 1, who=1))["version"] == 1
        await api.request("POST", 2, "/plan/preview", request, who=2, expected=404)
        changed = copy.deepcopy(api.plan)
        changed["items"][0]["kind"] = "saving"
        await api.request(
            "POST", 1, "/plan/preview", {**request, "version": 1, "plan": changed}, expected=409
        )
        async with api.sessions() as session:
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[1])
                .values(status="removed")
            )
            await session.commit()
        await api.request("GET", 1, who=1, expected=404)
        # 转让后原创建者仍是成员，也不能靠旧成功凭据重放写入。
        async with api.sessions() as session:
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[0])
                .values(role="member")
            )
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == api.books[1], BookMember.user_id == api.users[1])
                .values(role="owner", status="active")
            )
            await session.execute(
                update(Book).where(Book.id == api.books[1]).values(owner_id=api.users[1])
            )
            await session.commit()
        await api.request("PUT", 1, body=body, expected=403)
        assert (await api.request("GET", 1, who=1))["revisions"][0] == saved["revision"]


def test_plan_concurrent_retries_conflicts_and_project_uuid_race(monkeypatch):
    run_case(monkeypatch, concurrency_case)


async def concurrency_case(clock):
    async with local_api() as api:
        p = await api.preview(api.plan, 0, book=1)
        body = api.save_body(api.plan, 0, p["impactToken"])
        results = await asyncio.gather(
            api.request("PUT", 1, body=body), api.request("PUT", 1, body=body)
        )
        assert results[0] == results[1]
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetMutation)) == 1
            assert await session.scalar(select(func.count()).select_from(BookActivity)) == 1
        p = await api.preview(api.plan, 1, book=1)
        bodies = [api.save_body(api.plan, 1, p["impactToken"]) for _ in range(2)]
        responses = await asyncio.gather(
            *[
                api.client.put(
                    f"/api/v1/books/{api.books[1]}/budgets/plan",
                    json=value,
                    headers={"Authorization": "Bearer user-0"},
                )
                for value in bodies
            ]
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        fresh = copy.deepcopy(api.plan)
        for item in fresh["items"]:
            item["id"] = str(uuid4())
        previews = await asyncio.gather(api.preview(fresh, 0), api.preview(fresh, 0, book=2, who=2))
        responses = await asyncio.gather(
            *[
                api.client.put(
                    f"/api/v1/books/{api.books[index]}/budgets/plan",
                    json=api.save_body(fresh, 0, preview["impactToken"]),
                    headers={"Authorization": f"Bearer user-{who}"},
                )
                for index, who, preview in ((0, 0, previews[0]), (2, 2, previews[1]))
            ]
        )
        assert sorted(response.status_code for response in responses) == [200, 404]
        async with api.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(BudgetPlan)) == 2


def test_project_omission_archives_without_deleting_saved_funds(monkeypatch):
    run_case(monkeypatch, archive_case)


async def archive_case(clock):
    async with local_api() as api:
        p = await api.preview(api.plan, 0)
        saved = await api.request("PUT", body=api.save_body(api.plan, 0, p["impactToken"]))
        project = UUID(api.plan["items"][5]["id"])
        async with api.sessions() as session:
            await session.execute(
                insert(BudgetFundRecord).values(
                    project_id=project,
                    book_id=api.books[0],
                    kind="opening",
                    amount=5040000,
                    date=date(2026, 11, 6),
                    created_by=api.users[0],
                    updated_by=api.users[0],
                )
            )
            await session.commit()
        empty = {"plannedIncome": 1560000, "groups": [], "items": []}
        p = await api.preview(empty, 1)
        await api.request("PUT", body=api.save_body(empty, 1, p["impactToken"]))
        async with api.sessions() as session:
            row = await session.get(BudgetProject, project)
            assert row.status == "archived" and row.fund_version == 0
            assert await session.scalar(select(BudgetFundRecord.amount)) == 5040000
            assert (
                await session.scalar(
                    select(BudgetRevision.plan).where(
                        BudgetRevision.id == UUID(saved["revision"]["id"])
                    )
                )
                == saved["revision"]["plan"]
            )
        p = await api.preview(api.plan, 2, "2027-01")
        await api.request("PUT", body=api.save_body(api.plan, 2, p["impactToken"], "2027-01"))
        async with api.sessions() as session:
            assert (await session.get(BudgetProject, project)).status == "active"
            assert await session.scalar(select(func.count()).select_from(BudgetProject)) == 14
