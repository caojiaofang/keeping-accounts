"""离线验证预算模型注册、增量迁移边界与 PostgreSQL DDL。"""

from io import StringIO

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger

from alembic import command
from app import models  # noqa: F401
from app.core.config import BACKEND_DIR
from app.db.base import Base

BUDGET_TABLES = {
    "budget_plans",
    "budget_projects",
    "budget_revisions",
    "budget_months",
    "budget_goal_periods",
    "budget_fund_records",
    "budget_entry_links",
    "budget_mutations",
    "budget_previews",
}


def migration_config(output=None):
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=output, stdout=StringIO())
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def test_budget_models_are_registered_and_versions_and_money_use_bigint():
    assert {name for name in Base.metadata.tables if name.startswith("budget_")} == BUDGET_TABLES
    for table in BUDGET_TABLES:
        for name in ("amount", "target_amount", "carry_in_amount", "version", "fund_version"):
            if name in Base.metadata.tables[table].c:
                assert isinstance(Base.metadata.tables[table].c[name].type, BigInteger)
    assert "balance" not in Base.metadata.tables["budget_projects"].c
    assert "budget" in Base.metadata.tables["books"].c


def test_budget_is_a_single_incremental_head_after_admin():
    scripts = ScriptDirectory.from_config(migration_config())
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision("0006_budget").down_revision == "0005_admin"


def test_budget_upgrade_sql_only_creates_budget_objects(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "moji:dev:")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://test:unused@127.0.0.1:1/keeping_accounts_dev"
    )
    output = StringIO()
    command.upgrade(migration_config(output), "0005_admin:0006_budget", sql=True)
    sql = output.getvalue()
    for table in BUDGET_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert "WHERE superseded_at IS NULL" in sql
    assert "WHERE kind = 'opening' AND voided_at IS NULL" in sql
    assert "WHERE kind = 'use' AND voided_at IS NULL" in sql
    assert "FOREIGN KEY(source_revision_id, book_id)" in sql
    assert "FOREIGN KEY(goal_period_id, project_id, book_id)" in sql
    for table in ("users", "books", "entries", "book_members"):
        assert f"ALTER TABLE {table}" not in sql
        assert f"UPDATE {table}" not in sql
        assert f"DELETE FROM {table}" not in sql
    assert "from app.models" not in (
        BACKEND_DIR / "alembic/versions/20261106_0006_create_family_budget.py"
    ).read_text(encoding="utf-8")


def test_budget_downgrade_sql_preserves_existing_tables(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "moji:dev:")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://test:unused@127.0.0.1:1/keeping_accounts_dev"
    )
    output = StringIO()
    command.downgrade(migration_config(output), "0006_budget:0005_admin", sql=True)
    sql = output.getvalue()
    for table in BUDGET_TABLES:
        assert f"DROP TABLE {table}" in sql
    for table in ("users", "books", "entries", "book_members"):
        assert f"DROP TABLE {table}" not in sql
