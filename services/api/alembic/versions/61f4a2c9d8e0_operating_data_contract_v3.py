"""operating data contract v3 and atomic three-domain activation

Revision ID: 61f4a2c9d8e0
Revises: 3b2a7d9e5c41
Create Date: 2026-07-28 23:40:00
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "61f4a2c9d8e0"
down_revision: Union[str, None] = "3b2a7d9e5c41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _seed_default_weight_policies() -> None:
    bind = op.get_bind()
    enterprise_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM enterprises"))]
    if not enterprise_ids:
        return

    now = datetime.now(UTC)
    policy_table = sa.table(
        "opportunity_experience_weight_policies",
        sa.column("id", sa.Uuid()),
        sa.column("enterprise_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("label", sa.String()),
        sa.column("weights_json", _json_type()),
        sa.column("observation_windows_json", _json_type()),
        sa.column("observation_window_days", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("activated_at", sa.DateTime(timezone=True)),
        sa.column("created_by_user_id", sa.Uuid()),
        sa.column("notes", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        policy_table,
        [
            {
                "id": uuid.uuid4(),
                "enterprise_id": enterprise_id,
                "version": 1,
                "label": "经验权重初始观察口径",
                "weights_json": {"high": 0.20, "medium": 0.10, "low": 0.05},
                "observation_windows_json": [30, 60, 90],
                "observation_window_days": 90,
                "is_active": True,
                "activated_at": now,
                "created_by_user_id": None,
                "notes": "固定初始口径：高20%、中10%、低5%；不代表真实赢单概率。",
                "created_at": now,
                "updated_at": now,
            }
            for enterprise_id in enterprise_ids
        ],
    )


def upgrade() -> None:
    json_type = _json_type()

    op.create_table(
        "opportunity_experience_weight_policies",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("weights_json", json_type, nullable=False),
        sa.Column("observation_windows_json", json_type, nullable=False),
        sa.Column(
            "observation_window_days", sa.Integer(), server_default="90", nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id",
            "version",
            name="uq_opportunity_weight_policy_enterprise_version",
        ),
    )
    op.create_index(
        "ix_opportunity_experience_weight_policies_enterprise_id",
        "opportunity_experience_weight_policies",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_opportunity_weight_policy_enterprise_active",
        "opportunity_experience_weight_policies",
        ["enterprise_id", "is_active"],
    )
    op.create_index(
        "uq_opportunity_weight_policy_one_active",
        "opportunity_experience_weight_policies",
        ["enterprise_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )
    _seed_default_weight_policies()

    op.add_column(
        "data_sync_runs",
        sa.Column(
            "source_schema_hashes_json",
            json_type,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column(
            "source_record_counts_json",
            json_type,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column(
            "source_content_hashes_json",
            json_type,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column(
            "cross_table_validation_json",
            json_type,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column(
            "activation_mode",
            sa.String(length=40),
            server_default="all_three_atomic",
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column(
            "atomic_activation_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column("experience_weight_policy_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column("activation_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_sync_runs",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_data_sync_runs_experience_weight_policy",
        "data_sync_runs",
        "opportunity_experience_weight_policies",
        ["experience_weight_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_data_sync_runs_experience_weight_policy_id",
        "data_sync_runs",
        ["experience_weight_policy_id"],
    )

    op.add_column(
        "data_domain_status", sa.Column("current_source_batch_id", sa.String(160))
    )
    op.add_column("data_domain_status", sa.Column("contract_version", sa.String(32)))
    op.add_column("data_domain_status", sa.Column("status_reason", sa.Text()))

    # ODS 3.0 may activate more than once on the same calendar day. Preserve
    # each successful batch so daily-change analysis compares adjacent atomic
    # batches instead of silently overwriting the earlier snapshot.
    op.drop_constraint("uq_daily_snapshot_scope_date", "daily_snapshot", type_="unique")
    op.add_column("daily_snapshot", sa.Column("source_batch_id", sa.String(160)))
    op.create_index(
        "ix_daily_snapshot_source_batch_id", "daily_snapshot", ["source_batch_id"]
    )
    op.create_unique_constraint(
        "uq_daily_snapshot_scope_date",
        "daily_snapshot",
        ["enterprise_id", "organization_unit_id", "snapshot_date", "source_batch_id"],
    )

    op.add_column("dim_person", sa.Column("normalized_name", sa.String(200)))
    op.add_column("dim_person", sa.Column("identity_fingerprint", sa.String(64)))
    op.add_column(
        "dim_person",
        sa.Column(
            "role_types_json",
            json_type,
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_dim_person_identity_fingerprint", "dim_person", ["identity_fingerprint"]
    )

    op.add_column("dim_customer", sa.Column("normalized_name", sa.String(240)))
    op.add_column("dim_customer", sa.Column("identity_fingerprint", sa.String(64)))
    op.add_column(
        "dim_customer",
        sa.Column(
            "aliases_json", json_type, server_default=sa.text("'[]'"), nullable=False
        ),
    )
    op.add_column("dim_customer", sa.Column("customer_value_level", sa.String(40)))
    op.create_index(
        "ix_dim_customer_identity_fingerprint", "dim_customer", ["identity_fingerprint"]
    )

    op.add_column("fact_opportunity", sa.Column("upstream_record_id", sa.String(160)))
    op.add_column("fact_opportunity", sa.Column("stage_label", sa.String(80)))
    op.add_column("fact_opportunity", sa.Column("status_code", sa.String(40)))
    op.add_column("fact_opportunity", sa.Column("reliability_level", sa.String(24)))
    op.add_column("fact_opportunity", sa.Column("customer_value_level", sa.String(40)))
    op.add_column("fact_opportunity", sa.Column("industry", sa.String(120)))
    op.add_column("fact_opportunity", sa.Column("signed_amount", sa.Numeric(18, 2)))
    op.add_column(
        "fact_opportunity",
        sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "fact_opportunity", sa.Column("archived_at", sa.DateTime(timezone=True))
    )
    op.add_column("fact_opportunity", sa.Column("latest_progress", sa.Text()))
    op.alter_column(
        "fact_opportunity", "probability", existing_type=sa.Integer(), nullable=True
    )
    op.alter_column(
        "fact_opportunity",
        "expected_gross_profit",
        existing_type=sa.Numeric(18, 2),
        nullable=True,
    )
    op.create_index("ix_fact_opportunity_status_code", "fact_opportunity", ["status_code"])
    op.create_index(
        "ix_fact_opportunity_reliability_level",
        "fact_opportunity",
        ["reliability_level"],
    )

    op.create_table(
        "fact_opportunity_participant",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("participant_role", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["sync_run_id"], ["data_sync_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["fact_opportunity.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["dim_person.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "opportunity_id",
            "person_id",
            "participant_role",
            name="uq_fact_opportunity_participant_role",
        ),
    )
    for column in ("enterprise_id", "sync_run_id", "opportunity_id", "person_id"):
        op.create_index(
            f"ix_fact_opportunity_participant_{column}",
            "fact_opportunity_participant",
            [column],
        )
    op.create_index(
        "ix_fact_opportunity_participant_lookup",
        "fact_opportunity_participant",
        ["enterprise_id", "participant_role", "person_id"],
    )

    op.create_table(
        "fact_opportunity_product",
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("product_name", sa.String(length=240), nullable=False),
        sa.Column("normalized_product_name", sa.String(length=240), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["sync_run_id"], ["data_sync_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["fact_opportunity.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "opportunity_id",
            "normalized_product_name",
            name="uq_fact_opportunity_product_name",
        ),
    )
    for column in ("enterprise_id", "sync_run_id", "opportunity_id"):
        op.create_index(
            f"ix_fact_opportunity_product_{column}",
            "fact_opportunity_product",
            [column],
        )
    op.create_index(
        "ix_fact_opportunity_product_lookup",
        "fact_opportunity_product",
        ["enterprise_id", "normalized_product_name"],
    )

    op.add_column("fact_delivery", sa.Column("delivery_owner_person_id", sa.Uuid()))
    op.add_column("fact_delivery", sa.Column("opportunity_fact_id", sa.Uuid()))
    op.add_column("fact_delivery", sa.Column("recognized_revenue", sa.Numeric(18, 2)))
    op.add_column("fact_delivery", sa.Column("actual_start_date", sa.Date()))
    op.add_column("fact_delivery", sa.Column("latest_progress", sa.Text()))
    op.create_foreign_key(
        "fk_fact_delivery_delivery_owner_person",
        "fact_delivery",
        "dim_person",
        ["delivery_owner_person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_fact_delivery_opportunity_fact",
        "fact_delivery",
        "fact_opportunity",
        ["opportunity_fact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_fact_delivery_opportunity_fact_id", "fact_delivery", ["opportunity_fact_id"]
    )

    op.add_column("fact_finance_collection", sa.Column("opportunity_fact_id", sa.Uuid()))
    op.add_column("fact_finance_collection", sa.Column("delivery_fact_id", sa.Uuid()))
    op.add_column(
        "fact_finance_collection", sa.Column("collection_owner_person_id", sa.Uuid())
    )
    op.add_column("fact_finance_collection", sa.Column("payment_type", sa.String(80)))
    op.add_column(
        "fact_finance_collection", sa.Column("payment_milestone", sa.String(160))
    )
    op.add_column("fact_finance_collection", sa.Column("invoice_status", sa.String(40)))
    op.add_column("fact_finance_collection", sa.Column("invoice_number", sa.String(120)))
    op.add_column("fact_finance_collection", sa.Column("latest_follow_up", sa.Text()))
    op.alter_column(
        "fact_finance_collection",
        "invoice_amount",
        existing_type=sa.Numeric(18, 2),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_fact_collection_opportunity_fact",
        "fact_finance_collection",
        "fact_opportunity",
        ["opportunity_fact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_fact_collection_delivery_fact",
        "fact_finance_collection",
        "fact_delivery",
        ["delivery_fact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_fact_collection_owner_person",
        "fact_finance_collection",
        "dim_person",
        ["collection_owner_person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_fact_finance_collection_opportunity_fact_id",
        "fact_finance_collection",
        ["opportunity_fact_id"],
    )
    op.create_index(
        "ix_fact_finance_collection_delivery_fact_id",
        "fact_finance_collection",
        ["delivery_fact_id"],
    )


def downgrade() -> None:
    # V3 can keep multiple atomic snapshots for one calendar date.  The V2
    # uniqueness shape cannot represent them, so retain the newest snapshot
    # per enterprise/scope/date before removing source_batch_id.  Without this
    # deterministic compaction a populated V3 database cannot be downgraded.
    op.execute(
        """
        DELETE FROM daily_snapshot
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY enterprise_id, organization_unit_id, snapshot_date
                           ORDER BY source_data_as_of DESC, created_at DESC, id DESC
                       ) AS row_rank
                FROM daily_snapshot
            ) AS ranked
            WHERE row_rank > 1
        )
        """
    )
    op.drop_constraint("uq_daily_snapshot_scope_date", "daily_snapshot", type_="unique")
    op.drop_index("ix_daily_snapshot_source_batch_id", table_name="daily_snapshot")
    op.drop_column("daily_snapshot", "source_batch_id")
    op.create_unique_constraint(
        "uq_daily_snapshot_scope_date",
        "daily_snapshot",
        ["enterprise_id", "organization_unit_id", "snapshot_date"],
    )

    op.drop_index(
        "ix_fact_finance_collection_delivery_fact_id",
        table_name="fact_finance_collection",
    )
    op.drop_index(
        "ix_fact_finance_collection_opportunity_fact_id",
        table_name="fact_finance_collection",
    )
    op.drop_constraint(
        "fk_fact_collection_owner_person", "fact_finance_collection", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fact_collection_delivery_fact", "fact_finance_collection", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fact_collection_opportunity_fact",
        "fact_finance_collection",
        type_="foreignkey",
    )
    op.execute(
        "UPDATE fact_finance_collection SET invoice_amount = 0 "
        "WHERE invoice_amount IS NULL"
    )
    op.alter_column(
        "fact_finance_collection",
        "invoice_amount",
        existing_type=sa.Numeric(18, 2),
        nullable=False,
    )
    for column in (
        "latest_follow_up",
        "invoice_number",
        "invoice_status",
        "payment_milestone",
        "payment_type",
        "collection_owner_person_id",
        "delivery_fact_id",
        "opportunity_fact_id",
    ):
        op.drop_column("fact_finance_collection", column)

    op.drop_index("ix_fact_delivery_opportunity_fact_id", table_name="fact_delivery")
    op.drop_constraint(
        "fk_fact_delivery_opportunity_fact", "fact_delivery", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fact_delivery_delivery_owner_person", "fact_delivery", type_="foreignkey"
    )
    for column in (
        "latest_progress",
        "actual_start_date",
        "recognized_revenue",
        "opportunity_fact_id",
        "delivery_owner_person_id",
    ):
        op.drop_column("fact_delivery", column)

    op.drop_index(
        "ix_fact_opportunity_product_lookup", table_name="fact_opportunity_product"
    )
    for column in ("opportunity_id", "sync_run_id", "enterprise_id"):
        op.drop_index(
            f"ix_fact_opportunity_product_{column}",
            table_name="fact_opportunity_product",
        )
    op.drop_table("fact_opportunity_product")

    op.drop_index(
        "ix_fact_opportunity_participant_lookup",
        table_name="fact_opportunity_participant",
    )
    for column in ("person_id", "opportunity_id", "sync_run_id", "enterprise_id"):
        op.drop_index(
            f"ix_fact_opportunity_participant_{column}",
            table_name="fact_opportunity_participant",
        )
    op.drop_table("fact_opportunity_participant")

    op.drop_index("ix_fact_opportunity_reliability_level", table_name="fact_opportunity")
    op.drop_index("ix_fact_opportunity_status_code", table_name="fact_opportunity")
    op.execute("UPDATE fact_opportunity SET probability = 0 WHERE probability IS NULL")
    op.execute(
        "UPDATE fact_opportunity SET expected_gross_profit = 0 "
        "WHERE expected_gross_profit IS NULL"
    )
    op.alter_column(
        "fact_opportunity", "probability", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column(
        "fact_opportunity",
        "expected_gross_profit",
        existing_type=sa.Numeric(18, 2),
        nullable=False,
    )
    for column in (
        "latest_progress",
        "archived_at",
        "is_archived",
        "signed_amount",
        "industry",
        "customer_value_level",
        "reliability_level",
        "status_code",
        "stage_label",
        "upstream_record_id",
    ):
        op.drop_column("fact_opportunity", column)

    op.drop_index("ix_dim_customer_identity_fingerprint", table_name="dim_customer")
    for column in (
        "customer_value_level",
        "aliases_json",
        "identity_fingerprint",
        "normalized_name",
    ):
        op.drop_column("dim_customer", column)

    op.drop_index("ix_dim_person_identity_fingerprint", table_name="dim_person")
    for column in ("role_types_json", "identity_fingerprint", "normalized_name"):
        op.drop_column("dim_person", column)

    for column in ("status_reason", "contract_version", "current_source_batch_id"):
        op.drop_column("data_domain_status", column)

    op.drop_index(
        "ix_data_sync_runs_experience_weight_policy_id", table_name="data_sync_runs"
    )
    op.drop_constraint(
        "fk_data_sync_runs_experience_weight_policy", "data_sync_runs", type_="foreignkey"
    )
    for column in (
        "activated_at",
        "activation_started_at",
        "experience_weight_policy_id",
        "atomic_activation_status",
        "activation_mode",
        "cross_table_validation_json",
        "source_content_hashes_json",
        "source_record_counts_json",
        "source_schema_hashes_json",
    ):
        op.drop_column("data_sync_runs", column)

    op.drop_index(
        "uq_opportunity_weight_policy_one_active",
        table_name="opportunity_experience_weight_policies",
    )
    op.drop_index(
        "ix_opportunity_weight_policy_enterprise_active",
        table_name="opportunity_experience_weight_policies",
    )
    op.drop_index(
        "ix_opportunity_experience_weight_policies_enterprise_id",
        table_name="opportunity_experience_weight_policies",
    )
    op.drop_table("opportunity_experience_weight_policies")
