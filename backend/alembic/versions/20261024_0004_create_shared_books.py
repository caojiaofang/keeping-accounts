"""接入真实多人账本

Revision ID: 0004_shared
Revises: 0003_chat
Create Date: 2026-10-24 19:33:27.919798
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_shared"
down_revision: str | Sequence[str] | None = "0003_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """应用表结构变更。"""
    op.create_table(
        "book_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_book_activities_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_book_activities_book_id_books")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_book_activities")),
    )
    op.create_index(
        "ix_book_activities_book_created",
        "book_activities",
        ["book_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "book_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_book_invitations_book_id_books")
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_book_invitations_invited_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_book_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_book_invitations_token_hash")),
    )
    op.create_index("ix_book_invitations_book", "book_invitations", ["book_id"], unique=False)
    op.create_table(
        "book_members",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=12), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'member')", name=op.f("ck_book_members_valid_role")),
        sa.CheckConstraint(
            "status IN ('active', 'removed', 'left')", name=op.f("ck_book_members_valid_status")
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_book_members_book_id_books")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_book_members_user_id_users")
        ),
        sa.PrimaryKeyConstraint("book_id", "user_id", name=op.f("pk_book_members")),
    )
    op.create_index(
        "ix_book_members_user_status", "book_members", ["user_id", "status"], unique=False
    )
    op.create_index(
        "uq_book_members_active_owner",
        "book_members",
        ["book_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )
    op.add_column(
        "books", sa.Column("kind", sa.String(length=8), server_default="personal", nullable=False)
    )
    op.add_column(
        "books", sa.Column("status", sa.String(length=8), server_default="active", nullable=False)
    )
    op.add_column("entries", sa.Column("updated_by", sa.Uuid(), nullable=True))
    op.add_column(
        "entries",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_entries_updated_by_users"), "entries", "users", ["updated_by"], ["id"]
    )

    # 旧账单的修改人和时间沿用原记账人、创建时间，不改变收支数据。
    op.execute(
        "UPDATE entries SET updated_by = created_by, updated_at = created_at "
        "WHERE updated_by IS NULL"
    )


def downgrade() -> None:
    """撤销表结构变更。"""
    op.drop_constraint(op.f("fk_entries_updated_by_users"), "entries", type_="foreignkey")
    op.drop_column("entries", "updated_at")
    op.drop_column("entries", "updated_by")
    op.drop_column("books", "status")
    op.drop_column("books", "kind")
    op.drop_index(
        "uq_book_members_active_owner",
        table_name="book_members",
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )
    op.drop_index("ix_book_members_user_status", table_name="book_members")
    op.drop_table("book_members")
    op.drop_index("ix_book_invitations_book", table_name="book_invitations")
    op.drop_table("book_invitations")
    op.drop_index("ix_book_activities_book_created", table_name="book_activities")
    op.drop_table("book_activities")
