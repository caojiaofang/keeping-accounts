"""月预算保留删除状态和版本，防止删除重建后旧请求覆盖新预算。"""

import sqlalchemy as sa

from alembic import op

revision = "0009_budget_deletion"
down_revision = "0008_monthly_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "budget_months",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("budget_months", "is_deleted")
