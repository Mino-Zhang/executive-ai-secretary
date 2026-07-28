from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import (
    AuditEvent,
    Conversation,
    DailySnapshot,
    DataDomainStatus,
    DataSource,
    OrganizationUnit,
)
from executive_ai_api.operating_data_reset import (
    LOCAL_RESET_CONFIRMATION,
    OperatingDataResetError,
    reset_local_demo_operating_data,
)

VERIFIED_BACKUP = "verified-manifest-sha256:" + "a" * 64


def _seed_operating_state(seeded: dict) -> None:
    now = datetime(2026, 7, 28, tzinfo=UTC)
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="demo-sanitized-source",
            display_name="飞书经营三表",
            source_type="feishu_three_table",
            schema_version="3.0",
            is_enabled=True,
            configuration_json={"schema": "executive_source_v3"},
            secret_reference_key="SOURCE_DATABASE_URL",
        )
        db.add(source)
        db.flush()
        db.add(
            DataDomainStatus(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                domain="target",
                status="fresh",
                record_count=600,
                source_type="simulated_generator",
                source_display_name="旧模拟数据",
            )
        )
        db.add(
            DailySnapshot(
                enterprise_id=seeded["enterprise_id"],
                snapshot_date=date(2026, 7, 28),
                source_data_as_of=now,
                dataset_version="phase2-demo-v1",
                metrics_json={"targets": {"revenue": 1}},
                anomalies_json=[],
            )
        )
        db.add(
            Conversation(
                enterprise_id=seeded["enterprise_id"],
                owner_user_id=seeded["users"]["executive@example.com"],
                title="演示：本月整体经营情况",
                metadata_json={"fixture": "sanitized-demo"},
            )
        )


def test_reset_requires_exact_confirmation(seeded: dict) -> None:
    _seed_operating_state(seeded)
    with SessionLocal.begin() as db, pytest.raises(OperatingDataResetError):
        reset_local_demo_operating_data(
            db,
            enterprise_id=seeded["enterprise_id"],
            confirmation="CLEAR",
            backup_reference=VERIFIED_BACKUP,
        )


def test_reset_preserves_source_and_rebuilds_empty_domain_states(seeded: dict) -> None:
    _seed_operating_state(seeded)
    with SessionLocal.begin() as db:
        removed = reset_local_demo_operating_data(
            db,
            enterprise_id=seeded["enterprise_id"],
            confirmation=LOCAL_RESET_CONFIRMATION,
            backup_reference=VERIFIED_BACKUP,
        )
        assert removed.counts["daily_snapshot"] == 1
        assert removed.counts["derived_conversations"] == 1

    with SessionLocal() as db:
        assert db.scalar(select(func.count(DataSource.id))) == 1
        assert db.scalar(select(func.count(DailySnapshot.id))) == 0
        assert db.scalar(select(func.count(Conversation.id))) == 0
        statuses = {
            item.domain: item
            for item in db.scalars(
                select(DataDomainStatus).where(
                    DataDomainStatus.enterprise_id == seeded["enterprise_id"]
                )
            ).all()
        }
        assert set(statuses) == {"opportunity", "delivery", "collection", "target"}
        assert statuses["target"].status == "not_configured"
        assert statuses["opportunity"].status == "never_synced"
        assert all(item.record_count == 0 for item in statuses.values())
        assert all(
            not item.data_connected and not item.enabled_for_analysis
            for item in db.scalars(
                select(OrganizationUnit).where(
                    OrganizationUnit.enterprise_id == seeded["enterprise_id"]
                )
            ).all()
        )
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "cli.local_demo_operating_data_v3_reset"
            )
        )
        assert event is not None
        assert event.metadata_json["contract_version"] == "3.0"
        assert event.metadata_json["backup_reference"] == VERIFIED_BACKUP


def test_reset_rejects_unverified_backup_reference(seeded: dict) -> None:
    _seed_operating_state(seeded)
    with SessionLocal.begin() as db, pytest.raises(OperatingDataResetError):
        reset_local_demo_operating_data(
            db,
            enterprise_id=seeded["enterprise_id"],
            confirmation=LOCAL_RESET_CONFIRMATION,
            backup_reference="/verified/backup",
        )


def test_reset_is_one_time_only(seeded: dict) -> None:
    _seed_operating_state(seeded)
    with SessionLocal.begin() as db:
        reset_local_demo_operating_data(
            db,
            enterprise_id=seeded["enterprise_id"],
            confirmation=LOCAL_RESET_CONFIRMATION,
            backup_reference=VERIFIED_BACKUP,
        )
    with SessionLocal.begin() as db, pytest.raises(OperatingDataResetError):
        reset_local_demo_operating_data(
            db,
            enterprise_id=seeded["enterprise_id"],
            confirmation=LOCAL_RESET_CONFIRMATION,
            backup_reference=VERIFIED_BACKUP,
        )
