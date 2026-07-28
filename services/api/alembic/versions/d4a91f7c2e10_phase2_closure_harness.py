"""phase2 closure harness and MCP tool configuration

Revision ID: d4a91f7c2e10
Revises: c8e5a14d7f20
Create Date: 2026-07-28 19:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d4a91f7c2e10"
down_revision: Union[str, None] = "c8e5a14d7f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("memory_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "mcp_tool_configs",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("planner_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="20", nullable=False),
        sa.Column("max_rows", sa.Integer(), server_default="50", nullable=False),
        sa.Column("operator_note", sa.String(length=500), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id", "tool_name", name="uq_mcp_tool_enterprise_name"
        ),
    )
    op.create_index(
        "ix_mcp_tool_enterprise_enabled",
        "mcp_tool_configs",
        ["enterprise_id", "is_enabled"],
    )
    op.create_index(
        op.f("ix_mcp_tool_configs_enterprise_id"),
        "mcp_tool_configs",
        ["enterprise_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_mcp_tool_configs_enterprise_id"), table_name="mcp_tool_configs"
    )
    op.drop_index("ix_mcp_tool_enterprise_enabled", table_name="mcp_tool_configs")
    op.drop_table("mcp_tool_configs")
    op.drop_column("users", "memory_enabled")
