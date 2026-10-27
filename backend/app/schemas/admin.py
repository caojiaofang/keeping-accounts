"""管理端只返回查询所需字段，不暴露微信标识、账单正文和密钥。"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AdminLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


class AdminIdentity(BaseModel):
    username: str


class AdminAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    created_at: datetime
    last_login_at: datetime | None
    password_changed_at: datetime | None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=256)


AuditAction = Literal[
    "login", "logout", "password_change", "logout_all", "password_reset", "bootstrap"
]
AuditOutcome = Literal["success", "failure"]


class AdminAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    action: AuditAction
    outcome: AuditOutcome
    reason: str
    ip: str
    user_agent: str
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class AdminUser(BaseModel):
    id: UUID
    created_at: datetime
    last_login_at: datetime
    book_count: int
    entry_count: int


class AdminBook(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    kind: str
    status: str
    created_at: datetime
    entry_count: int
    member_count: int


class AdminMember(BaseModel):
    user_id: UUID
    role: str
    status: str
    joined_at: datetime


class AdminBookDetail(AdminBook):
    members: list[AdminMember]


class TrendPoint(BaseModel):
    date: date
    users: int
    entries: int


class Overview(BaseModel):
    users: int
    new_users_today: int
    active_users_7d: int
    books: int
    shared_books: int
    entries: int
    new_entries_today: int
    trend: list[TrendPoint]
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    generated_at: datetime
