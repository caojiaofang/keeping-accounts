"""区分独立月预算与旧的持续方案，保留已有月份和账单。"""

import sqlalchemy as sa

from alembic import op

revision = "0008_monthly_budget"
down_revision = "0007_nickname"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "budget_revisions",
        sa.Column("monthly_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_index("uq_budget_revisions_current_month", table_name="budget_revisions")
    op.create_index(
        "uq_budget_revisions_current_month",
        "budget_revisions",
        ["plan_id", "effective_month"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL AND monthly_only = false"),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE budget_revisions SET superseded_at = COALESCE(superseded_at, now()) "
        "WHERE monthly_only"
    )
    op.drop_index("uq_budget_revisions_current_month", table_name="budget_revisions")
    op.create_index(
        "uq_budget_revisions_current_month",
        "budget_revisions",
        ["plan_id", "effective_month"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.drop_column("budget_revisions", "monthly_only")
