"""add worker leases and bounded retry state

Revision ID: c5d91f4a8b72
Revises: a83f4c91d720
Create Date: 2026-07-27 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c5d91f4a8b72"
down_revision: Union[str, None] = "a83f4c91d720"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column("jobs", sa.Column("lease_owner", sa.String(length=160)))
    op.add_column("jobs", sa.Column("lease_token", sa.String(length=64)))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("dead_lettered_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_job_status_lease",
        "jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.add_column("job_attempts", sa.Column("lease_token", sa.String(length=64)))
    op.add_column("job_attempts", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("job_attempts", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("job_attempts", sa.Column("error_code", sa.String(length=100)))
    op.create_index(
        "uq_job_attempt_number",
        "job_attempts",
        ["job_id", "attempt"],
        unique=True,
    )

    # Preserve the number of attempts already made before enabling leases.
    op.execute(
        sa.text(
            """
            UPDATE jobs
            SET attempt_count = (
                SELECT COUNT(*)
                FROM job_attempts
                WHERE job_attempts.job_id = jobs.id
            )
            WHERE EXISTS (
                SELECT 1
                FROM job_attempts
                WHERE job_attempts.job_id = jobs.id
            )
            """
        )
    )
    # A pre-upgrade running job has no renewable lease. Close its attempt and
    # safely requeue it instead of allowing an unbounded permanent running state.
    op.execute(
        sa.text(
            """
            UPDATE job_attempts
            SET status = 'lease_expired',
                completed_at = CURRENT_TIMESTAMP,
                error_code = 'worker_upgrade_requeued',
                error_message = 'Running attempt had no lease during worker upgrade'
            WHERE status = 'running'
            """
        )
    )
    op.create_index(
        "uq_job_single_running_attempt",
        "job_attempts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )
    op.execute(
        sa.text(
            """
            UPDATE jobs
            SET status = 'queued',
                scheduled_at = CURRENT_TIMESTAMP,
                error_code = 'worker_upgrade_requeued',
                error_message = 'Running job was safely requeued during worker upgrade'
            WHERE status = 'running'
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uq_job_single_running_attempt", table_name="job_attempts")
    op.drop_index("uq_job_attempt_number", table_name="job_attempts")
    op.drop_column("job_attempts", "error_code")
    op.drop_column("job_attempts", "lease_expires_at")
    op.drop_column("job_attempts", "heartbeat_at")
    op.drop_column("job_attempts", "lease_token")
    op.drop_index("ix_job_status_lease", table_name="jobs")
    op.drop_column("jobs", "dead_lettered_at")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "lease_token")
    op.drop_column("jobs", "lease_owner")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempt_count")
