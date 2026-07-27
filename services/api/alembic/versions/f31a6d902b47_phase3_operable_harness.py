"""phase3 operable harness, multi-organization scope and private profile

Revision ID: f31a6d902b47
Revises: d4a91f7c2e10
Create Date: 2026-07-28 20:58:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f31a6d902b47"
down_revision: Union[str, None] = "d4a91f7c2e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    json_type = _json_type()

    op.create_table(
        "harness_config_versions",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="3.0", nullable=False),
        sa.Column("config_json", json_type, nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["harness_config_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enterprise_id", "version", name="uq_harness_enterprise_version"),
    )
    op.create_index(
        "ix_harness_config_versions_enterprise_id",
        "harness_config_versions",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_harness_enterprise_active",
        "harness_config_versions",
        ["enterprise_id", "is_active"],
    )
    op.create_index(
        "uq_harness_one_active_enterprise",
        "harness_config_versions",
        ["enterprise_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.add_column(
        "conversations",
        sa.Column(
            "scope_mode",
            sa.String(length=32),
            server_default="all_authorized",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE conversations SET scope_mode = CASE "
        "WHEN organization_unit_id IS NULL THEN 'all_authorized' ELSE 'selected' END"
    )
    op.create_table(
        "conversation_organization_scopes",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("organization_unit_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_unit_id"], ["organization_units.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "organization_unit_id",
            name="uq_conversation_organization_scope",
        ),
    )
    op.create_index(
        "ix_conversation_scope_conversation",
        "conversation_organization_scopes",
        ["conversation_id"],
    )
    op.execute(
        "INSERT INTO conversation_organization_scopes "
        "(id, conversation_id, organization_unit_id, created_at, updated_at) "
        "SELECT id, id, organization_unit_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM conversations WHERE organization_unit_id IS NOT NULL"
    )

    op.add_column("jobs", sa.Column("harness_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_harness_version",
        "jobs",
        "harness_config_versions",
        ["harness_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_harness_version_id", "jobs", ["harness_version_id"])

    op.add_column(
        "message_routes",
        sa.Column("query_spec_json", json_type, server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column("message_routes", sa.Column("harness_version_id", sa.Uuid(), nullable=True))
    op.add_column(
        "message_routes",
        sa.Column("route_source", sa.String(length=40), server_default="hermes", nullable=False),
    )
    op.add_column(
        "message_routes", sa.Column("matched_rule_id", sa.String(length=100), nullable=True)
    )
    op.create_foreign_key(
        "fk_message_routes_harness_version",
        "message_routes",
        "harness_config_versions",
        ["harness_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_message_routes_harness_version_id", "message_routes", ["harness_version_id"]
    )

    op.create_table(
        "harness_stage_runs",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("harness_version_id", sa.Uuid(), nullable=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("route_source", sa.String(length=40), nullable=True),
        sa.Column("model_name", sa.String(length=160), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_names_json", json_type, nullable=False),
        sa.Column("summary_json", json_type, nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["harness_version_id"], ["harness_config_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_harness_stage_message_created",
        "harness_stage_runs",
        ["message_id", "created_at"],
    )
    op.create_index(
        "ix_harness_stage_enterprise_created",
        "harness_stage_runs",
        ["enterprise_id", "created_at"],
    )
    op.create_index(
        "ix_harness_stage_runs_enterprise_id", "harness_stage_runs", ["enterprise_id"]
    )
    op.create_index("ix_harness_stage_runs_message_id", "harness_stage_runs", ["message_id"])
    op.create_index(
        "ix_harness_stage_runs_harness_version_id",
        "harness_stage_runs",
        ["harness_version_id"],
    )

    op.create_table(
        "harness_diagnostic_grants",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_harness_diagnostic_message"),
    )
    op.create_index(
        "ix_harness_diagnostic_expiry",
        "harness_diagnostic_grants",
        ["expires_at", "revoked_at"],
    )
    op.create_index(
        "ix_harness_diagnostic_grants_enterprise_id",
        "harness_diagnostic_grants",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_harness_diagnostic_grants_conversation_id",
        "harness_diagnostic_grants",
        ["conversation_id"],
    )
    op.create_index(
        "ix_harness_diagnostic_grants_message_id",
        "harness_diagnostic_grants",
        ["message_id"],
    )

    op.create_table(
        "executive_personal_profiles",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile_ciphertext", sa.Text(), nullable=False),
        sa.Column("profile_nonce", sa.String(length=64), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_executive_personal_profile_user"),
    )
    op.create_index(
        "ix_executive_personal_profiles_enterprise_id",
        "executive_personal_profiles",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_executive_personal_profiles_user_id",
        "executive_personal_profiles",
        ["user_id"],
    )

    op.add_column("memories", sa.Column("content_ciphertext", sa.Text(), nullable=True))
    op.add_column("memories", sa.Column("content_nonce", sa.String(length=64), nullable=True))
    op.add_column(
        "memories", sa.Column("encryption_key_version", sa.String(length=64), nullable=True)
    )
    # Memory event bodies were an implementation detail and are intentionally purged.
    op.execute("UPDATE memory_events SET previous_content = NULL, new_content = NULL")


def downgrade() -> None:
    op.drop_column("memories", "encryption_key_version")
    op.drop_column("memories", "content_nonce")
    op.drop_column("memories", "content_ciphertext")
    op.drop_index("ix_executive_personal_profiles_user_id", table_name="executive_personal_profiles")
    op.drop_index(
        "ix_executive_personal_profiles_enterprise_id",
        table_name="executive_personal_profiles",
    )
    op.drop_table("executive_personal_profiles")
    op.drop_index(
        "ix_harness_diagnostic_grants_message_id", table_name="harness_diagnostic_grants"
    )
    op.drop_index(
        "ix_harness_diagnostic_grants_conversation_id", table_name="harness_diagnostic_grants"
    )
    op.drop_index(
        "ix_harness_diagnostic_grants_enterprise_id", table_name="harness_diagnostic_grants"
    )
    op.drop_index("ix_harness_diagnostic_expiry", table_name="harness_diagnostic_grants")
    op.drop_table("harness_diagnostic_grants")
    op.drop_index(
        "ix_harness_stage_runs_harness_version_id", table_name="harness_stage_runs"
    )
    op.drop_index("ix_harness_stage_runs_message_id", table_name="harness_stage_runs")
    op.drop_index("ix_harness_stage_runs_enterprise_id", table_name="harness_stage_runs")
    op.drop_index("ix_harness_stage_enterprise_created", table_name="harness_stage_runs")
    op.drop_index("ix_harness_stage_message_created", table_name="harness_stage_runs")
    op.drop_table("harness_stage_runs")
    op.drop_index("ix_message_routes_harness_version_id", table_name="message_routes")
    op.drop_constraint("fk_message_routes_harness_version", "message_routes", type_="foreignkey")
    op.drop_column("message_routes", "matched_rule_id")
    op.drop_column("message_routes", "route_source")
    op.drop_column("message_routes", "harness_version_id")
    op.drop_column("message_routes", "query_spec_json")
    op.drop_index("ix_jobs_harness_version_id", table_name="jobs")
    op.drop_constraint("fk_jobs_harness_version", "jobs", type_="foreignkey")
    op.drop_column("jobs", "harness_version_id")
    op.drop_index("ix_conversation_scope_conversation", table_name="conversation_organization_scopes")
    op.drop_table("conversation_organization_scopes")
    op.drop_column("conversations", "scope_mode")
    op.drop_index("uq_harness_one_active_enterprise", table_name="harness_config_versions")
    op.drop_index("ix_harness_enterprise_active", table_name="harness_config_versions")
    op.drop_index("ix_harness_config_versions_enterprise_id", table_name="harness_config_versions")
    op.drop_table("harness_config_versions")
