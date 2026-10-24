"""新增消费分析会话

Revision ID: 0003_chat
Revises: 0002_ledger
Create Date: 2026-10-24 13:20:09.887529
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_chat"
down_revision: str | Sequence[str] | None = "0002_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """应用表结构变更。"""
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("title", sa.String(length=40), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], name=op.f("fk_chat_conversations_book_id_books")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_chat_conversations_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_conversations")),
    )
    op.create_index(
        "ix_chat_conversations_owner_book",
        "chat_conversations",
        ["owner_id", "book_id", "month"],
        unique=False,
    )
    op.create_table(
        "chat_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("answer", sa.String(length=2500), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            name=op.f("fk_chat_turns_conversation_id_chat_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_turns")),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_chat_turns_conversation_sequence"
        ),
    )


def downgrade() -> None:
    """撤销表结构变更。"""
    op.drop_table("chat_turns")
    op.drop_index("ix_chat_conversations_owner_book", table_name="chat_conversations")
    op.drop_table("chat_conversations")
