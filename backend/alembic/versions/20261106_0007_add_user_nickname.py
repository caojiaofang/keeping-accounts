"""为微信用户增加持久化账号昵称。"""

import sqlalchemy as sa

from alembic import op

revision = "0007_nickname"
down_revision = "0006_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("nickname", sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "nickname")
