from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from executive_ai_api import ingestion
from executive_ai_api.config import get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.ingestion import ResolvedSourceConnection
from executive_ai_api.models import (
    DataSource,
    DataSyncRun,
    Job,
    OpportunityExperienceWeightPolicy,
)
from tests.conftest import login

SOURCE_APP_TOKEN = "app_token_that_must_never_be_returned"


def _seed_feishu_v3_source_and_runs(seeded: dict) -> dict:
    now = datetime(2026, 7, 28, 2, 6, tzinfo=UTC)
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="feishu-operating-data-v3",
            display_name="飞书经营数据三表",
            source_type="feishu_three_table",
            schema_version="3.0",
            is_enabled=True,
            configuration_json={
                "activation_policy": "all_three_atomic",
                "tables": {
                    "opportunity": {
                        "app_token": SOURCE_APP_TOKEN,
                        "table_id": "tbl_opportunity",
                    },
                    "delivery": {
                        "app_token": SOURCE_APP_TOKEN,
                        "table_id": "tbl_delivery",
                    },
                    "collection": {
                        "app_token": SOURCE_APP_TOKEN,
                        "table_id": "tbl_collection",
                    },
                },
            },
            secret_reference_key="FEISHU_APP_SECRET",
        )
        db.add(source)
        db.flush()

        successful = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="manual",
            status="succeeded",
            source_schema_version="3.0",
            source_batch_id="batch-success-001",
            source_data_as_of=now,
            started_at=now - timedelta(minutes=2),
            completed_at=now,
            created_at=now - timedelta(minutes=2),
            records_read=172,
            records_written=172,
            source_schema_hashes_json={
                "opportunity": "schema-opportunity",
                "delivery": "schema-delivery",
                "collection": "schema-collection",
            },
            source_record_counts_json={
                "opportunity": 100,
                "delivery": 18,
                "collection": 54,
            },
            source_content_hashes_json={
                "opportunity": "content-opportunity",
                "delivery": "content-delivery",
                "collection": "content-collection",
            },
            cross_table_validation_json={
                "status": "passed",
                "project_contract_amount": "5336000.00",
                "signed_amount": "5336000.00",
                "receivable_amount": "5336000.00",
                "collected_amount": "2385000.00",
                "outstanding_amount": "2951000.00",
            },
            activation_mode="all_three_atomic",
            atomic_activation_status="activated",
            activation_started_at=now - timedelta(seconds=15),
            activated_at=now,
        )
        rejected = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="schedule",
            status="rejected",
            source_schema_version="3.0",
            source_batch_id="batch-rejected-002",
            source_data_as_of=now + timedelta(days=1),
            started_at=now + timedelta(days=1),
            completed_at=now + timedelta(days=1, minutes=1),
            created_at=now + timedelta(days=1),
            records_read=171,
            records_written=0,
            records_rejected=1,
            source_schema_hashes_json={
                "opportunity": "schema-opportunity",
                "delivery": "schema-delivery",
                "collection": "schema-collection",
            },
            source_record_counts_json={
                "opportunity": 100,
                "delivery": 18,
                "collection": 53,
            },
            source_content_hashes_json={
                "opportunity": "content-opportunity-v2",
                "delivery": "content-delivery-v2",
                "collection": "content-collection-v2",
            },
            cross_table_validation_json={
                "status": "failed",
                "errors": [
                    {
                        "domain": "collection",
                        "code": "collection_amount_mismatch",
                        "message": "应收金额与已回款及未回款不平",
                    }
                ],
            },
            activation_mode="all_three_atomic",
            atomic_activation_status="rejected",
            error_code="cross_table_validation_failed",
            error_message="三表校验未通过",
        )
        db.add_all([successful, rejected])
        db.flush()
        return {
            "source_id": source.id,
            "successful_run_id": successful.id,
            "rejected_run_id": rejected.id,
        }


def test_data_operations_overview_reports_safe_three_table_status(client, seeded) -> None:
    rows = _seed_feishu_v3_source_and_runs(seeded)
    login(client, "admin@example.com")

    response = client.get("/api/v1/admin/data-operations/overview")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["source_id"] == str(rows["source_id"])
    assert source["schema_version"] == "3.0"
    assert source["activation_policy"] == "all_three_atomic"
    assert [binding["domain"] for binding in source["bindings"]] == [
        "opportunity",
        "delivery",
        "collection",
    ]
    assert [binding["record_count"] for binding in source["bindings"]] == [100, 18, 53]
    assert all(binding["configured"] for binding in source["bindings"])
    assert all(binding["fields"] for binding in source["bindings"])
    assert all(binding["app_token_masked"] == "app_…rned" for binding in source["bindings"])
    assert SOURCE_APP_TOKEN not in json.dumps(payload, ensure_ascii=False)

    assert source["latest_successful_run"]["id"] == str(rows["successful_run_id"])
    assert source["latest_successful_run"]["source_batch_id"] == "batch-success-001"
    assert source["latest_rejected_run"]["id"] == str(rows["rejected_run_id"])
    assert source["latest_rejected_run"]["source_batch_id"] == "batch-rejected-002"
    assert source["bindings"][2]["validation_status"] == "rejected"
    assert source["bindings"][2]["warnings"] == ["应收金额与已回款及未回款不平"]

    policy = payload["experience_weight_policy"]
    assert policy["version"] == 1
    assert policy["weights_json"] == {"high": 0.2, "medium": 0.1, "low": 0.05}
    assert policy["observation_windows_json"] == [30, 60, 90]


def test_validate_data_source_enqueues_non_activating_atomic_job(client, seeded) -> None:
    rows = _seed_feishu_v3_source_and_runs(seeded)
    session = login(client, "admin@example.com")

    response = client.post(
        f"/api/v1/admin/data-sources/{rows['source_id']}/validate",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )

    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(response.json()["job_id"]))
        assert job is not None
        assert job.job_type == "data.sync"
        assert job.status == "queued"
        assert job.payload_json["data_source_id"] == str(rows["source_id"])
        assert job.payload_json["trigger_type"] == "manual_validation"
        assert job.payload_json["validation_only"] is True
        assert job.payload_json["operation"] == "validate"
        assert job.payload_json["activation_mode"] == "all_three_atomic"


@pytest.mark.parametrize(
    "payload",
    [
        {"validation_only": True},
        {"operation": "validate"},
    ],
    ids=["current-validation-only-field", "legacy-operation-field"],
)
def test_validation_only_job_never_activates_or_marks_source_batch(
    monkeypatch, seeded, payload
) -> None:
    rows = _seed_feishu_v3_source_and_runs(seeded)
    captured: dict[str, object] = {}

    def fake_resolve_source_connection(*_args, **_kwargs):
        return ResolvedSourceConnection(
            database_url="postgresql://source.invalid/source",
            schema="executive_source_v3",
            schema_version="3.0",
            connection_mode="internal",
            secret_reference_key="SOURCE_DATABASE_URL",
        )

    def fake_run_data_sync(**kwargs):
        captured["validate_only"] = kwargs["validate_only"]
        return {"status": "validated", "source_batch_id": "batch-must-not-be-marked"}

    def fail_if_marked(**_kwargs):
        raise AssertionError("validation-only jobs must not mark a source batch activated")

    monkeypatch.setattr(ingestion, "resolve_source_connection", fake_resolve_source_connection)
    monkeypatch.setattr(ingestion, "run_data_sync", fake_run_data_sync)
    monkeypatch.setattr(ingestion, "mark_source_v3_batch_activated", fail_if_marked)
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "source_writer_database_url": SecretStr(
                "postgresql://source-writer.invalid/source"
            ),
        }
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        enterprise_id=seeded["enterprise_id"],
        payload_json={
            "data_source_id": str(rows["source_id"]),
            "trigger_type": "manual_validation",
            **payload,
        },
    )

    result = ingestion.run_data_sync_job(job, settings)

    assert result["status"] == "validated"
    assert captured["validate_only"] is True


def test_experience_weight_policy_update_creates_version_and_rejects_stale_write(
    client, seeded
) -> None:
    session = login(client, "admin@example.com")
    initial = client.get("/api/v1/admin/metric-policies/opportunity-experience-weight")
    assert initial.status_code == 200, initial.text
    assert initial.json()["version"] == 1

    update_payload = {
        "base_version": 1,
        "weights": {"high": 0.18, "medium": 0.09, "low": 0.04},
        "label": "90天观察口径调整",
        "notes": "仅用于经验权重观察，不代表赢单概率。",
    }
    updated = client.patch(
        "/api/v1/admin/metric-policies/opportunity-experience-weight",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json=update_payload,
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["weights_json"] == {
        "high": 0.18,
        "medium": 0.09,
        "low": 0.04,
    }
    with SessionLocal() as db:
        versions = db.scalars(
            select(OpportunityExperienceWeightPolicy)
            .where(
                OpportunityExperienceWeightPolicy.enterprise_id
                == seeded["enterprise_id"]
            )
            .order_by(OpportunityExperienceWeightPolicy.version)
        ).all()
        assert [(row.version, row.is_active) for row in versions] == [
            (1, False),
            (2, True),
        ]

    stale = client.patch(
        "/api/v1/admin/metric-policies/opportunity-experience-weight",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json=update_payload,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "experience_weight_policy_version_conflict"
    assert stale.json()["error"]["details"] == {"current_version": 2}


def test_experience_weight_policy_rejects_invalid_weight_order(client, seeded) -> None:
    session = login(client, "admin@example.com")

    response = client.patch(
        "/api/v1/admin/metric-policies/opportunity-experience-weight",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={
            "base_version": 1,
            "weights": {"high": 0.1, "medium": 0.2, "low": 0.05},
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    with SessionLocal() as db:
        assert db.scalar(select(OpportunityExperienceWeightPolicy)) is None
