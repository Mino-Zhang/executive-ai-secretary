from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from executive_ai_api.config import get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.logging_config import configure_logging
from executive_ai_api.models import (
    DataSource,
    Enterprise,
    Job,
    OrganizationUnit,
    ScheduledTask,
    ScheduleRun,
)
from executive_ai_api.security import as_utc, utc_now
from sqlalchemy import func, select

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("executive_ai_scheduler")
stopping = False
SCHEDULER_LOCK_KEY = 4_609_302_027


def stop(*_: object) -> None:
    global stopping
    stopping = True


def next_cron_time(expression: str, timezone_name: str, after: datetime) -> datetime:
    zone = ZoneInfo(timezone_name)
    localized = as_utc(after).astimezone(zone)
    value = croniter(expression, localized).get_next(datetime)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC)


def ensure_default_tasks() -> int:
    created = 0
    with SessionLocal.begin() as db:
        now = utc_now()
        sources = db.scalars(
            select(DataSource)
            .join(Enterprise, Enterprise.id == DataSource.enterprise_id)
            .where(DataSource.is_enabled.is_(True), Enterprise.is_active.is_(True))
        ).all()
        for source in sources:
            key = f"daily-source-sync:{source.key}"
            task = db.scalar(
                select(ScheduledTask).where(
                    ScheduledTask.enterprise_id == source.enterprise_id,
                    ScheduledTask.key == key,
                )
            )
            if task is None:
                task = ScheduledTask(
                    enterprise_id=source.enterprise_id,
                    data_source_id=source.id,
                    key=key,
                    task_type="data.sync",
                    cron_expression=settings.sync_cron,
                    timezone=settings.sync_timezone,
                    is_enabled=True,
                    next_run_at=next_cron_time(
                        settings.sync_cron,
                        settings.sync_timezone,
                        now,
                    ),
                    configuration_json={"managed_by": "phase2-default"},
                )
                db.add(task)
                created += 1
    return created


def _try_scheduler_lock(db) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    return bool(db.scalar(select(func.pg_try_advisory_xact_lock(SCHEDULER_LOCK_KEY))))


def enqueue_due_tasks(limit: int = 100) -> int:
    enqueued = 0
    with SessionLocal.begin() as db:
        if not _try_scheduler_lock(db):
            return 0
        now = as_utc(db.scalar(select(func.now())) or utc_now())
        tasks = db.scalars(
            select(ScheduledTask)
            .where(
                ScheduledTask.is_enabled.is_(True),
                ScheduledTask.next_run_at.is_not(None),
                ScheduledTask.next_run_at <= now,
            )
            .order_by(ScheduledTask.next_run_at, ScheduledTask.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for task in tasks:
            scheduled_for = as_utc(task.next_run_at or now)
            scheduled_window = scheduled_for.astimezone(ZoneInfo(task.timezone)).strftime(
                "%Y%m%dT%H%M"
            )
            window_key = f"{task.key}:{scheduled_window}"
            existing = db.scalar(
                select(ScheduleRun).where(
                    ScheduleRun.scheduled_task_id == task.id,
                    ScheduleRun.window_key == window_key,
                )
            )
            if existing is None:
                organization_ids = db.scalars(
                    select(OrganizationUnit.id).where(
                        OrganizationUnit.enterprise_id == task.enterprise_id,
                        OrganizationUnit.is_active.is_(True),
                        OrganizationUnit.enabled_for_analysis.is_(True),
                        OrganizationUnit.data_connected.is_(True),
                    )
                ).all()
                job = Job(
                    enterprise_id=task.enterprise_id,
                    created_by_user_id=None,
                    job_type=task.task_type,
                    status="queued",
                    scheduled_at=now,
                    max_attempts=settings.worker_job_max_attempts,
                    payload_json={
                        "data_source_id": str(task.data_source_id),
                        "scheduled_task_id": str(task.id),
                        "trigger_type": "schedule",
                        "window_key": window_key,
                    },
                    scope_snapshot_json={
                        "system": True,
                        "enterprise_id": str(task.enterprise_id),
                        "organization_unit_ids": [str(value) for value in organization_ids],
                    },
                )
                db.add(job)
                db.flush()
                db.add(
                    ScheduleRun(
                        scheduled_task_id=task.id,
                        enterprise_id=task.enterprise_id,
                        job_id=job.id,
                        window_key=window_key,
                        trigger_type="schedule",
                        status="enqueued",
                        scheduled_for=scheduled_for,
                        enqueued_at=now,
                    )
                )
                task.last_enqueued_at = now
                enqueued += 1
            task.next_run_at = next_cron_time(
                task.cron_expression,
                task.timezone,
                scheduled_for,
            )
    return enqueued


def run() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("scheduler_started")
    while not stopping:
        try:
            ensure_default_tasks()
            count = enqueue_due_tasks()
            if count:
                logger.info("scheduled_jobs_enqueued", extra={"structured": {"count": count}})
        except Exception:
            logger.exception("scheduler_iteration_failed")
        time.sleep(settings.scheduler_poll_seconds)
    logger.info("scheduler_stopped")


if __name__ == "__main__":
    run()
