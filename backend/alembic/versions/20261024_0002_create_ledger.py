"""新增个人账本和账单

Revision ID: 0002_ledger
Revises: 0001_users
Create Date: 2026-10-24 12:51:50.134873
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_ledger"
down_revision: str | Sequence[str] | None = "0001_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """应用表结构变更。"""
    op.create_table(
        "books",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=24), nullable=False),
        sa.Column("description", sa.String(length=80), nullable=False),
        sa.Column("icon", sa.String(length=24), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("budget", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("budget >= 0", name=op.f("ck_books_budget_nonnegative")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_books_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_books")),
    )
    op.create_index(op.f("ix_books_owner_id"), "books", ["owner_id"], unique=False)
    op.create_table(
        "entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("category_id", sa.String(length=24), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.String(length=5), nullable=True),
        sa.Column("note", sa.String(length=80), nullable=False),
        sa.Column("account", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.CheckConstraint("type IN ('expense', 'income')", name=op.f("ck_entries_valid_type")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_entries_amount_positive")),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], name=op.f("fk_entries_book_id_books")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_entries_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entries")),
    )
    op.create_index("ix_entries_book_date", "entries", ["book_id", "date"], unique=False)


def downgrade() -> None:
    """撤销表结构变更。"""
    op.drop_index("ix_entries_book_date", table_name="entries")
    op.drop_table("entries")
    op.drop_index(op.f("ix_books_owner_id"), table_name="books")
    op.drop_table("books")
