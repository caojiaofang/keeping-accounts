from typing import Any
from uuid import UUID

from pydantic import Field, SecretStr, field_validator

from app.schemas.ledger import LedgerSchema


class CreateInvitation(LedgerSchema):
    id: UUID
    replace: bool = False


class InvitationResponse(LedgerSchema):
    id: UUID
    token: str
    book_id: UUID
    expires_at: int
    revoked: bool = False


class InvitationToken(LedgerSchema):
    token: SecretStr = Field(min_length=1, max_length=120)


class AcceptInvitation(InvitationToken):
    name: str = Field(default="家庭成员", min_length=1, max_length=12)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class InvitationPreview(LedgerSchema):
    book_id: UUID
    book_name: str
    inviter_name: str
    expires_at: int


class BookVersion(LedgerSchema):
    version: int = Field(strict=True, ge=1)


class TransferBook(BookVersion):
    user_id: UUID


class ActivityResponse(LedgerSchema):
    id: UUID
    actor_id: UUID
    action: str
    target_id: UUID | None
    payload: dict[str, Any]
    created_at: int
