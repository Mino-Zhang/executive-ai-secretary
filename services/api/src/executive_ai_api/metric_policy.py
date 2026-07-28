from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Enterprise, OpportunityExperienceWeightPolicy

DEFAULT_EXPERIENCE_WEIGHTS: dict[str, float] = {
    "high": 0.20,
    "medium": 0.10,
    "low": 0.05,
}
DEFAULT_OBSERVATION_WINDOWS: tuple[int, ...] = (30, 60, 90)


def ensure_default_opportunity_weight_policy(
    db: Session,
    enterprise_id: uuid.UUID,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> OpportunityExperienceWeightPolicy:
    """Return the active policy, creating the conservative v1 policy if absent.

    The enterprise row lock makes first-time bootstrap idempotent when multiple
    workers start concurrently.  If an enterprise already has version history
    but no active policy, the latest version is reactivated rather than
    silently replacing administrator-managed weights with the defaults.
    """

    # Serialize first-time creation without granting an ingestion worker write
    # access to the enterprise row.  PostgreSQL SELECT FOR UPDATE requires
    # UPDATE privilege on the locked table, which would widen the worker's
    # security boundary solely for coordination.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"opportunity-experience-weight-policy:{enterprise_id}"},
        )
    enterprise = db.scalar(select(Enterprise).where(Enterprise.id == enterprise_id))
    if enterprise is None:
        raise ValueError("enterprise_not_found")

    active = db.scalar(
        select(OpportunityExperienceWeightPolicy)
        .where(
            OpportunityExperienceWeightPolicy.enterprise_id == enterprise_id,
            OpportunityExperienceWeightPolicy.is_active.is_(True),
        )
        .order_by(OpportunityExperienceWeightPolicy.version.desc())
        .limit(1)
    )
    if active is not None:
        return active

    latest = db.scalar(
        select(OpportunityExperienceWeightPolicy)
        .where(OpportunityExperienceWeightPolicy.enterprise_id == enterprise_id)
        .order_by(OpportunityExperienceWeightPolicy.version.desc())
        .limit(1)
    )
    if latest is not None:
        latest.is_active = True
        latest.activated_at = datetime.now(UTC)
        db.flush()
        return latest

    policy = OpportunityExperienceWeightPolicy(
        enterprise_id=enterprise_id,
        version=1,
        label="经验权重初始观察口径",
        weights_json=dict(DEFAULT_EXPERIENCE_WEIGHTS),
        observation_windows_json=list(DEFAULT_OBSERVATION_WINDOWS),
        observation_window_days=max(DEFAULT_OBSERVATION_WINDOWS),
        is_active=True,
        activated_at=datetime.now(UTC),
        created_by_user_id=created_by_user_id,
        notes="固定初始口径：高20%、中10%、低5%；不代表真实赢单概率。",
    )
    db.add(policy)
    db.flush()
    return policy
