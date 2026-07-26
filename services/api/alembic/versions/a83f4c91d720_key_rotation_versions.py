"""add file and audit key versions

Revision ID: a83f4c91d720
Revises: 7c4a9e1d2f30
Create Date: 2026-07-27 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a83f4c91d720"
down_revision: Union[str, None] = "7c4a9e1d2f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "encryption_key_version",
            sa.String(length=64),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
    )
    # Existing event and chain-head signatures did not bind a key-version field.
    # NULL therefore means "verify with AUDIT_HMAC_LEGACY_KEY_VERSION".
    op.add_column("audit_events", sa.Column("audit_key_version", sa.String(length=64)))
    op.add_column(
        "audit_chain_heads",
        sa.Column("anchor_key_version", sa.String(length=64)),
    )


def downgrade() -> None:
    op.drop_column("audit_chain_heads", "anchor_key_version")
    op.drop_column("audit_events", "audit_key_version")
    op.drop_column("files", "encryption_key_version")
