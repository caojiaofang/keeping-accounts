from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class WeChatLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    code: SecretStr = Field(min_length=1, max_length=256, description="wx.login 返回的一次性 code")

    @field_validator("code", mode="before")
    @classmethod
    def trim_code(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    openid: str
    created_at: datetime
    last_login_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    is_new_user: bool
    user: UserResponse


class AuthErrorResponse(BaseModel):
    detail: str
