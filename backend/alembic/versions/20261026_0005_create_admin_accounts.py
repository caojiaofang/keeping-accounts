"""独立管理员账号及登录日志；账号通过命令显式初始化，不在迁移中读取密钥。"""

import sqlalchemy as sa

from alembic import op

revision = "0005_admin"
down_revision = "0004_shared"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("password_changed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "session_version >= 1", name=op.f("ck_admin_accounts_valid_session_version")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_accounts")),
        sa.UniqueConstraint("username", name=op.f("uq_admin_accounts_username")),
    )
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid()),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(256), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin_accounts.id"],
            ondelete="SET NULL",
            name=op.f("fk_admin_audit_events_admin_id_admin_accounts"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure')", name=op.f("ck_admin_audit_events_valid_outcome")
        ),
        sa.CheckConstraint(
            "action IN ('login', 'logout', 'password_change', 'logout_all', "
            "'password_reset', 'bootstrap')",
            name=op.f("ck_admin_audit_events_valid_action"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_events")),
    )
    op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_table("admin_accounts")
