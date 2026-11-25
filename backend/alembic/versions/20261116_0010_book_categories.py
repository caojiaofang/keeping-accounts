"""新增账本专属分类，保留公共分类与原账单。"""

import sqlalchemy as sa

from alembic import op

revision = "0010_book_categories"
down_revision = "0009_budget_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_categories",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("book_id", sa.Uuid(), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("name", sa.String(12), nullable=False),
        sa.Column("icon", sa.String(24), nullable=False, server_default="tag"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#e1eee7"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("type IN ('expense', 'income')", name="category_valid_type"),
    )
    op.create_index("ix_book_categories_book_id", "book_categories", ["book_id"])
    op.create_index(
        "ix_book_categories_active_name",
        "book_categories",
        ["book_id", "type", "name"],
        unique=True,
        postgresql_where=sa.text("NOT is_deleted"),
    )


def downgrade() -> None:
    op.drop_table("book_categories")
