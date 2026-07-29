"""add deletion-detecting audit chain anchor

Revision ID: 7c4a9e1d2f30
Revises: 902b75c8e14e
Create Date: 2026-07-27 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "7c4a9e1d2f30"
down_revision: Union[str, None] = "902b75c8e14e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain_scope", sa.String(length=64), nullable=False),
        sa.Column("legacy_event_count", sa.BigInteger(), nullable=False),
        sa.Column("legacy_root_hash", sa.String(length=64), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_integrity_hash", sa.String(length=64), nullable=False),
        sa.Column("anchor_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("chain_scope", name=op.f("pk_audit_chain_heads")),
    )
    op.add_column("audit_events", sa.Column("chain_scope", sa.String(length=64)))
    op.add_column("audit_events", sa.Column("chain_sequence", sa.BigInteger()))
    op.add_column("audit_events", sa.Column("previous_integrity_hash", sa.String(length=64)))
    # A unique index is portable across PostgreSQL and SQLite Alembic runs.
    op.create_index(
        "uq_audit_chain_sequence",
        "audit_events",
        ["chain_scope", "chain_sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_audit_chain_sequence", table_name="audit_events")
    op.drop_column("audit_events", "previous_integrity_hash")
    op.drop_column("audit_events", "chain_sequence")
    op.drop_column("audit_events", "chain_scope")
    op.drop_table("audit_chain_heads")
