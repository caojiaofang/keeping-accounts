"""仅在指定本机测试库的事务 schema 中验迁移；全部 DDL 与数据最终回滚。"""

import asyncio
import copy
import json
import os
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, insert, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.config import BACKEND_DIR
from app.db.base import Base
from app.models.budget import (
    BudgetEntryLink,
    BudgetFundRecord,
    BudgetGoalPeriod,
    BudgetMonth,
    BudgetMutation,
    BudgetPlan,
    BudgetPreview,
    BudgetProject,
    BudgetRevision,
)
from app.models.ledger import Book, LedgerEntry
from app.models.user import User

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1",
        reason="需显式启用本机预算迁移测试",
    ),
]


def test_budget_upgrade_constraints_downgrade_and_reupgrade():
    asyncio.run(isolated_roundtrip())


async def isolated_roundtrip():
    value = os.getenv("MOJI_BUDGET_MIGRATION_TEST_URL")
    if not value:
        pytest.skip("未提供独立的本机预算迁移测试库")
    url = make_url(value)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.database != "moji_budget_migration_test"
    ):
        raise RuntimeError("迁移验证仅允许本机 moji_budget_migration_test，不使用开发或正式库")
    schema = "budget_test_" + uuid4().hex
    engine = create_async_engine(value, connect_args={"timeout": 10, "command_timeout": 30})
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                assert await connection.scalar(text("SELECT current_schema()")) == schema
                await connection.run_sync(migration_roundtrip)
            finally:
                await transaction.rollback()
            assert not await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"),
                {"schema": schema},
            )
    finally:
        await engine.dispose()


def reject(connection, statement, sqlstate):
    """每个无效写入在 savepoint 中回滚，继续检查同一份隔离数据。"""
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert error.value.orig.sqlstate == sqlstate


def migration_roundtrip(connection):
    config = Config(str(BACKEND_DIR / "alembic.ini"), stdout=StringIO())
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["connection"] = connection
    command.upgrade(config, "0005_admin")
    owner = uuid4()
    books = [uuid4() for _ in range(3)]
    old_entry = uuid4()
    connection.execute(
        insert(User).values(id=owner, wechat_app_id="wx0f90babab478b2b6", openid=str(owner))
    )
    for number, book_id in enumerate(books):
        connection.execute(
            insert(Book).values(id=book_id, owner_id=owner, name=f"旧账本{number}", budget=456789)
        )
    connection.execute(
        insert(LedgerEntry).values(
            id=old_entry,
            book_id=books[0],
            created_by=owner,
            type="expense",
            category_id="house",
            amount=12345,
            date=date(2026, 11, 6),
            account="原账户",
            note="迁移前保留的账单",
        )
    )
    baseline = connection.execute(select(Book.id, Book.name, Book.budget).order_by(Book.id)).all()
    baseline_entry = connection.execute(select(LedgerEntry.__table__)).all()

    command.upgrade(config, "head")
    assert (
        connection.scalar(text("SELECT version_num FROM alembic_version")) == "0009_budget_deletion"
    )
    assert (
        connection.execute(select(Book.id, Book.name, Book.budget).order_by(Book.id)).all()
        == baseline
    )
    assert connection.execute(select(LedgerEntry.__table__)).all() == baseline_entry
    validate_metadata(connection)
    validate_constraints(connection, owner, books)

    command.downgrade(config, "0005_admin")
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005_admin"
    assert not any(name.startswith("budget_") for name in inspect(connection).get_table_names())
    assert (
        connection.execute(select(Book.id, Book.name, Book.budget).order_by(Book.id)).all()
        == baseline
    )
    # 约束验证新建的用款账单仍是正常旧账单，不随预算回退删除。
    assert connection.scalar(select(LedgerEntry.amount).where(LedgerEntry.id == old_entry)) == 12345
    assert (
        connection.scalar(select(LedgerEntry.note).where(LedgerEntry.id == old_entry))
        == "迁移前保留的账单"
    )
    command.upgrade(config, "head")
    validate_metadata(connection)
    assert connection.scalar(select(text("count(*)")).select_from(BudgetPlan)) == 0


def validate_metadata(connection):
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_object": lambda obj, name, kind, reflected, other: (
                kind != "table" or name.startswith("budget_")
            ),
        },
    )
    assert compare_metadata(context, Base.metadata) == []
    inspector = inspect(connection)
    for table in Base.metadata.tables.values():
        if table.name.startswith("budget_"):
            expected = {
                item.name for item in table.constraints if isinstance(item, CheckConstraint)
            }
            assert {
                item["name"] for item in inspector.get_check_constraints(table.name)
            } == expected


def validate_constraints(connection, owner, books):
    actor = {"created_by": owner, "updated_by": owner}
    today = date(2026, 11, 6)
    now = datetime.now(UTC)
    payload = json.loads(
        (BACKEND_DIR / "docs/examples/family-budget-plan.json").read_text(encoding="utf-8")
    )
    plan_ids = [uuid4(), uuid4()]
    for plan_id, book_id in zip(plan_ids, books[:2], strict=True):
        connection.execute(insert(BudgetPlan).values(id=plan_id, book_id=book_id, version=1))
    reject(connection, insert(BudgetPlan).values(book_id=books[0]), "23505")
    reject(connection, insert(BudgetPlan).values(book_id=books[2], version=-1), "23514")
    for item in payload["items"]:
        connection.execute(
            insert(BudgetProject).values(
                id=item["id"],
                book_id=books[0],
                kind=item["kind"],
                enabled_month=date(2026, 11, 1),
            )
        )
    saving, reserve = payload["items"][5]["id"], payload["items"][3]["id"]
    foreign_project = uuid4()
    connection.execute(
        insert(BudgetProject).values(
            id=foreign_project,
            book_id=books[1],
            kind="saving",
            enabled_month=date(2026, 11, 1),
        )
    )
    reject(
        connection,
        insert(BudgetProject).values(
            book_id=books[0],
            kind="saving",
            enabled_month=date(2026, 11, 2),
        ),
        "23514",
    )
    reject(
        connection,
        insert(BudgetProject).values(
            book_id=books[0],
            kind="unknown",
            enabled_month=date(2026, 11, 1),
        ),
        "23514",
    )
    revision = uuid4()
    revision_values = dict(
        plan_id=plan_ids[0],
        book_id=books[0],
        effective_month=date(2026, 11, 1),
        plan=payload,
        created_by=owner,
    )
    connection.execute(insert(BudgetRevision).values(id=revision, **revision_values))
    reject(connection, insert(BudgetRevision).values(**revision_values), "23505")
    reject(
        connection,
        insert(BudgetRevision).values(**{**revision_values, "plan_id": plan_ids[1]}),
        "23503",
    )
    reject(
        connection,
        insert(BudgetRevision).values(**{**revision_values, "effective_month": date(2027, 1, 2)}),
        "23514",
    )
    reject(
        connection,
        insert(BudgetRevision).values(
            **{**revision_values, "effective_month": date(2027, 1, 1), "plan": []}
        ),
        "23514",
    )
    month_values = dict(
        book_id=books[0],
        month=date(2026, 11, 1),
        source_revision_id=revision,
        base_plan=payload,
        resolved_plan=payload,
        **actor,
    )
    connection.execute(insert(BudgetMonth).values(**month_values))
    assert connection.scalar(select(BudgetMonth.override)) == {
        "plannedIncome": None,
        "itemAmounts": [],
    }
    reject(connection, insert(BudgetMonth).values(**month_values), "23505")
    reject(connection, insert(BudgetMonth).values(**{**month_values, "book_id": books[1]}), "23503")
    reject(
        connection,
        insert(BudgetMonth).values(**{**month_values, "month": date(2026, 12, 1), "version": 0}),
        "23514",
    )
    replacement_payload = copy.deepcopy(payload)
    replacement_payload["items"][5]["monthlyAmount"] = 430000
    connection.execute(
        update(BudgetRevision).where(BudgetRevision.id == revision).values(superseded_at=now)
    )
    connection.execute(
        insert(BudgetRevision).values(**{**revision_values, "plan": replacement_payload})
    )
    assert connection.scalar(select(BudgetMonth.base_plan)) == payload
    assert (
        connection.scalar(select(BudgetRevision.plan).where(BudgetRevision.id == revision))
        == payload
    )
    reject(
        connection, BudgetRevision.__table__.delete().where(BudgetRevision.id == revision), "23503"
    )

    goal_id = uuid4()
    goal_values = dict(
        project_id=saving,
        book_id=books[0],
        start_date=date(2026, 11, 1),
        target_date=date(2027, 10, 31),
        target_amount=5000000,
        **actor,
    )
    connection.execute(insert(BudgetGoalPeriod).values(id=goal_id, **goal_values))
    for changes in (
        {"target_amount": 0},
        {"target_amount": 1000000000},
        {"target_date": date(2026, 10, 31)},
        {"carry_in_amount": -1},
        {"state": "closed"},
    ):
        reject(connection, insert(BudgetGoalPeriod).values(**{**goal_values, **changes}), "23514")
    reject(
        connection, insert(BudgetGoalPeriod).values(**{**goal_values, "book_id": books[1]}), "23503"
    )
    payment = uuid4()
    connection.execute(
        insert(LedgerEntry).values(
            id=payment,
            book_id=books[0],
            created_by=owner,
            type="expense",
            category_id="house",
            amount=5000000,
            date=today,
            account="测试账户",
            note="提前还贷",
        )
    )
    link_values = dict(
        entry_id=payment, project_id=saving, book_id=books[0], goal_period_id=goal_id, **actor
    )
    connection.execute(insert(BudgetEntryLink).values(**link_values))
    reject(connection, insert(BudgetEntryLink).values(**link_values), "23505")
    connection.execute(update(BudgetEntryLink).values(is_deleted=True, version=2))
    reject(connection, insert(BudgetEntryLink).values(**link_values), "23505")
    reject(connection, update(BudgetEntryLink).values(project_id=reserve), "23503")
    reject(connection, update(BudgetEntryLink).values(book_id=books[1]), "23503")

    fund_values = dict(project_id=saving, book_id=books[0], date=today, amount=5040000, **actor)
    opening = uuid4()
    connection.execute(insert(BudgetFundRecord).values(id=opening, kind="opening", **fund_values))
    reject(connection, insert(BudgetFundRecord).values(kind="opening", **fund_values), "23505")
    for changes in (
        {"amount": 0},
        {"amount": -1},
        {"amount": 1000000000},
        {"date": date(2101, 1, 1)},
        {"version": 0},
    ):
        reject(
            connection,
            insert(BudgetFundRecord).values(kind="deposit", **{**fund_values, **changes}),
            "23514",
        )
    reject(connection, insert(BudgetFundRecord).values(kind="use", **fund_values), "23514")
    reject(
        connection,
        insert(BudgetFundRecord).values(kind="deposit", entry_id=payment, **fund_values),
        "23514",
    )
    reject(
        connection,
        insert(BudgetFundRecord).values(
            kind="deposit", goal_period_id=goal_id, **{**fund_values, "project_id": reserve}
        ),
        "23503",
    )
    reject(
        connection,
        insert(BudgetFundRecord).values(kind="deposit", **{**fund_values, "book_id": books[1]}),
        "23503",
    )
    use_values = {
        **fund_values,
        "amount": 5000000,
        "kind": "use",
        "entry_id": payment,
        "goal_period_id": goal_id,
    }
    use_id = uuid4()
    connection.execute(insert(BudgetFundRecord).values(id=use_id, **use_values))
    reject(connection, insert(BudgetFundRecord).values(**use_values), "23505")
    connection.execute(
        update(BudgetFundRecord)
        .where(BudgetFundRecord.id == use_id)
        .values(voided_at=now, version=2)
    )
    connection.execute(insert(BudgetFundRecord).values(replaces_id=use_id, **use_values))
    reject(
        connection,
        insert(BudgetFundRecord).values(
            replaces_id=opening, kind="deposit", **{**fund_values, "project_id": reserve}
        ),
        "23503",
    )
    connection.execute(
        update(BudgetFundRecord)
        .where(BudgetFundRecord.id == opening)
        .values(voided_at=now, version=2)
    )
    connection.execute(
        insert(BudgetFundRecord).values(replaces_id=opening, kind="opening", **fund_values)
    )
    reject(
        connection,
        insert(BudgetFundRecord).values(replaces_id=opening, kind="deposit", **fund_values),
        "23505",
    )

    request_id = uuid4()
    mutation = dict(
        book_id=books[0],
        request_id=request_id,
        actor_id=owner,
        operation="plan_save",
        request_hash="a" * 64,
        result={"version": 1},
    )
    connection.execute(insert(BudgetMutation).values(**mutation))
    reject(connection, insert(BudgetMutation).values(**mutation), "23505")
    connection.execute(insert(BudgetMutation).values(**{**mutation, "book_id": books[1]}))
    reject(
        connection,
        insert(BudgetMutation).values(
            **{**mutation, "request_id": uuid4(), "request_hash": "invalid"}
        ),
        "23514",
    )
    reject(
        connection,
        insert(BudgetMutation).values(
            **{**mutation, "request_id": uuid4(), "response_status": 500}
        ),
        "23514",
    )
    preview_values = dict(
        book_id=books[2],
        actor_id=owner,
        operation="plan_save",
        request_hash="b" * 64,
        dependencies={"version": 0},
        impact={"confirmedMonths": []},
    )
    token = connection.scalar(
        insert(BudgetPreview).values(**preview_values).returning(BudgetPreview.token)
    )
    assert len(token) >= 32
    times = connection.execute(select(BudgetPreview.created_at, BudgetPreview.expires_at)).one()
    assert times.expires_at - times.created_at == timedelta(minutes=10)
    reject(
        connection, insert(BudgetPreview).values(**{**preview_values, "token": "short"}), "23514"
    )
    reject(
        connection,
        insert(BudgetPreview).values(**preview_values, created_at=now, expires_at=now),
        "23514",
    )
