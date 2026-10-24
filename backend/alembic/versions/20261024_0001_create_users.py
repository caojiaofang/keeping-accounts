"""新增微信用户表，openid 在同一小程序下唯一。"""

import sqlalchemy as sa

from alembic import op

revision = "0001_users"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wechat_app_id", sa.String(length=18), nullable=False),
        sa.Column("openid", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("wechat_app_id", "openid", name="uq_users_wechat_app_id_openid"),
    )


def downgrade() -> None:
    op.drop_table("users")
