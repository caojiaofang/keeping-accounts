"""新增家庭存钱助手，原月预算与账单保持独立。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_savings_assistant"
down_revision = "0010_book_categories"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("savings_workspaces", "savings_plans"):
        columns = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("book_id", sa.Uuid(), sa.ForeignKey("books.id"), nullable=False),
            sa.Column("scope_key", sa.String(36), nullable=False),
            sa.Column("version", sa.BigInteger(), nullable=False),
            sa.Column("data", postgresql.JSONB(), nullable=False),
            sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("version BETWEEN 1 AND 9007199254740991", name="valid_version"),
        ]
        keys = ["book_id", "scope_key"]
        if name == "savings_plans":
            columns.append(sa.Column("month", sa.String(7), nullable=False))
            keys.append("month")
        op.create_table(name, *columns, sa.UniqueConstraint(*keys))
    op.create_table(
        "savings_mutations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("book_id", sa.Uuid(), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("book_id", "user_id", "request_id"),
    )


def downgrade():
    op.drop_table("savings_mutations")
    op.drop_table("savings_plans")
    op.drop_table("savings_workspaces")
