"""enterprise composite MCP tools

Revision ID: 3b2a7d9e5c41
Revises: f31a6d902b47
"""

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3b2a7d9e5c41"
down_revision: Union[str, None] = "f31a6d902b47"
branch_labels: Union[str, list[str], None] = None
depends_on: Union[str, list[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    json_type = _json_type()
    op.create_table(
        "mcp_tool_definitions",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("tool_type", sa.String(length=32), nullable=False),
        sa.Column("component_tools_json", json_type, nullable=False),
        sa.Column("domains_json", json_type, nullable=False),
        sa.Column("parameters_json", json_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id", "tool_name", name="uq_mcp_definition_enterprise_name"
        ),
    )
    op.create_index(
        "ix_mcp_definition_enterprise_type",
        "mcp_tool_definitions",
        ["enterprise_id", "tool_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_tool_definitions_enterprise_id"),
        "mcp_tool_definitions",
        ["enterprise_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_mcp_tool_definitions_enterprise_id"), table_name="mcp_tool_definitions"
    )
    op.drop_index("ix_mcp_definition_enterprise_type", table_name="mcp_tool_definitions")
    op.drop_table("mcp_tool_definitions")
