import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_utf8_env_file_and_environment_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=墨记测试\nPORT=8123\n", encoding="utf-8")
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.setenv("PORT", "8124")
    settings = Settings(_env_file=env_file)
    assert settings.app_name == "墨记测试"
    assert settings.port == 8124


def test_configuration_errors_do_not_expose_credentials():
    secret_url = "mysql://user:private-password@localhost/database"
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url=secret_url)
    assert "private-password" not in str(error.value)


def test_empty_connection_settings_are_optional(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=\nREDIS_URL=\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=env_file)
    assert settings.database_url is None
    assert settings.redis_url is None


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_cannot_select_production_database(environment):
    with pytest.raises(ValidationError, match="keeping_accounts_dev"):
        Settings(
            _env_file=None,
            app_env=environment,
            database_url="postgresql+asyncpg://test:private-password@localhost/keeping_accounts_prod",
        )


def test_development_cannot_select_production_redis_prefix():
    with pytest.raises(ValidationError, match="moji:dev:"):
        Settings(_env_file=None, app_env="development", redis_key_prefix="moji:prod:")


def test_production_requires_its_own_database_and_prefix():
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://test:test@localhost/keeping_accounts_prod",
        redis_key_prefix="moji:prod:",
    )
    assert settings.app_env == "production"
