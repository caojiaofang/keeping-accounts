"""在临时目录检查迁移模板和离线 SQL，不访问数据库。"""

from io import StringIO
from shutil import copy2

from alembic.config import Config

from alembic import command
from app.core.config import BACKEND_DIR


def test_migration_template_and_offline_upgrade(tmp_path, monkeypatch):
    migration_dir = tmp_path / "alembic"
    (migration_dir / "versions").mkdir(parents=True)
    for filename in ("env.py", "script.py.mako"):
        copy2(BACKEND_DIR / "alembic" / filename, migration_dir / filename)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:encoded%25password@127.0.0.1:1/keeping_accounts_dev",
    )
    sql = StringIO()
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=sql, stdout=StringIO())
    config.set_main_option("script_location", str(migration_dir))

    command.revision(config, message="框架离线检查", rev_id="framework_test")
    command.upgrade(config, "head", sql=True)

    assert "CREATE TABLE alembic_version" in sql.getvalue()
    assert "framework_test" in sql.getvalue()


def test_nickname_migration_only_adds_nullable_user_field(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "moji:dev:")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://test:unused@127.0.0.1:1/keeping_accounts_dev"
    )
    sql = StringIO()
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=sql, stdout=StringIO())
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "0006_budget:0007_nickname", sql=True)
    assert "ALTER TABLE users ADD COLUMN nickname VARCHAR(12)" in sql.getvalue()
    assert "NOT NULL" not in sql.getvalue()
    assert "DROP TABLE" not in sql.getvalue()
