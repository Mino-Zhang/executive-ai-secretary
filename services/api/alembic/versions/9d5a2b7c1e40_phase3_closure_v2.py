"""phase3 closure v2 answer, model and placement contracts

Revision ID: 9d5a2b7c1e40
Revises: 72e1b4c8a903
Create Date: 2026-07-29 16:00:00
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "9d5a2b7c1e40"
down_revision: Union[str, None] = "72e1b4c8a903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_provider_configs",
        sa.Column("credential_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "conversations", sa.Column("selected_model_id", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "messages", sa.Column("requested_model_id", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "messages",
        sa.Column("output_contract_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "messages", sa.Column("output_template_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "message_runs", sa.Column("requested_model_id", sa.String(length=100), nullable=True)
    )

    op.create_table(
        "enterprise_model_authorizations",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("test_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("tested_credential_version", sa.Integer(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("authorized_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
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
            ["authorized_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id", "model_id", name="uq_enterprise_model_authorization"
        ),
    )
    op.create_index(
        "ix_enterprise_model_authorizations_enterprise_id",
        "enterprise_model_authorizations",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_enterprise_model_authorization_state",
        "enterprise_model_authorizations",
        ["enterprise_id", "is_authorized", "is_default"],
    )
    op.create_index(
        "uq_enterprise_default_model",
        "enterprise_model_authorizations",
        ["enterprise_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default = 1"),
    )

    connection = op.get_bind()
    configs = connection.execute(
        sa.text(
            "SELECT enterprise_id, model_id, is_enabled, last_test_status, "
            "last_tested_at, last_test_latency_ms FROM model_provider_configs"
        )
    ).mappings()
    for config in configs:
        if not config["is_enabled"] or config["last_test_status"] != "success":
            continue
        authorization_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO enterprise_model_authorizations "
                "(id, enterprise_id, model_id, display_name, test_status, "
                "tested_credential_version, is_authorized, is_default, last_tested_at, "
                "last_test_latency_ms, authorized_at, created_at, updated_at) "
                "VALUES (:id, :enterprise_id, :model_id, :display_name, 'success', 1, "
                "true, true, :last_tested_at, :latency, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": authorization_id,
                "enterprise_id": config["enterprise_id"],
                "model_id": config["model_id"],
                "display_name": config["model_id"],
                "last_tested_at": config["last_tested_at"],
                "latency": config["last_test_latency_ms"],
            },
        )
        connection.execute(
            sa.text(
                "UPDATE conversations SET selected_model_id = :model_id "
                "WHERE enterprise_id = :enterprise_id AND selected_model_id IS NULL"
            ),
            {"model_id": config["model_id"], "enterprise_id": config["enterprise_id"]},
        )

    memberships = connection.execute(
        sa.text(
            "SELECT id, conversation_id FROM project_conversations "
            "ORDER BY conversation_id, created_at DESC, id DESC"
        )
    ).mappings()
    seen: set[object] = set()
    duplicate_ids: list[object] = []
    for membership in memberships:
        conversation_id = membership["conversation_id"]
        if conversation_id in seen:
            duplicate_ids.append(membership["id"])
        else:
            seen.add(conversation_id)
    for duplicate_id in duplicate_ids:
        connection.execute(
            sa.text("DELETE FROM project_conversations WHERE id = :id"),
            {"id": duplicate_id},
        )
    op.create_unique_constraint(
        "uq_project_conversation_single_project",
        "project_conversations",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_project_conversation_single_project",
        "project_conversations",
        type_="unique",
    )
    op.drop_index(
        "uq_enterprise_default_model", table_name="enterprise_model_authorizations"
    )
    op.drop_index(
        "ix_enterprise_model_authorization_state",
        table_name="enterprise_model_authorizations",
    )
    op.drop_index(
        "ix_enterprise_model_authorizations_enterprise_id",
        table_name="enterprise_model_authorizations",
    )
    op.drop_table("enterprise_model_authorizations")
    op.drop_column("message_runs", "requested_model_id")
    op.drop_column("messages", "output_template_id")
    op.drop_column("messages", "output_contract_version")
    op.drop_column("messages", "requested_model_id")
    op.drop_column("conversations", "selected_model_id")
    op.drop_column("model_provider_configs", "credential_version")
