"""家庭预算存储；计划快照、稳定资金用途与真实账单分别保存。"""

from datetime import date, datetime
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BudgetTimestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BudgetPlan(BudgetTimestamps, Base):
    __tablename__ = "budget_plans"
    __table_args__ = (
        UniqueConstraint("book_id"),
        UniqueConstraint("id", "book_id", name="uq_budget_plans_id_book"),
        CheckConstraint("version BETWEEN 0 AND 9007199254740991", name="valid_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class BudgetProject(BudgetTimestamps, Base):
    __tablename__ = "budget_projects"
    __table_args__ = (
        UniqueConstraint("id", "book_id", name="uq_budget_projects_id_book"),
        CheckConstraint("kind IN ('expense', 'reserve', 'saving')", name="valid_kind"),
        CheckConstraint("status IN ('active', 'archived')", name="valid_status"),
        CheckConstraint("fund_version BETWEEN 0 AND 9007199254740991", name="valid_fund_version"),
        CheckConstraint(
            "enabled_month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' "
            "AND EXTRACT(DAY FROM enabled_month) = 1",
            name="valid_enabled_month",
        ),
        Index("ix_budget_projects_book_status", "book_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(ForeignKey("budget_plans.book_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="active")
    enabled_month: Mapped[date] = mapped_column(Date, nullable=False)
    fund_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class BudgetRevision(Base):
    __tablename__ = "budget_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "book_id"], ["budget_plans.id", "budget_plans.book_id"]),
        UniqueConstraint("id", "book_id", name="uq_budget_revisions_id_book"),
        CheckConstraint(
            "effective_month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' "
            "AND EXTRACT(DAY FROM effective_month) = 1",
            name="valid_effective_month",
        ),
        CheckConstraint("jsonb_typeof(plan) = 'object'", name="valid_plan_object"),
        Index(
            "uq_budget_revisions_current_month",
            "plan_id",
            "effective_month",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_budget_revisions_book_month", "book_id", "effective_month"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    book_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    effective_month: Mapped[date] = mapped_column(Date, nullable=False)
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetMonth(BudgetTimestamps, Base):
    __tablename__ = "budget_months"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_revision_id", "book_id"], ["budget_revisions.id", "budget_revisions.book_id"]
        ),
        UniqueConstraint("book_id", "month", name="uq_budget_months_book_month"),
        CheckConstraint(
            "month BETWEEN DATE '2000-01-01' AND DATE '2100-12-01' AND EXTRACT(DAY FROM month) = 1",
            name="valid_month",
        ),
        CheckConstraint("version BETWEEN 1 AND 9007199254740991", name="valid_version"),
        CheckConstraint("jsonb_typeof(base_plan) = 'object'", name="valid_base_plan_object"),
        CheckConstraint("jsonb_typeof(override) = 'object'", name="valid_override_object"),
        CheckConstraint(
            "jsonb_typeof(resolved_plan) = 'object'", name="valid_resolved_plan_object"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    source_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    base_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    override: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "jsonb_build_object('plannedIncome', NULL, 'itemAmounts', jsonb_build_array())"
        ),
    )
    resolved_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class BudgetGoalPeriod(BudgetTimestamps, Base):
    __tablename__ = "budget_goal_periods"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "book_id"], ["budget_projects.id", "budget_projects.book_id"]
        ),
        UniqueConstraint("id", "project_id", "book_id", name="uq_budget_goals_id_project_book"),
        CheckConstraint("target_amount BETWEEN 1 AND 999999999", name="valid_target_amount"),
        CheckConstraint("carry_in_amount BETWEEN 0 AND 999999999", name="valid_carry_in_amount"),
        CheckConstraint(
            "start_date >= DATE '2000-01-01' AND target_date <= DATE '2100-12-31' "
            "AND start_date <= target_date",
            name="valid_dates",
        ),
        CheckConstraint("version BETWEEN 1 AND 9007199254740991", name="valid_version"),
        CheckConstraint("state IN ('active', 'closed')", name="valid_state"),
        CheckConstraint(
            "(state = 'active' AND closed_at IS NULL) "
            "OR (state = 'closed' AND closed_at IS NOT NULL)",
            name="valid_closed_at",
        ),
        Index("ix_budget_goals_project_dates", "project_id", "start_date", "target_date"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    book_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    carry_in_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    state: Mapped[str] = mapped_column(String(8), nullable=False, server_default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class BudgetEntryLink(BudgetTimestamps, Base):
    __tablename__ = "budget_entry_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "book_id"], ["budget_projects.id", "budget_projects.book_id"]
        ),
        ForeignKeyConstraint(
            ["goal_period_id", "project_id", "book_id"],
            [
                "budget_goal_periods.id",
                "budget_goal_periods.project_id",
                "budget_goal_periods.book_id",
            ],
        ),
        CheckConstraint("version BETWEEN 1 AND 9007199254740991", name="valid_version"),
        Index("ix_budget_links_project", "project_id", "is_deleted"),
    )

    entry_id: Mapped[UUID] = mapped_column(ForeignKey("entries.id"), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    book_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    goal_period_id: Mapped[UUID | None] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class BudgetFundRecord(BudgetTimestamps, Base):
    __tablename__ = "budget_fund_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "book_id"], ["budget_projects.id", "budget_projects.book_id"]
        ),
        ForeignKeyConstraint(
            ["goal_period_id", "project_id", "book_id"],
            [
                "budget_goal_periods.id",
                "budget_goal_periods.project_id",
                "budget_goal_periods.book_id",
            ],
        ),
        UniqueConstraint("id", "project_id", "book_id", name="uq_budget_funds_id_project_book"),
        ForeignKeyConstraint(
            ["replaces_id", "project_id", "book_id"],
            [
                "budget_fund_records.id",
                "budget_fund_records.project_id",
                "budget_fund_records.book_id",
            ],
            name="fk_budget_funds_replaces_project_book",
        ),
        UniqueConstraint("replaces_id"),
        CheckConstraint("kind IN ('opening', 'deposit', 'use')", name="valid_kind"),
        CheckConstraint("amount BETWEEN 1 AND 999999999", name="valid_amount"),
        CheckConstraint("date BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'", name="valid_date"),
        CheckConstraint("version BETWEEN 1 AND 9007199254740991", name="valid_version"),
        CheckConstraint(
            "(kind = 'use' AND entry_id IS NOT NULL) OR (kind <> 'use' AND entry_id IS NULL)",
            name="valid_entry_reference",
        ),
        CheckConstraint("kind <> 'opening' OR goal_period_id IS NULL", name="valid_opening_goal"),
        CheckConstraint("replaces_id IS NULL OR replaces_id <> id", name="valid_replacement"),
        Index(
            "uq_budget_funds_active_opening",
            "project_id",
            unique=True,
            postgresql_where=text("kind = 'opening' AND voided_at IS NULL"),
        ),
        Index(
            "uq_budget_funds_active_use",
            "entry_id",
            unique=True,
            postgresql_where=text("kind = 'use' AND voided_at IS NULL"),
        ),
        Index("ix_budget_funds_project_date", "project_id", "date", "id"),
        Index("ix_budget_funds_goal", "goal_period_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    book_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("entries.id"))
    goal_period_id: Mapped[UUID | None] = mapped_column(Uuid)
    replaces_id: Mapped[UUID | None] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class BudgetMutation(Base):
    __tablename__ = "budget_mutations"
    __table_args__ = (
        CheckConstraint("length(operation) > 0", name="valid_operation"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="valid_request_hash"),
        CheckConstraint("response_status BETWEEN 200 AND 299", name="valid_response_status"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="valid_result_object"),
    )

    book_id: Mapped[UUID] = mapped_column(ForeignKey("budget_plans.book_id"), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False, server_default="200")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetPreview(Base):
    __tablename__ = "budget_previews"
    __table_args__ = (
        CheckConstraint("length(token) >= 32", name="valid_token"),
        CheckConstraint("operation IN ('plan_save', 'equalize')", name="valid_operation"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="valid_request_hash"),
        CheckConstraint("jsonb_typeof(dependencies) = 'object'", name="valid_dependencies_object"),
        CheckConstraint("jsonb_typeof(impact) = 'object'", name="valid_impact_object"),
        CheckConstraint("expires_at > created_at", name="valid_expiry"),
        Index("ix_budget_previews_book_actor", "book_id", "actor_id"),
        Index("ix_budget_previews_expires", "expires_at"),
    )

    token: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: token_urlsafe(32)
    )
    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dependencies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    impact: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '10 minutes'"),
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
