"""共享成员、仅保存摘要的邀请和账本操作记录。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BookMember(Base):
    __tablename__ = "book_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="valid_role"),
        CheckConstraint("status IN ('active', 'removed', 'left')", name="valid_status"),
        Index(
            "uq_book_members_active_owner",
            "book_id",
            unique=True,
            postgresql_where=text("role = 'owner' AND status = 'active'"),
        ),
        Index("ix_book_members_user_status", "user_id", "status"),
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(12), nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BookInvitation(Base):
    __tablename__ = "book_invitations"
    __table_args__ = (Index("ix_book_invitations_book", "book_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    invited_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookActivity(Base):
    __tablename__ = "book_activities"
    __table_args__ = (Index("ix_book_activities_book_created", "book_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
