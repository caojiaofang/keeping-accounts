"""集中读取环境变量和 backend/.env，统一使用 UTF-8。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        extra="ignore",
    )

    app_name: str = "墨记后端"
    app_env: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cors_origins: list[str] = Field(default_factory=list)

    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    redis_key_prefix: str = "moji:dev:"
    check_connections_on_startup: bool = False
    wechat_app_id: str = Field(default="wx0f90babab478b2b6", pattern=r"^wx[0-9a-f]{16}$")
    wechat_app_secret: SecretStr | None = None
    wechat_timeout_seconds: float = Field(default=10, ge=1, le=30)
    auth_session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    auth_login_rate_limit: int = Field(default=30, ge=1, le=1000)
    llm_base_url: str = "https://api.zf-yun.cn"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-6-luna"
    llm_api_mode: Literal["responses", "chat_completions"] = "responses"
    llm_timeout_seconds: float = Field(default=45, ge=1, le=90)
    chat_timeout_seconds: float = Field(default=240, ge=10, le=240)
    book_invite_secret: SecretStr | None = Field(default=None, min_length=32)

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LLM_BASE_URL 必须为不含认证信息和查询参数的 HTTPS 地址")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value and not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL 必须使用 postgresql+asyncpg:// 协议")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value and not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL 必须使用 redis:// 或 rediss:// 协议")
        return value

    @model_validator(mode="after")
    def validate_environment_targets(self) -> "Settings":
        development = self.app_env in {"development", "test"}
        expected_database = "keeping_accounts_dev" if development else "keeping_accounts_prod"
        expected_prefix = "moji:dev:" if development else "moji:prod:"
        if self.database_url:
            try:
                database = make_url(self.database_url.get_secret_value()).database
            except Exception:
                raise ValueError("DATABASE_URL 格式无效，请检查本地连接配置") from None
            if database != expected_database:
                raise ValueError(f"当前环境只允许连接 {expected_database} 数据库")
        if self.redis_key_prefix != expected_prefix:
            raise ValueError(f"当前环境的 REDIS_KEY_PREFIX 必须为 {expected_prefix}")
        return self
