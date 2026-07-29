from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import DailySnapshot, DataDomainStatus, DataSource, DataSyncRun
from executive_ai_api.security import utc_now
from tests.conftest import login, login_and_change_password


def _seed_sync_context(seeded: dict, *, batch_id: str) -> None:
    timestamp = utc_now()
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key=f"daily-brief-{batch_id}",
            display_name="经营数据源",
            source_type="customer_sanitized_database",
        )
        db.add(source)
        db.flush()
        sync_run = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="scheduled",
            status="completed",
            dataset_version="daily-brief-v1",
            source_schema_version="3.0",
            source_batch_id=batch_id,
            source_data_as_of=timestamp,
            atomic_activation_status="activated",
            activated_at=timestamp,
            completed_at=timestamp,
        )
        db.add(sync_run)
        db.flush()
        for domain in ("opportunity", "delivery", "collection", "target"):
            db.add(
                DataDomainStatus(
                    enterprise_id=seeded["enterprise_id"],
                    data_source_id=source.id,
                    domain=domain,
                    status="fresh",
                    active_sync_run_id=sync_run.id,
                    source_data_as_of=timestamp,
                    last_success_at=timestamp,
                    record_count=0,
                    dataset_version="daily-brief-v1",
                    source_type="customer_sanitized_database",
                    source_display_name="经营数据源",
                    current_source_batch_id=batch_id,
                )
            )


def test_daily_brief_count_is_derived_from_latest_snapshot(client, seeded) -> None:
    batch_id = "internal-source-batch-secret-20260729"
    _seed_sync_context(seeded, batch_id=batch_id)
    delivery_cutoff = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    collection_cutoff = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    with SessionLocal.begin() as db:
        statuses = db.scalars(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == seeded["enterprise_id"]
            )
        ).all()
        for status in statuses:
            if status.domain == "delivery":
                status.source_data_as_of = delivery_cutoff
            elif status.domain == "collection":
                status.source_data_as_of = collection_cutoff
            elif status.domain == "opportunity":
                status.status = "failed"
                status.source_data_as_of = datetime(2026, 7, 1, tzinfo=UTC)
            elif status.domain == "target":
                status.status = "not_configured"
                status.source_data_as_of = None
        db.add(
            DailySnapshot(
                enterprise_id=seeded["enterprise_id"],
                organization_unit_id=None,
                snapshot_date=date(2026, 7, 29),
                source_data_as_of=utc_now(),
                dataset_version="daily-brief-v1",
                source_batch_id=batch_id,
                metrics_json={
                    "delivery_delayed_count": 2,
                    "overdue_amount": 244000.0,
                    "overdue_record_count": 5,
                },
                anomalies_json=[],
            )
        )

    session = login(client, "other@example.com")
    response = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["attention_count"] == 2
    assert [item["rule_id"] for item in payload["items"]] == [
        "delivery_delayed",
        "collection_overdue",
    ]
    assert payload["items"][0]["affected_count"] == 2
    assert payload["items"][1]["affected_count"] == 5
    assert payload["items"][1]["amount"] == 244000.0
    assert payload["uses_enterprise_snapshot"] is True
    assert payload["readiness"] == "ready"
    assert datetime.fromisoformat(payload["data_as_of"]) == collection_cutoff.replace(tzinfo=None)
    assert payload["source_batch_id"].startswith("batch_")
    assert "internal" not in payload["source_batch_id"]
    assert "飞书" not in response.text

    with SessionLocal.begin() as db:
        snapshot = db.scalar(
            select(DailySnapshot).where(
                DailySnapshot.enterprise_id == seeded["enterprise_id"],
                DailySnapshot.organization_unit_id.is_(None),
                DailySnapshot.source_batch_id == batch_id,
            )
        )
        assert snapshot is not None
        snapshot.metrics_json = {
            "delivery_delayed_count": 0,
            "overdue_amount": 0.0,
            "overdue_record_count": 0,
        }

    refreshed = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["attention_count"] == 0
    assert refreshed.json()["items"] == []


def test_daily_brief_never_uses_enterprise_snapshot_for_partial_scope(client, seeded) -> None:
    batch_id = "scope-safe-batch"
    _seed_sync_context(seeded, batch_id=batch_id)
    timestamp = utc_now()
    with SessionLocal.begin() as db:
        db.add_all(
            [
                DailySnapshot(
                    enterprise_id=seeded["enterprise_id"],
                    organization_unit_id=None,
                    snapshot_date=date(2026, 7, 29),
                    source_data_as_of=timestamp,
                    dataset_version="daily-brief-v1",
                    source_batch_id=batch_id,
                    metrics_json={
                        "delivery_delayed_count": 99,
                        "overdue_amount": 9999999.0,
                    },
                    anomalies_json=[],
                ),
                DailySnapshot(
                    enterprise_id=seeded["enterprise_id"],
                    organization_unit_id=seeded["east_id"],
                    snapshot_date=date(2026, 7, 29),
                    source_data_as_of=timestamp,
                    dataset_version="daily-brief-v1",
                    source_batch_id=batch_id,
                    metrics_json={
                        "delivery_delayed_count": 1,
                        "overdue_amount": 0.0,
                    },
                    anomalies_json=[],
                ),
                DailySnapshot(
                    enterprise_id=seeded["enterprise_id"],
                    organization_unit_id=seeded["west_id"],
                    snapshot_date=date(2026, 7, 29),
                    source_data_as_of=timestamp,
                    dataset_version="daily-brief-v1",
                    source_batch_id=batch_id,
                    metrics_json={
                        "delivery_delayed_count": 0,
                        "overdue_amount": 500000.0,
                    },
                    anomalies_json=[],
                ),
            ]
        )

    session = login_and_change_password(client)
    response = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["uses_enterprise_snapshot"] is False
    assert payload["organization_unit_ids"] == [str(seeded["east_id"])]
    assert payload["attention_count"] == 1
    assert payload["items"][0]["rule_id"] == "delivery_delayed"
    assert payload["items"][0]["affected_count"] == 1

    forbidden = client.get(
        "/api/v1/daily-brief",
        params={"organization_unit_ids": str(seeded["west_id"])},
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"]["code"] == "data_scope_forbidden"

    disconnected = client.get(
        "/api/v1/daily-brief",
        params={"organization_unit_ids": str(seeded["pending_id"])},
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert disconnected.status_code == 403, disconnected.text
    assert disconnected.json()["error"]["code"] == "data_scope_forbidden"


def test_daily_brief_zero_items_can_be_partial_or_unavailable(client, seeded) -> None:
    batch_id = "zero-items-batch"
    _seed_sync_context(seeded, batch_id=batch_id)
    with SessionLocal.begin() as db:
        collection = db.scalar(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == seeded["enterprise_id"],
                DataDomainStatus.domain == "collection",
            )
        )
        assert collection is not None
        collection.status = "failed"
        db.add(
            DailySnapshot(
                enterprise_id=seeded["enterprise_id"],
                organization_unit_id=None,
                snapshot_date=date(2026, 7, 29),
                source_data_as_of=utc_now(),
                dataset_version="daily-brief-v1",
                source_batch_id=batch_id,
                metrics_json={
                    "delivery_delayed_count": 0,
                    "overdue_amount": 0.0,
                },
                anomalies_json=[],
            )
        )

    session = login(client, "other@example.com")
    partial = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["readiness"] == "partial"
    assert partial.json()["attention_count"] == 0
    assert partial.json()["items"] == []

    with SessionLocal.begin() as db:
        db.execute(
            delete(DailySnapshot).where(
                DailySnapshot.enterprise_id == seeded["enterprise_id"]
            )
        )

    unavailable = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["readiness"] == "unavailable"
    assert unavailable.json()["attention_count"] == 0
    assert unavailable.json()["items"] == []


def test_daily_brief_data_as_of_falls_back_to_snapshot(client, seeded) -> None:
    batch_id = "snapshot-cutoff-fallback"
    snapshot_cutoff = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    _seed_sync_context(seeded, batch_id=batch_id)
    with SessionLocal.begin() as db:
        relevant_statuses = db.scalars(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == seeded["enterprise_id"],
                DataDomainStatus.domain.in_(("delivery", "collection")),
            )
        ).all()
        for status in relevant_statuses:
            status.source_data_as_of = None
        db.add(
            DailySnapshot(
                enterprise_id=seeded["enterprise_id"],
                organization_unit_id=None,
                snapshot_date=date(2026, 7, 29),
                source_data_as_of=snapshot_cutoff,
                dataset_version="daily-brief-v1",
                source_batch_id=batch_id,
                metrics_json={
                    "delivery_delayed_count": 0,
                    "overdue_amount": 0.0,
                },
                anomalies_json=[],
            )
        )

    session = login(client, "other@example.com")
    response = client.get(
        "/api/v1/daily-brief",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    assert datetime.fromisoformat(response.json()["data_as_of"]) == snapshot_cutoff.replace(
        tzinfo=None
    )
