"""move Anspire model provider to the global gateway

Revision ID: c8e5a14d7f20
Revises: b7f3c9a2e611
Create Date: 2026-07-28 16:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c8e5a14d7f20"
down_revision: Union[str, None] = "b7f3c9a2e611"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMESTIC_GATEWAY = "https://open-gateway.anspire.cn/v6"
GLOBAL_GATEWAY = "https://open-gateway.anspire.ai/v6"


def upgrade() -> None:
    provider_configs = sa.table(
        "model_provider_configs",
        sa.column("endpoint_url", sa.String()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("last_test_status", sa.String()),
        sa.column("last_test_latency_ms", sa.Integer()),
        sa.column("last_test_error", sa.Text()),
    )
    op.execute(
        provider_configs.update()
        .where(provider_configs.c.endpoint_url == DOMESTIC_GATEWAY)
        .values(
            endpoint_url=GLOBAL_GATEWAY,
            is_enabled=False,
            last_test_status="pending",
            last_test_latency_ms=None,
            last_test_error=None,
        )
    )


def downgrade() -> None:
    provider_configs = sa.table(
        "model_provider_configs",
        sa.column("endpoint_url", sa.String()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("last_test_status", sa.String()),
        sa.column("last_test_latency_ms", sa.Integer()),
        sa.column("last_test_error", sa.Text()),
    )
    op.execute(
        provider_configs.update()
        .where(provider_configs.c.endpoint_url == GLOBAL_GATEWAY)
        .values(
            endpoint_url=DOMESTIC_GATEWAY,
            is_enabled=False,
            last_test_status="pending",
            last_test_latency_ms=None,
            last_test_error=None,
        )
    )
