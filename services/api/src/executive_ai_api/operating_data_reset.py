from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from .models import (
    AuditEvent,
    Conversation,
    DailySnapshot,
    DataDomainStatus,
    DataSource,
    DataSyncRun,
    DimCustomer,
    DimPerson,
    FactDelivery,
    FactFinanceCollection,
    FactOpportunity,
    FactOpportunityParticipant,
    FactOpportunityProduct,
    FactTarget,
    Job,
    Message,
    MessageEvidence,
    OrganizationUnit,
    Report,
    ScheduleRun,
    SourceCheckpoint,
)

LOCAL_RESET_CONFIRMATION = "CLEAR local-demo operating-data-v3"
VERIFIED_BACKUP_REFERENCE = re.compile(r"^verified-manifest-sha256:[0-9a-f]{64}$")
SOURCE_DOMAINS = ("opportunity", "delivery", "collection")


class OperatingDataResetError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperatingDataInventory:
    counts: dict[str, int]
    derived_conversation_ids: tuple[uuid.UUID, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "derived_conversation_count": len(self.derived_conversation_ids),
            "derived_conversation_ids": [str(value) for value in self.derived_conversation_ids],
        }


COUNT_MODELS = {
    "fact_opportunity": FactOpportunity,
    "fact_delivery": FactDelivery,
    "fact_finance_collection": FactFinanceCollection,
    "fact_target": FactTarget,
    "dim_customer": DimCustomer,
    "dim_person": DimPerson,
    "daily_snapshot": DailySnapshot,
    "reports": Report,
    "data_sync_runs": DataSyncRun,
    "data_domain_status": DataDomainStatus,
    "source_checkpoints": SourceCheckpoint,
}


def _unique(values: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted(set(values), key=str))


def inventory_operating_data(db: Session, enterprise_id: uuid.UUID) -> OperatingDataInventory:
    counts: dict[str, int] = {}
    for name, model in COUNT_MODELS.items():
        statement = select(func.count(model.id))
        if hasattr(model, "enterprise_id"):
            statement = statement.where(model.enterprise_id == enterprise_id)
        elif model is SourceCheckpoint:
            source_ids = select(DataSource.id).where(DataSource.enterprise_id == enterprise_id)
            statement = statement.where(SourceCheckpoint.data_source_id.in_(source_ids))
        counts[name] = int(db.scalar(statement) or 0)

    evidence_conversations = db.scalars(
        select(Message.conversation_id)
        .join(MessageEvidence, MessageEvidence.message_id == Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.enterprise_id == enterprise_id)
    ).all()
    fixture_conversations = [
        conversation.id
        for conversation in db.scalars(
            select(Conversation).where(Conversation.enterprise_id == enterprise_id)
        ).all()
        if conversation.title.startswith("演示：")
        or str((conversation.metadata_json or {}).get("fixture") or "").startswith("sanitized-")
    ]
    conversation_ids = _unique([*evidence_conversations, *fixture_conversations])
    counts["message_evidence"] = int(
        db.scalar(
            select(func.count(MessageEvidence.id))
            .join(Message, Message.id == MessageEvidence.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.enterprise_id == enterprise_id)
        )
        or 0
    )
    counts["derived_conversations"] = len(conversation_ids)
    return OperatingDataInventory(counts=counts, derived_conversation_ids=conversation_ids)


def reset_local_demo_operating_data(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    confirmation: str,
    backup_reference: str,
) -> OperatingDataInventory:
    if confirmation != LOCAL_RESET_CONFIRMATION:
        raise OperatingDataResetError(f"二次确认不匹配；必须精确输入 {LOCAL_RESET_CONFIRMATION!r}")
    normalized_backup_reference = backup_reference.strip().lower()
    if not VERIFIED_BACKUP_REFERENCE.fullmatch(normalized_backup_reference):
        raise OperatingDataResetError(
            "执行清理前必须由受保护切换脚本验证备份签名并传入 Manifest SHA-256"
        )

    # PostgreSQL owns the destructive production path.  Serialize resets per
    # enterprise and block concurrent data.sync inserts until this transaction
    # commits.  SQLite is retained only for unit tests.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"operating-data-v3-reset:{enterprise_id}"},
        )
        db.execute(text("LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE"))

    previous_reset = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.enterprise_id == enterprise_id,
            AuditEvent.action == "cli.local_demo_operating_data_v3_reset",
            AuditEvent.outcome == "success",
        )
    )
    if previous_reset is not None:
        raise OperatingDataResetError("该企业已完成一次 ODS 3.0 切换清理，禁止重复删除正式数据")
    active_sync_count = int(
        db.scalar(
            select(func.count(Job.id)).where(
                Job.enterprise_id == enterprise_id,
                Job.job_type == "data.sync",
                Job.status.in_(("queued", "running", "retrying")),
            )
        )
        or 0
    )
    if active_sync_count:
        raise OperatingDataResetError("仍有数据同步任务在排队或执行，已拒绝清理")

    source = db.scalar(
        select(DataSource)
        .where(DataSource.enterprise_id == enterprise_id, DataSource.is_enabled.is_(True))
        .order_by(DataSource.created_at)
        .with_for_update()
    )
    if source is None:
        raise OperatingDataResetError("当前企业没有启用的数据源，已拒绝清理")

    before = inventory_operating_data(db, enterprise_id)

    if before.derived_conversation_ids:
        db.execute(delete(Conversation).where(Conversation.id.in_(before.derived_conversation_ids)))
    db.execute(delete(Report).where(Report.enterprise_id == enterprise_id))
    db.execute(delete(DailySnapshot).where(DailySnapshot.enterprise_id == enterprise_id))

    # Explicit child deletion keeps this command deterministic even when a
    # local SQLite test database does not enable foreign-key cascades.
    db.execute(
        delete(FactOpportunityParticipant).where(
            FactOpportunityParticipant.enterprise_id == enterprise_id
        )
    )
    db.execute(
        delete(FactOpportunityProduct).where(FactOpportunityProduct.enterprise_id == enterprise_id)
    )
    db.execute(
        delete(FactFinanceCollection).where(FactFinanceCollection.enterprise_id == enterprise_id)
    )
    db.execute(delete(FactDelivery).where(FactDelivery.enterprise_id == enterprise_id))
    db.execute(delete(FactOpportunity).where(FactOpportunity.enterprise_id == enterprise_id))
    db.execute(delete(FactTarget).where(FactTarget.enterprise_id == enterprise_id))
    db.execute(delete(DimCustomer).where(DimCustomer.enterprise_id == enterprise_id))
    db.execute(delete(DimPerson).where(DimPerson.enterprise_id == enterprise_id))

    db.execute(delete(DataDomainStatus).where(DataDomainStatus.enterprise_id == enterprise_id))
    source_ids = select(DataSource.id).where(DataSource.enterprise_id == enterprise_id)
    db.execute(delete(SourceCheckpoint).where(SourceCheckpoint.data_source_id.in_(source_ids)))
    db.execute(delete(DataSyncRun).where(DataSyncRun.enterprise_id == enterprise_id))
    db.execute(delete(ScheduleRun).where(ScheduleRun.enterprise_id == enterprise_id))
    db.execute(delete(Job).where(Job.enterprise_id == enterprise_id, Job.job_type == "data.sync"))

    # Keep organization IDs because user grants and workspace projects refer to
    # them. They become analyzable again only after the V3 batch is activated.
    db.execute(
        update(OrganizationUnit)
        .where(OrganizationUnit.enterprise_id == enterprise_id)
        .values(data_connected=False, enabled_for_analysis=False)
    )

    for domain in SOURCE_DOMAINS:
        db.add(
            DataDomainStatus(
                enterprise_id=enterprise_id,
                data_source_id=source.id,
                domain=domain,
                status="never_synced",
                record_count=0,
                source_type=source.source_type,
                source_display_name=source.display_name,
                contract_version="3.0",
                status_reason="等待首个飞书三表 3.0 批次原子激活",
            )
        )
    db.add(
        DataDomainStatus(
            enterprise_id=enterprise_id,
            data_source_id=source.id,
            domain="target",
            status="not_configured",
            record_count=0,
            source_type=source.source_type,
            source_display_name=source.display_name,
            contract_version="3.0",
            status_reason="目标数据域尚未接入",
        )
    )
    db.add(
        AuditEvent(
            enterprise_id=enterprise_id,
            action="cli.local_demo_operating_data_v3_reset",
            target_type="enterprise",
            target_id=str(enterprise_id),
            outcome="success",
            metadata_json={
                "contract_version": "3.0",
                "backup_reference": normalized_backup_reference,
                "removed": before.counts,
                "preserved": [
                    "users",
                    "sessions",
                    "permissions",
                    "admin_configuration",
                    "model_configuration",
                    "harness_versions",
                    "mcp_configuration",
                    "audit_events",
                    "workspace_projects",
                ],
            },
        )
    )
    db.flush()
    return before


def render_inventory(inventory: OperatingDataInventory) -> str:
    return json.dumps(inventory.as_dict(), ensure_ascii=False, sort_keys=True, indent=2)
