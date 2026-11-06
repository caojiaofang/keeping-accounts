"""新增家庭预算方案、月快照、目标与资金记录；不改写旧账单。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_budget"
down_revision = "0005_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version BETWEEN 0 AND 9007199254740991", name=op.f("ck_budget_plans_valid_version")
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_budget_plans_book_id_books")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_plans")),
        sa.UniqueConstraint("book_id", name=op.f("uq_budget_plans_book_id")),
        sa.UniqueConstraint("id", "book_id", name="uq_budget_plans_id_book"),
    )
    op.create_table(
        "budget_previews",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("impact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now() + interval '10 minutes'"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(dependencies) = 'object'",
            name=op.f("ck_budget_previews_valid_dependencies_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(impact) = 'object'", name=op.f("ck_budget_previews_valid_impact_object")
        ),
        sa.CheckConstraint(
            "operation IN ('plan_save', 'equalize')",
            name=op.f("ck_budget_previews_valid_operation"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_budget_previews_valid_request_hash")
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_budget_previews_valid_expiry")),
        sa.CheckConstraint("length(token) >= 32", name=op.f("ck_budget_previews_valid_token")),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_budget_previews_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_budget_previews_book_id_books")
        ),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_budget_previews")),
    )
    op.create_index(
        "ix_budget_previews_book_actor", "budget_previews", ["book_id", "actor_id"], unique=False
    )
    op.create_index("ix_budget_previews_expires", "budget_previews", ["expires_at"], unique=False)
    op.create_table(
        "budget_mutations",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), server_default="200", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result) = 'object'", name=op.f("ck_budget_mutations_valid_result_object")
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_budget_mutations_valid_request_hash")
        ),
        sa.CheckConstraint(
            "length(operation) > 0", name=op.f("ck_budget_mutations_valid_operation")
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 200 AND 299",
            name=op.f("ck_budget_mutations_valid_response_status"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_budget_mutations_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["budget_plans.book_id"],
            name=op.f("fk_budget_mutations_book_id_budget_plans"),
        ),
        sa.PrimaryKeyConstraint("book_id", "request_id", name=op.f("pk_budget_mutations")),
    )
    op.create_table(
        "budget_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="active", nullable=False),
        sa.Column("enabled_month", sa.Date(), nullable=False),
        sa.Column("fund_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "enabled_month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' "
            "AND EXTRACT(DAY FROM enabled_month) = 1",
            name=op.f("ck_budget_projects_valid_enabled_month"),
        ),
        sa.CheckConstraint(
            "kind IN ('expense', 'reserve', 'saving')", name=op.f("ck_budget_projects_valid_kind")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_budget_projects_valid_status")
        ),
        sa.CheckConstraint(
            "fund_version BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_budget_projects_valid_fund_version"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["budget_plans.book_id"],
            name=op.f("fk_budget_projects_book_id_budget_plans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_projects")),
        sa.UniqueConstraint("id", "book_id", name="uq_budget_projects_id_book"),
    )
    op.create_index(
        "ix_budget_projects_book_status", "budget_projects", ["book_id", "status"], unique=False
    )
    op.create_table(
        "budget_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("effective_month", sa.Date(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' "
            "AND EXTRACT(DAY FROM effective_month) = 1",
            name=op.f("ck_budget_revisions_valid_effective_month"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(plan) = 'object'", name=op.f("ck_budget_revisions_valid_plan_object")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_budget_revisions_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "book_id"],
            ["budget_plans.id", "budget_plans.book_id"],
            name=op.f("fk_budget_revisions_plan_id_budget_plans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_revisions")),
        sa.UniqueConstraint("id", "book_id", name="uq_budget_revisions_id_book"),
    )
    op.create_index(
        "ix_budget_revisions_book_month",
        "budget_revisions",
        ["book_id", "effective_month"],
        unique=False,
    )
    op.create_index(
        "uq_budget_revisions_current_month",
        "budget_revisions",
        ["plan_id", "effective_month"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_table(
        "budget_goal_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("target_amount", sa.BigInteger(), nullable=False),
        sa.Column("carry_in_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("state", sa.String(length=8), server_default="active", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'active' AND closed_at IS NULL) "
            "OR (state = 'closed' AND closed_at IS NOT NULL)",
            name=op.f("ck_budget_goal_periods_valid_closed_at"),
        ),
        sa.CheckConstraint(
            "start_date >= DATE '2000-01-01' AND target_date <= DATE '2100-12-31' "
            "AND start_date <= target_date",
            name=op.f("ck_budget_goal_periods_valid_dates"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'closed')", name=op.f("ck_budget_goal_periods_valid_state")
        ),
        sa.CheckConstraint(
            "carry_in_amount BETWEEN 0 AND 999999999",
            name=op.f("ck_budget_goal_periods_valid_carry_in_amount"),
        ),
        sa.CheckConstraint(
            "target_amount BETWEEN 1 AND 999999999",
            name=op.f("ck_budget_goal_periods_valid_target_amount"),
        ),
        sa.CheckConstraint(
            "version BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_budget_goal_periods_valid_version"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_budget_goal_periods_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "book_id"],
            ["budget_projects.id", "budget_projects.book_id"],
            name=op.f("fk_budget_goal_periods_project_id_budget_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_budget_goal_periods_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_goal_periods")),
        sa.UniqueConstraint("id", "project_id", "book_id", name="uq_budget_goals_id_project_book"),
    )
    op.create_index(
        "ix_budget_goals_project_dates",
        "budget_goal_periods",
        ["project_id", "start_date", "target_date"],
        unique=False,
    )
    op.create_table(
        "budget_months",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("base_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "override",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(
                "jsonb_build_object('plannedIncome', NULL, 'itemAmounts', jsonb_build_array())"
            ),
            nullable=False,
        ),
        sa.Column("resolved_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(base_plan) = 'object'",
            name=op.f("ck_budget_months_valid_base_plan_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(override) = 'object'", name=op.f("ck_budget_months_valid_override_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resolved_plan) = 'object'",
            name=op.f("ck_budget_months_valid_resolved_plan_object"),
        ),
        sa.CheckConstraint(
            "month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' AND EXTRACT(DAY FROM month) = 1",
            name=op.f("ck_budget_months_valid_month"),
        ),
        sa.CheckConstraint(
            "version BETWEEN 1 AND 9007199254740991", name=op.f("ck_budget_months_valid_version")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_budget_months_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "book_id"],
            ["budget_revisions.id", "budget_revisions.book_id"],
            name=op.f("fk_budget_months_source_revision_id_budget_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_budget_months_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_months")),
        sa.UniqueConstraint("book_id", "month", name="uq_budget_months_book_month"),
    )
    op.create_table(
        "budget_entry_links",
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("goal_period_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_budget_entry_links_valid_version"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_budget_entry_links_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["entries.id"], name=op.f("fk_budget_entry_links_entry_id_entries")
        ),
        sa.ForeignKeyConstraint(
            ["goal_period_id", "project_id", "book_id"],
            [
                "budget_goal_periods.id",
                "budget_goal_periods.project_id",
                "budget_goal_periods.book_id",
            ],
            name=op.f("fk_budget_entry_links_goal_period_id_budget_goal_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "book_id"],
            ["budget_projects.id", "budget_projects.book_id"],
            name=op.f("fk_budget_entry_links_project_id_budget_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_budget_entry_links_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("entry_id", name=op.f("pk_budget_entry_links")),
    )
    op.create_index(
        "ix_budget_links_project", "budget_entry_links", ["project_id", "is_deleted"], unique=False
    )
    op.create_table(
        "budget_fund_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=80), server_default="", nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("goal_period_id", sa.Uuid(), nullable=True),
        sa.Column("replaces_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'use' AND entry_id IS NOT NULL) OR (kind <> 'use' AND entry_id IS NULL)",
            name=op.f("ck_budget_fund_records_valid_entry_reference"),
        ),
        sa.CheckConstraint(
            "date BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'",
            name=op.f("ck_budget_fund_records_valid_date"),
        ),
        sa.CheckConstraint(
            "kind <> 'opening' OR goal_period_id IS NULL",
            name=op.f("ck_budget_fund_records_valid_opening_goal"),
        ),
        sa.CheckConstraint(
            "kind IN ('opening', 'deposit', 'use')", name=op.f("ck_budget_fund_records_valid_kind")
        ),
        sa.CheckConstraint(
            "amount BETWEEN 1 AND 999999999", name=op.f("ck_budget_fund_records_valid_amount")
        ),
        sa.CheckConstraint(
            "replaces_id IS NULL OR replaces_id <> id",
            name=op.f("ck_budget_fund_records_valid_replacement"),
        ),
        sa.CheckConstraint(
            "version BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_budget_fund_records_valid_version"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_budget_fund_records_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["entries.id"], name=op.f("fk_budget_fund_records_entry_id_entries")
        ),
        sa.ForeignKeyConstraint(
            ["goal_period_id", "project_id", "book_id"],
            [
                "budget_goal_periods.id",
                "budget_goal_periods.project_id",
                "budget_goal_periods.book_id",
            ],
            name=op.f("fk_budget_fund_records_goal_period_id_budget_goal_periods"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "book_id"],
            ["budget_projects.id", "budget_projects.book_id"],
            name=op.f("fk_budget_fund_records_project_id_budget_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["replaces_id", "project_id", "book_id"],
            [
                "budget_fund_records.id",
                "budget_fund_records.project_id",
                "budget_fund_records.book_id",
            ],
            name="fk_budget_funds_replaces_project_book",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_budget_fund_records_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_fund_records")),
        sa.UniqueConstraint("id", "project_id", "book_id", name="uq_budget_funds_id_project_book"),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_budget_fund_records_replaces_id")),
    )
    op.create_index("ix_budget_funds_goal", "budget_fund_records", ["goal_period_id"], unique=False)
    op.create_index(
        "ix_budget_funds_project_date",
        "budget_fund_records",
        ["project_id", "date", "id"],
        unique=False,
    )
    op.create_index(
        "uq_budget_funds_active_opening",
        "budget_fund_records",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'opening' AND voided_at IS NULL"),
    )
    op.create_index(
        "uq_budget_funds_active_use",
        "budget_fund_records",
        ["entry_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'use' AND voided_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("budget_fund_records")
    op.drop_table("budget_entry_links")
    op.drop_table("budget_months")
    op.drop_table("budget_goal_periods")
    op.drop_table("budget_revisions")
    op.drop_table("budget_projects")
    op.drop_table("budget_mutations")
    op.drop_table("budget_previews")
    op.drop_table("budget_plans")
