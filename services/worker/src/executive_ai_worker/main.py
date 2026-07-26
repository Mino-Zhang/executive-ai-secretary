from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from executive_ai_api.authz import scope_snapshot_is_current_for_user
from executive_ai_api.config import get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.job_state import (
    ASSISTANT_NOT_CONFIGURED_CONTENT,
    close_assistant_placeholder,
)
from executive_ai_api.logging_config import configure_logging
from executive_ai_api.models import AuditEvent, Enterprise, Job, JobAttempt, User
from executive_ai_api.security import as_utc, utc_now
from sqlalchemy import func, or_, select

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("executive_ai_worker")
stopping = False
worker_id = f"{socket.gethostname()}:{os.getpid()}"
RETRY_EXHAUSTED_CONTENT = "处理任务多次中断，请稍后重试"


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    lease_token: str


class PermanentJobError(Exception):
    def __init__(self, code: str, message: str, placeholder_content: str) -> None:
        self.code = code
        self.placeholder_content = placeholder_content
        super().__init__(message)


def stop(*_: object) -> None:
    global stopping
    stopping = True


def _database_now(db):
    value = db.scalar(select(func.now()))
    if value is None:
        raise RuntimeError("database clock is unavailable")
    return as_utc(value)


def authorization_is_current(db, job: Job | None) -> bool:
    if job is None or not job.created_by_user_id:
        return False
    user = db.get(User, job.created_by_user_id)
    if user is None or not user.is_active or user.role != "executive":
        return False
    enterprise = db.get(Enterprise, job.enterprise_id)
    if enterprise is None or not enterprise.is_active or user.enterprise_id != enterprise.id:
        return False
    return scope_snapshot_is_current_for_user(db, user, job.scope_snapshot_json)


def retry_delay_seconds(attempt_count: int) -> float:
    exponent = max(0, min(attempt_count - 1, 30))
    return min(
        settings.worker_retry_max_seconds,
        settings.worker_retry_base_seconds * (2**exponent),
    )


def _audit(db, job: Job, action: str, *, outcome: str, reason: str | None = None) -> None:
    db.add(
        AuditEvent(
            enterprise_id=job.enterprise_id,
            actor_user_id=job.created_by_user_id,
            action=action,
            target_type="job",
            target_id=str(job.id),
            outcome=outcome,
            failure_reason_code=reason,
            metadata_json={
                "job_type": job.job_type,
                "attempt": job.attempt_count,
                "max_attempts": job.max_attempts,
                "error_code": job.error_code,
            },
        )
    )


def _running_attempt(db, job: Job) -> JobAttempt | None:
    statement = select(JobAttempt).where(
        JobAttempt.job_id == job.id,
        JobAttempt.status == "running",
    )
    if job.lease_token:
        statement = statement.where(JobAttempt.lease_token == job.lease_token)
    return db.scalar(statement.order_by(JobAttempt.attempt.desc()).limit(1))


def _clear_lease(job: Job) -> None:
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires_at = None
    job.heartbeat_at = None


def _close_attempt(
    attempt: JobAttempt | None,
    *,
    status: str,
    error_code: str | None,
    error_message: str | None,
    completed_at=None,
) -> None:
    if attempt is None:
        return
    attempt.status = status
    attempt.completed_at = completed_at or utc_now()
    attempt.error_code = error_code
    attempt.error_message = error_message


def _terminal_failure(
    db,
    job: Job,
    attempt: JobAttempt | None,
    *,
    error_code: str,
    error_message: str,
    placeholder_content: str,
    dead_lettered: bool,
) -> None:
    now = _database_now(db)
    job.status = "failed"
    job.error_code = error_code
    job.error_message = error_message[:2000]
    job.completed_at = now
    job.dead_lettered_at = now if dead_lettered else None
    _close_attempt(
        attempt,
        status="failed",
        error_code=error_code,
        error_message=job.error_message,
        completed_at=now,
    )
    _clear_lease(job)
    close_assistant_placeholder(db, job, status="failed", content=placeholder_content)
    _audit(
        db,
        job,
        "job.dead_lettered" if dead_lettered else "job.failed",
        outcome="failure",
        reason="max_attempts_exhausted" if dead_lettered else error_code,
    )


def _retry_or_dead_letter(
    db,
    job: Job,
    attempt: JobAttempt | None,
    *,
    error_code: str,
    error_message: str,
    attempt_status: str,
) -> None:
    if job.attempt_count >= job.max_attempts:
        _terminal_failure(
            db,
            job,
            attempt,
            error_code=error_code,
            error_message=error_message,
            placeholder_content=RETRY_EXHAUSTED_CONTENT,
            dead_lettered=True,
        )
        return
    now = _database_now(db)
    delay = retry_delay_seconds(job.attempt_count)
    job.status = "queued"
    job.scheduled_at = now + timedelta(seconds=delay)
    job.completed_at = None
    job.error_code = error_code
    job.error_message = error_message[:2000]
    _close_attempt(
        attempt,
        status=attempt_status,
        error_code=error_code,
        error_message=job.error_message,
        completed_at=now,
    )
    _clear_lease(job)
    close_assistant_placeholder(db, job, status="queued")
    _audit(db, job, "job.retry_scheduled", outcome="failure", reason=error_code)


def fail_revoked_job(db, job: Job) -> None:
    _terminal_failure(
        db,
        job,
        _running_attempt(db, job),
        error_code="authorization_revoked",
        error_message="User or data scope authorization is no longer valid",
        placeholder_content="请求权限已变更，未执行处理",
        dead_lettered=False,
    )


def recover_expired_leases(limit: int = 100) -> int:
    recovered = 0
    with SessionLocal.begin() as db:
        now = _database_now(db)
        jobs = db.scalars(
            select(Job)
            .where(
                Job.status == "running",
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
            )
            .order_by(Job.lease_expires_at, Job.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            _retry_or_dead_letter(
                db,
                job,
                _running_attempt(db, job),
                error_code="lease_expired",
                error_message="Worker lease expired before the attempt completed",
                attempt_status="lease_expired",
            )
            recovered += 1
    return recovered


def claim_one() -> ClaimedJob | None:
    recover_expired_leases()
    with SessionLocal.begin() as db:
        now = _database_now(db)
        job = db.scalar(
            select(Job)
            .where(
                Job.status == "queued",
                or_(Job.scheduled_at.is_(None), Job.scheduled_at <= now),
            )
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        if job.attempt_count >= job.max_attempts:
            _terminal_failure(
                db,
                job,
                None,
                error_code="max_attempts_exhausted",
                error_message="Job reached its maximum attempt count before claim",
                placeholder_content=RETRY_EXHAUSTED_CONTENT,
                dead_lettered=True,
            )
            return None
        if not authorization_is_current(db, job):
            fail_revoked_job(db, job)
            return None

        lease_token = uuid.uuid4().hex
        lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
        job.status = "running"
        job.started_at = job.started_at or now
        job.completed_at = None
        job.scheduled_at = None
        job.error_code = None
        job.error_message = None
        job.attempt_count += 1
        job.lease_owner = worker_id
        job.lease_token = lease_token
        job.lease_expires_at = lease_expires_at
        job.heartbeat_at = now
        db.add(
            JobAttempt(
                job_id=job.id,
                attempt=job.attempt_count,
                worker_id=worker_id,
                lease_token=lease_token,
                status="running",
                started_at=now,
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
            )
        )
        close_assistant_placeholder(db, job, status="running")
        _audit(db, job, "job.started", outcome="success")
        return ClaimedJob(job_id=str(job.id), lease_token=lease_token)


def renew_lease(job_id: str, lease_token: str) -> bool:
    """Heartbeat contract for current and future long-running production handlers."""

    with SessionLocal.begin() as db:
        now = _database_now(db)
        job = db.scalar(select(Job).where(Job.id == uuid.UUID(job_id)).with_for_update())
        if (
            job is None
            or job.status != "running"
            or job.lease_owner != worker_id
            or job.lease_token != lease_token
            or job.lease_expires_at is None
            or as_utc(job.lease_expires_at) <= now
        ):
            return False
        attempt = _running_attempt(db, job)
        if attempt is None:
            return False
        deadline = now + timedelta(seconds=settings.worker_lease_seconds)
        job.heartbeat_at = now
        job.lease_expires_at = deadline
        attempt.heartbeat_at = now
        attempt.lease_expires_at = deadline
        return True


heartbeat = renew_lease


def execute_job_handler(job: Job) -> dict:
    if job.job_type == "system.noop":
        return {"ok": True}
    raise PermanentJobError(
        "integration_not_configured",
        "No production handler is configured for this job type",
        ASSISTANT_NOT_CONFIGURED_CONTENT,
    )


def _owned_running_job(db, job_id: str, lease_token: str, *, unexpired: bool) -> Job | None:
    job = db.scalar(select(Job).where(Job.id == uuid.UUID(job_id)).with_for_update())
    if (
        job is None
        or job.status != "running"
        or job.lease_owner != worker_id
        or job.lease_token != lease_token
    ):
        return None
    if unexpired and (
        job.lease_expires_at is None
        or as_utc(job.lease_expires_at) <= _database_now(db)
    ):
        return None
    return job


def _finish_success(job_id: str, lease_token: str, result: dict) -> bool:
    with SessionLocal.begin() as db:
        job = _owned_running_job(db, job_id, lease_token, unexpired=True)
        if job is None:
            return False
        if not authorization_is_current(db, job):
            fail_revoked_job(db, job)
            return True
        attempt = _running_attempt(db, job)
        job.status = "completed"
        job.result_json = result
        now = _database_now(db)
        job.completed_at = now
        job.dead_lettered_at = None
        _close_attempt(
            attempt,
            status="completed",
            error_code=None,
            error_message=None,
            completed_at=now,
        )
        _clear_lease(job)
        content = result.get("content")
        close_assistant_placeholder(
            db,
            job,
            status="completed",
            content=content if isinstance(content, str) else None,
        )
        _audit(db, job, "job.completed", outcome="success")
        return True


def _finish_permanent_failure(
    job_id: str,
    lease_token: str,
    error: PermanentJobError,
) -> bool:
    with SessionLocal.begin() as db:
        job = _owned_running_job(db, job_id, lease_token, unexpired=True)
        if job is None:
            return False
        _terminal_failure(
            db,
            job,
            _running_attempt(db, job),
            error_code=error.code,
            error_message=str(error),
            placeholder_content=error.placeholder_content,
            dead_lettered=False,
        )
        return True


def _finish_unexpected_failure(job_id: str, lease_token: str, error: Exception) -> bool:
    with SessionLocal.begin() as db:
        job = _owned_running_job(db, job_id, lease_token, unexpired=True)
        if job is None:
            return False
        message = f"{type(error).__name__}: {error}"[:2000]
        _retry_or_dead_letter(
            db,
            job,
            _running_attempt(db, job),
            error_code="processing_error",
            error_message=message,
            attempt_status="failed",
        )
        return True


def _heartbeat_loop(
    job_id: str,
    lease_token: str,
    stop_event: threading.Event,
    lease_lost: threading.Event,
) -> None:
    while not stop_event.wait(settings.worker_heartbeat_seconds):
        try:
            if not renew_lease(job_id, lease_token):
                lease_lost.set()
                return
        except Exception:
            # A transient heartbeat database failure is retried. Final writes remain
            # fenced by the database lease deadline even if every renewal fails.
            logger.exception("job_heartbeat_failed", extra={"structured": {"job_id": job_id}})


def process(job_id: str, lease_token: str) -> bool:
    with SessionLocal.begin() as db:
        job = _owned_running_job(db, job_id, lease_token, unexpired=True)
        if job is None:
            return False
        if not authorization_is_current(db, job):
            fail_revoked_job(db, job)
            return True
    stop_event = threading.Event()
    lease_lost = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, lease_token, stop_event, lease_lost),
        name=f"job-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    result: dict | None = None
    permanent_error: PermanentJobError | None = None
    unexpected_error: Exception | None = None
    try:
        result = execute_job_handler(job)
    except PermanentJobError as error:
        permanent_error = error
    except Exception as error:
        logger.exception("job_handler_failed", extra={"structured": {"job_id": job_id}})
        unexpected_error = error
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=max(1.0, settings.worker_heartbeat_seconds))
    if lease_lost.is_set():
        return False
    if permanent_error is not None:
        return _finish_permanent_failure(job_id, lease_token, permanent_error)
    if unexpected_error is not None:
        return _finish_unexpected_failure(job_id, lease_token, unexpected_error)
    if result is None:
        return _finish_unexpected_failure(
            job_id,
            lease_token,
            RuntimeError("job handler returned no result"),
        )
    return _finish_success(job_id, lease_token, result)


def run() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker_started", extra={"structured": {"worker_id": worker_id}})
    while not stopping:
        claimed = claim_one()
        if claimed is not None:
            try:
                process(claimed.job_id, claimed.lease_token)
            except Exception:
                # A database or process-level failure is recovered when the lease expires.
                logger.exception(
                    "job_processing_failed",
                    extra={"structured": {"job_id": claimed.job_id}},
                )
        else:
            time.sleep(settings.worker_poll_seconds)
    logger.info("worker_stopped", extra={"structured": {"worker_id": worker_id}})


if __name__ == "__main__":
    run()
