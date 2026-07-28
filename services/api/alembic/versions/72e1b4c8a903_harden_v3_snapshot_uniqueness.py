"""Harden V3 daily snapshot uniqueness.

Revision ID: 72e1b4c8a903
Revises: 61f4a2c9d8e0
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "72e1b4c8a903"
down_revision: str | None = "61f4a2c9d8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Compact any duplicates that PostgreSQL previously allowed because NULL
    # values in organization_unit_id/source_batch_id were considered distinct.
    op.execute(
        """
        DELETE FROM daily_snapshot
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY enterprise_id, organization_unit_id,
                                        snapshot_date, coalesce(source_batch_id, '')
                           ORDER BY source_data_as_of DESC, created_at DESC, id DESC
                       ) AS row_rank
                FROM daily_snapshot
            ) AS ranked
            WHERE row_rank > 1
        )
        """
    )
    op.drop_constraint("uq_daily_snapshot_scope_date", "daily_snapshot", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_daily_snapshot_org_date_batch
        ON daily_snapshot (
            enterprise_id, organization_unit_id, snapshot_date,
            coalesce(source_batch_id, '')
        )
        WHERE organization_unit_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_daily_snapshot_enterprise_date_batch
        ON daily_snapshot (
            enterprise_id, snapshot_date, coalesce(source_batch_id, '')
        )
        WHERE organization_unit_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("uq_daily_snapshot_enterprise_date_batch", table_name="daily_snapshot")
    op.drop_index("uq_daily_snapshot_org_date_batch", table_name="daily_snapshot")
    op.create_unique_constraint(
        "uq_daily_snapshot_scope_date",
        "daily_snapshot",
        ["enterprise_id", "organization_unit_id", "snapshot_date", "source_batch_id"],
    )
