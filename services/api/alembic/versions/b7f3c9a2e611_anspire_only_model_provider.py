"""add enterprise scoped Anspire model configuration

Revision ID: b7f3c9a2e611
Revises: e6a7c2d941b0
Create Date: 2026-07-27 18:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7f3c9a2e611"
down_revision: Union[str, None] = "e6a7c2d941b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_provider_configs",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("endpoint_url", sa.String(length=300), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("api_key_nonce", sa.String(length=64), nullable=True),
        sa.Column("api_key_hint", sa.String(length=16), nullable=True),
        sa.Column("encryption_key_version", sa.String(length=64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id"],
            ["enterprises.id"],
            name=op.f("fk_model_provider_configs_enterprise_id_enterprises"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_model_provider_configs_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_provider_configs")),
        sa.UniqueConstraint("enterprise_id", name="uq_model_provider_enterprise"),
    )
    op.create_index(
        "ix_model_provider_enterprise_enabled",
        "model_provider_configs",
        ["enterprise_id", "is_enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_provider_configs_enterprise_id"),
        "model_provider_configs",
        ["enterprise_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_model_provider_configs_enterprise_id"),
        table_name="model_provider_configs",
    )
    op.drop_index(
        "ix_model_provider_enterprise_enabled",
        table_name="model_provider_configs",
    )
    op.drop_table("model_provider_configs")
