from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from executive_ai_api.anspire import (
    ANSPIRE_ENDPOINT_URL,
    AnspireConfigurationError,
    decrypt_anspire_api_key,
    encrypt_anspire_api_key,
)
from executive_ai_api.business_tools import execute_business_tool
from executive_ai_api.capabilities import (
    CapabilityClaims,
    CapabilityError,
    issue_capability_token,
    verify_capability_token,
)
from executive_ai_api.config import Settings, get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.demo_dataset import build_demo_dataset
from executive_ai_api.ingestion import (
    IngestionError,
    rebuild_daily_snapshots,
    require_isolated_data_source,
    resolve_source_connection,
)
from executive_ai_api.models import (
    AuditEvent,
    DailySnapshot,
    DataDomainStatus,
    DataSource,
    DataSyncRun,
    Enterprise,
    FactFinanceCollection,
    FactOpportunity,
    FactTarget,
    ModelProviderConfig,
)
from executive_ai_api.routers import admin_data, admin_models
from executive_ai_api.source_contract import SOURCE_COLUMNS, source_domain_fingerprint
from tests.conftest import login


def _target_claims(seeded: dict, organization_ids: set[uuid.UUID]) -> CapabilityClaims:
    return CapabilityClaims(
        enterprise_id=seeded["enterprise_id"],
        user_id=seeded["users"]["other@example.com"],
        organization_unit_ids=frozenset(organization_ids),
        tools=frozenset({"get_target_completion"}),
        message_id=uuid.uuid4(),
        expires_at=2**31,
    )


def _seed_target_completion_facts(seeded: dict) -> None:
    source_updated_at = datetime(2026, 10, 2, tzinfo=UTC)
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="target-benchmark-source",
            display_name="目标基准数据",
            source_type="simulated_generator",
        )
        db.add(source)
        db.flush()
        sync_run = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="manual",
            status="completed",
        )
        db.add(sync_run)
        db.flush()

        def target(
            source_record_id: str,
            organization_unit_id: uuid.UUID,
            metric_code: str,
            metric_name: str,
            period_type: str,
            period_start: date,
            period_end: date,
            value: int,
        ) -> FactTarget:
            return FactTarget(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                sync_run_id=sync_run.id,
                organization_unit_id=organization_unit_id,
                source_record_id=source_record_id,
                metric_code=metric_code,
                metric_name=metric_name,
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                target_value=value,
                unit="元",
                source_system="benchmark",
                source_updated_at=source_updated_at,
                is_current=True,
            )

        def opportunity(
            source_record_id: str,
            organization_unit_id: uuid.UUID,
            *,
            status: str,
            amount: int,
            gross_profit: int,
            closed_date: date | None,
            expected_close_date: date,
            probability: int = 100,
        ) -> FactOpportunity:
            return FactOpportunity(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                sync_run_id=sync_run.id,
                organization_unit_id=organization_unit_id,
                source_record_id=source_record_id,
                opportunity_code=source_record_id,
                title=source_record_id,
                stage="赢单" if status == "won" else "合同谈判",
                status=status,
                probability=probability,
                expected_amount=amount,
                expected_gross_profit=gross_profit,
                created_date=date(2026, 1, 1),
                expected_close_date=expected_close_date,
                closed_date=closed_date,
                source_system="benchmark",
                source_updated_at=source_updated_at,
                is_current=True,
            )

        def collection(
            source_record_id: str,
            organization_unit_id: uuid.UUID,
            *,
            collected_amount: int,
            actual_collection_date: date,
        ) -> FactFinanceCollection:
            receivable_amount = collected_amount + 100
            return FactFinanceCollection(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                sync_run_id=sync_run.id,
                organization_unit_id=organization_unit_id,
                source_record_id=source_record_id,
                project_source_record_id=f"project-{source_record_id}",
                invoice_amount=receivable_amount,
                receivable_amount=receivable_amount,
                collected_amount=collected_amount,
                outstanding_amount=100,
                planned_collection_date=actual_collection_date,
                actual_collection_date=actual_collection_date,
                overdue_days=0,
                aging_bucket="未到期",
                status="partial",
                source_system="benchmark",
                source_updated_at=source_updated_at,
                is_current=True,
            )

        east = seeded["east_id"]
        west = seeded["west_id"]
        july_start = date(2026, 7, 1)
        july_end = date(2026, 7, 31)
        quarter_end = date(2026, 9, 30)
        db.add_all(
            [
                target(
                    "east-month-revenue",
                    east,
                    "signed_revenue",
                    "签约收入",
                    "month",
                    july_start,
                    july_end,
                    1000,
                ),
                target(
                    "east-month-collection",
                    east,
                    "collection",
                    "回款",
                    "month",
                    july_start,
                    july_end,
                    800,
                ),
                target(
                    "east-month-profit",
                    east,
                    "gross_profit",
                    "毛利",
                    "month",
                    july_start,
                    july_end,
                    400,
                ),
                target(
                    "east-month-pipeline",
                    east,
                    "weighted_pipeline",
                    "加权商机",
                    "month",
                    july_start,
                    july_end,
                    600,
                ),
                target(
                    "east-quarter-revenue",
                    east,
                    "quarterly_revenue",
                    "季度签约收入",
                    "quarter",
                    july_start,
                    quarter_end,
                    3000,
                ),
                target(
                    "west-month-revenue",
                    west,
                    "signed_revenue",
                    "签约收入",
                    "month",
                    july_start,
                    july_end,
                    5000,
                ),
                target(
                    "west-month-collection",
                    west,
                    "collection",
                    "回款",
                    "month",
                    july_start,
                    july_end,
                    4000,
                ),
                target(
                    "west-quarter-revenue",
                    west,
                    "quarterly_revenue",
                    "季度签约收入",
                    "quarter",
                    july_start,
                    quarter_end,
                    9000,
                ),
                opportunity(
                    "east-june-won",
                    east,
                    status="won",
                    amount=700,
                    gross_profit=175,
                    closed_date=date(2026, 6, 30),
                    expected_close_date=date(2026, 6, 30),
                ),
                opportunity(
                    "east-july-won",
                    east,
                    status="won",
                    amount=400,
                    gross_profit=100,
                    closed_date=date(2026, 7, 10),
                    expected_close_date=date(2026, 7, 10),
                ),
                opportunity(
                    "east-august-won",
                    east,
                    status="won",
                    amount=600,
                    gross_profit=150,
                    closed_date=date(2026, 8, 15),
                    expected_close_date=date(2026, 8, 15),
                ),
                opportunity(
                    "east-october-won",
                    east,
                    status="won",
                    amount=900,
                    gross_profit=225,
                    closed_date=date(2026, 10, 1),
                    expected_close_date=date(2026, 10, 1),
                ),
                opportunity(
                    "east-july-active",
                    east,
                    status="active",
                    amount=1000,
                    gross_profit=250,
                    closed_date=None,
                    expected_close_date=date(2026, 7, 20),
                    probability=50,
                ),
                opportunity(
                    "west-july-won",
                    west,
                    status="won",
                    amount=2500,
                    gross_profit=625,
                    closed_date=date(2026, 7, 11),
                    expected_close_date=date(2026, 7, 11),
                ),
                opportunity(
                    "west-august-won",
                    west,
                    status="won",
                    amount=1000,
                    gross_profit=250,
                    closed_date=date(2026, 8, 20),
                    expected_close_date=date(2026, 8, 20),
                ),
                collection(
                    "east-june-collection",
                    east,
                    collected_amount=700,
                    actual_collection_date=date(2026, 6, 30),
                ),
                collection(
                    "east-july-collection",
                    east,
                    collected_amount=200,
                    actual_collection_date=date(2026, 7, 12),
                ),
                collection(
                    "east-august-collection",
                    east,
                    collected_amount=300,
                    actual_collection_date=date(2026, 8, 12),
                ),
                collection(
                    "west-july-collection",
                    west,
                    collected_amount=1500,
                    actual_collection_date=date(2026, 7, 13),
                ),
                collection(
                    "west-august-collection",
                    west,
                    collected_amount=500,
                    actual_collection_date=date(2026, 8, 13),
                ),
            ]
        )


def test_demo_dataset_is_large_deterministic_and_relationally_valid() -> None:
    arguments = {
        "enterprise_id": "11111111-1111-4111-8111-111111111111",
        "dataset_version": "phase2-demo-v1",
        "reference_date": date(2026, 7, 26),
    }
    first = build_demo_dataset(**arguments)
    second = build_demo_dataset(**arguments)

    assert first.content_sha256 == second.content_sha256
    assert first.record_counts == {
        "organization_units": 6,
        "people": 45,
        "customers": 600,
        "opportunities": 3000,
        "deliveries": 800,
        "collections": 12000,
        "targets": 600,
    }
    assert first.validation == {
        "valid": True,
        "errors": [],
        "checks": {
            "counts": True,
            "referential_integrity": True,
            "financial_invariants": True,
            "date_invariants": True,
        },
    }
    assert all(
        collection["collected_amount"]
        <= collection["receivable_amount"]
        <= collection["invoice_amount"]
        for collection in first.collections
    )


def test_source_contract_whitelist_excludes_direct_identifiers() -> None:
    forbidden = {
        "phone",
        "mobile",
        "email",
        "address",
        "identity_number",
        "bank_account",
    }
    assert not forbidden.intersection(
        column for columns in SOURCE_COLUMNS.values() for column in columns
    )
    base = [{"source_record_id": "one", "amount": 10}]
    changed = [{"source_record_id": "one", "amount": 11}]
    assert source_domain_fingerprint(base) != source_domain_fingerprint(changed)


def test_external_customer_source_requires_verify_full_tls() -> None:
    with pytest.raises(ValidationError, match="sslmode=verify-full"):
        Settings(
            app_env="customer-template",
            app_mode="production",
            service_role="scheduler",
            source_database_url="postgresql://reader:secret@db.example/source",
        )
    settings = Settings(
        app_env="customer-template",
        app_mode="production",
        service_role="scheduler",
        source_database_url=("postgresql://reader:secret@db.example/source?sslmode=verify-full"),
    )
    assert settings.source_connection_mode == "external"


def test_data_source_resolves_its_own_secret_reference(monkeypatch, seeded) -> None:
    tenant_url = "postgresql://reader:tenant-secret@tenant-source:5432/sanitized"
    monkeypatch.setenv("SOURCE_DATABASE_URL_TENANT_A", tenant_url)
    settings = Settings(
        app_env="test",
        app_mode="demo",
        service_role="ingestion_worker",
        source_database_url="postgresql://reader:wrong-global@global-source:5432/source",
        source_connection_mode="internal",
    )
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="tenant-a-source",
            display_name="Tenant A sanitized source",
            source_type="customer_sanitized_database",
            schema_version="2.0",
            is_enabled=True,
            configuration_json={"schema": "tenant_source", "connection_mode": "internal"},
            secret_reference_key="SOURCE_DATABASE_URL_TENANT_A",
        )
        db.add(source)
        db.flush()
        resolved = resolve_source_connection(db, source, settings)

    assert resolved.database_url == tenant_url
    assert resolved.schema == "tenant_source"
    assert resolved.secret_reference_key == "SOURCE_DATABASE_URL_TENANT_A"
    assert "wrong-global" not in resolved.database_url


def test_external_data_source_reference_still_requires_verify_full_tls(monkeypatch, seeded) -> None:
    monkeypatch.setenv(
        "SOURCE_DATABASE_URL_CUSTOMER",
        "postgresql://reader:secret@customer-source.example:5432/sanitized",
    )
    settings = Settings(
        app_env="test",
        app_mode="demo",
        service_role="ingestion_worker",
        source_connection_mode="internal",
    )
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="customer-source",
            display_name="Customer sanitized source",
            source_type="customer_sanitized_database",
            schema_version="2.0",
            is_enabled=True,
            configuration_json={"connection_mode": "external"},
            secret_reference_key="SOURCE_DATABASE_URL_CUSTOMER",
        )
        db.add(source)
        db.flush()

        with pytest.raises(IngestionError) as exc_info:
            resolve_source_connection(db, source, settings)

    assert exc_info.value.code == "source_tls_required"


def test_data_source_cannot_downgrade_deployment_tls_mode(monkeypatch, seeded) -> None:
    monkeypatch.setenv(
        "SOURCE_DATABASE_URL_CUSTOMER",
        "postgresql://reader:secret@customer-source.example:5432/sanitized?sslmode=verify-full",
    )
    settings = Settings(
        app_env="test",
        app_mode="demo",
        service_role="ingestion_worker",
        source_connection_mode="external",
    )
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="customer-source",
            display_name="Customer sanitized source",
            source_type="customer_sanitized_database",
            schema_version="2.0",
            is_enabled=True,
            configuration_json={"connection_mode": "internal"},
            secret_reference_key="SOURCE_DATABASE_URL_CUSTOMER",
        )
        db.add(source)
        db.flush()

        with pytest.raises(IngestionError) as exc_info:
            resolve_source_connection(db, source, settings)

    assert exc_info.value.code == "source_connection_mode_downgrade_forbidden"


def test_enabled_data_sources_fail_closed_across_enterprises(seeded) -> None:
    with SessionLocal.begin() as db:
        other_enterprise = Enterprise(name="Other group", slug="other-group")
        db.add(other_enterprise)
        db.flush()
        first = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="first-source",
            display_name="First source",
            source_type="customer_sanitized_database",
            is_enabled=True,
            secret_reference_key="SOURCE_DATABASE_URL_FIRST",
        )
        second = DataSource(
            enterprise_id=other_enterprise.id,
            key="second-source",
            display_name="Second source",
            source_type="customer_sanitized_database",
            is_enabled=True,
            secret_reference_key="SOURCE_DATABASE_URL_SECOND",
        )
        db.add_all([first, second])
        db.flush()

        with pytest.raises(IngestionError) as exc_info:
            require_isolated_data_source(db, first)

    assert exc_info.value.code == "source_deployment_isolation_violation"


def test_admin_source_test_uses_selected_data_source(client, seeded, monkeypatch) -> None:
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="selected-source",
            display_name="Selected source",
            source_type="customer_sanitized_database",
            schema_version="2.0",
            is_enabled=True,
            secret_reference_key="SOURCE_DATABASE_URL_SELECTED",
        )
        db.add(source)
        db.flush()
        source_id = source.id

    captured: dict[str, object] = {}

    def fake_test_source_connection(data_source, *, db, settings=None):
        captured["source_id"] = data_source.id
        captured["db"] = db
        return {
            "ok": True,
            "schema_version": "2.0",
            "database_version": "17.0",
            "current_user": "source_reader",
            "read_only": True,
            "tls_active": True,
            "latest_batch_id": "batch-1",
            "source_data_as_of": datetime(2026, 7, 26, tzinfo=UTC),
            "duration_ms": 1,
        }

    monkeypatch.setattr(admin_data, "test_source_connection", fake_test_source_connection)
    session = login(client, "admin@example.com")
    response = client.post(
        f"/api/v1/admin/data-sources/{source_id}/test",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )

    assert response.status_code == 200, response.text
    assert captured["source_id"] == source_id
    assert captured["db"] is not None


def test_admin_sync_refuses_a_second_enabled_source(client, seeded) -> None:
    with SessionLocal.begin() as db:
        first = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="primary-source",
            display_name="Primary source",
            source_type="customer_sanitized_database",
            is_enabled=True,
            secret_reference_key="SOURCE_DATABASE_URL_PRIMARY",
        )
        second = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="accidental-source",
            display_name="Accidental source",
            source_type="customer_sanitized_database",
            is_enabled=True,
            secret_reference_key="SOURCE_DATABASE_URL_ACCIDENTAL",
        )
        db.add_all([first, second])
        db.flush()
        source_id = first.id

    session = login(client, "admin@example.com")
    response = client.post(
        f"/api/v1/admin/data-sources/{source_id}/sync",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "source_deployment_isolation_violation"


def test_capability_token_is_signed_scoped_and_tamper_evident() -> None:
    settings = Settings(
        app_env="test",
        app_mode="demo",
        service_role="worker",
        capability_hmac_key="capability-test-key-with-at-least-32-characters",
    )
    enterprise_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    message_id = uuid.uuid4()
    token = issue_capability_token(
        settings=settings,
        enterprise_id=enterprise_id,
        user_id=user_id,
        organization_unit_ids={organization_id},
        tools={"get_overall_business"},
        message_id=message_id,
    )
    claims = verify_capability_token(token, settings)
    assert claims.enterprise_id == enterprise_id
    assert claims.organization_unit_ids == {organization_id}
    with pytest.raises(CapabilityError, match="signature"):
        verify_capability_token(token[:-1] + ("A" if token[-1] != "A" else "B"), settings)


def test_business_tool_rejects_cross_scope_before_query() -> None:
    allowed = uuid.uuid4()
    forbidden = uuid.uuid4()
    claims = CapabilityClaims(
        enterprise_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        organization_unit_ids=frozenset({allowed}),
        tools=frozenset({"get_overall_business"}),
        message_id=uuid.uuid4(),
        expires_at=2**31,
    )
    with SessionLocal() as db, pytest.raises(CapabilityError, match="forbidden"):
        execute_business_tool(
            db,
            claims,
            "get_overall_business",
            {"organization_unit_ids": [str(forbidden)]},
        )


def test_target_completion_reports_domain_not_configured(seeded) -> None:
    _seed_target_completion_facts(seeded)
    claims = _target_claims(seeded, {seeded["east_id"]})

    with SessionLocal() as db:
        result = execute_business_tool(
            db,
            claims,
            "get_target_completion",
            {
                "organization_unit_ids": [str(seeded["east_id"])],
                "period_type": "month",
                "period_start": "2026-07-01",
            },
        )

    assert result["data"] == {
        "availability": "not_configured",
        "message": "目标数据尚未接入",
        "metrics": [],
    }
    assert result["evidence"][0]["status"] == "not_configured"


def test_target_completion_ignores_stale_target_facts(seeded) -> None:
    _seed_target_completion_facts(seeded)
    claims = _target_claims(seeded, {seeded["east_id"]})

    with SessionLocal() as db:
        result = execute_business_tool(
            db,
            claims,
            "get_target_completion",
            {
                "organization_unit_ids": [str(seeded["east_id"])],
                "period_type": "quarter",
                "period_start": "2026-07-01",
            },
        )

    assert result["data"]["availability"] == "not_configured"
    assert result["data"]["metrics"] == []


def test_target_completion_preserves_requested_organization_scope(seeded) -> None:
    _seed_target_completion_facts(seeded)
    claims = _target_claims(seeded, {seeded["east_id"], seeded["west_id"]})

    with SessionLocal() as db:
        east_result = execute_business_tool(
            db,
            claims,
            "get_target_completion",
            {
                "organization_unit_ids": [str(seeded["east_id"])],
                "period_type": "month",
                "period_start": "2026-07-01",
            },
        )
        all_result = execute_business_tool(
            db,
            claims,
            "get_target_completion",
            {
                "organization_unit_ids": [str(seeded["east_id"]), str(seeded["west_id"])],
                "period_type": "month",
                "period_start": "2026-07-01",
            },
        )

    assert east_result["scope"]["organization_unit_ids"] == [str(seeded["east_id"])]
    assert set(all_result["scope"]["organization_unit_ids"]) == {
        str(seeded["east_id"]),
        str(seeded["west_id"]),
    }


def test_data_capabilities_exposes_domain_freshness(client, seeded) -> None:
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="test-source",
            display_name="演示模拟数据",
            source_type="simulated_generator",
            is_enabled=True,
        )
        db.add(source)
        db.flush()
        sync_run = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="manual",
            status="completed",
        )
        db.add(sync_run)
        db.flush()
        db.add(
            DataDomainStatus(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                domain="opportunity",
                status="fresh",
                active_sync_run_id=sync_run.id,
                record_count=3000,
                source_type="simulated_generator",
                source_display_name="演示模拟数据",
            )
        )
    session = login(client, "other@example.com")
    response = client.get(
        "/api/v1/data-capabilities",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source_kind"] == "simulated_generator"
    assert payload["overall_status"] == "fresh"
    assert payload["capabilities"]["pipeline"] is True


def test_daily_snapshot_preserves_each_domain_freshness(seeded) -> None:
    older = datetime(2026, 7, 25, 18, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="mixed-snapshot-source",
            display_name="演示模拟数据",
            source_type="simulated_generator",
        )
        db.add(source)
        db.flush()
        for index, domain in enumerate(("opportunity", "delivery", "collection", "target")):
            db.add(
                DataDomainStatus(
                    enterprise_id=seeded["enterprise_id"],
                    data_source_id=source.id,
                    domain=domain,
                    status="stale" if domain == "collection" else "fresh",
                    source_data_as_of=older if domain == "collection" else newer,
                    dataset_version="dataset-old" if domain == "collection" else "dataset-new",
                    source_type="simulated_generator",
                    source_display_name="演示模拟数据",
                    record_count=index,
                )
            )

    rebuild_daily_snapshots(
        enterprise_id=seeded["enterprise_id"],
        reference_date=date(2026, 7, 27),
        dataset_version="incoming-dataset",
        source_data_as_of=newer,
    )

    with SessionLocal() as db:
        snapshot = db.scalar(
            select(DailySnapshot).where(
                DailySnapshot.enterprise_id == seeded["enterprise_id"],
                DailySnapshot.organization_unit_id.is_(None),
                DailySnapshot.snapshot_date == date(2026, 7, 27),
            )
        )
        assert snapshot is not None
        assert snapshot.source_data_as_of == older.replace(tzinfo=None)
        assert snapshot.dataset_version == "mixed-domain-versions"
        freshness = snapshot.metrics_json["_domain_freshness"]
        assert freshness["collection"]["status"] == "stale"
        assert (
            freshness["collection"]["source_data_as_of"] == older.replace(tzinfo=None).isoformat()
        )
        assert (
            freshness["opportunity"]["source_data_as_of"] == newer.replace(tzinfo=None).isoformat()
        )


def test_anspire_credentials_are_randomized_encrypted_and_tamper_evident() -> None:
    settings = Settings(app_env="test", app_mode="demo")
    enterprise_id = uuid.uuid4()
    api_key = "unit-test-anspire-key-encryption-123456"
    first = encrypt_anspire_api_key(api_key, enterprise_id=enterprise_id, settings=settings)
    second = encrypt_anspire_api_key(api_key, enterprise_id=enterprise_id, settings=settings)

    assert first.ciphertext != second.ciphertext
    assert first.nonce != second.nonce
    config = ModelProviderConfig(
        enterprise_id=enterprise_id,
        provider="anspire",
        endpoint_url=ANSPIRE_ENDPOINT_URL,
        model_id="doubao-seed-2-1-pro",
        api_key_ciphertext=first.ciphertext,
        api_key_nonce=first.nonce,
        api_key_hint=first.hint,
        encryption_key_version=first.key_version,
    )
    assert decrypt_anspire_api_key(config, settings) == api_key

    config.enterprise_id = uuid.uuid4()
    with pytest.raises(AnspireConfigurationError, match="完整性"):
        decrypt_anspire_api_key(config, settings)
    config.enterprise_id = enterprise_id

    config.api_key_ciphertext = ("A" if first.ciphertext[0] != "A" else "B") + first.ciphertext[1:]
    with pytest.raises(AnspireConfigurationError, match="完整性"):
        decrypt_anspire_api_key(config, settings)


def test_anspire_admin_flow_is_role_gated_fixed_and_audited(
    client,
    seeded,
    monkeypatch,
) -> None:
    executive = login(client, "other@example.com")
    denied = client.get(
        "/api/v1/admin/model-provider",
        headers={"X-CSRF-Token": executive["csrf_token"]},
    )
    assert denied.status_code == 403

    admin = login(client, "admin@example.com")
    headers = {"X-CSRF-Token": admin["csrf_token"]}
    initial = client.get("/api/v1/admin/model-provider", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["provider"] == "anspire"
    assert initial.json()["endpoint_url"] == ANSPIRE_ENDPOINT_URL
    assert initial.json()["model_id"] == "glm-5.2"
    assert initial.json()["is_configured"] is False
    catalog = {item["id"]: item for item in initial.json()["models"]}
    assert len(catalog) == 53
    assert catalog["gpt-5.6-sol"]["family"] == "GPT"
    assert catalog["claude-opus-4-8"]["family"] == "Claude"
    assert catalog["gemini-3.1-pro-preview"]["family"] == "Gemini"
    assert catalog["gpt-image-2"]["capability"] == "image"
    assert catalog["gpt-image-2"]["selectable"] is False
    assert catalog["text-embedding-v4"]["capability"] == "embedding"
    assert catalog["text-embedding-v4"]["selectable"] is False

    api_key = "unit-test-anspire-key-admin-flow-123456"
    saved = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={"model_id": "doubao-seed-2-1-pro", "api_key": api_key},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["api_key_masked"].endswith("3456")
    assert api_key not in saved.text
    assert saved.json()["last_test_status"] == "pending"

    with SessionLocal() as db:
        stored = db.scalar(
            select(ModelProviderConfig).where(
                ModelProviderConfig.enterprise_id == seeded["enterprise_id"]
            )
        )
        assert stored is not None
        assert api_key not in stored.api_key_ciphertext
        assert decrypt_anspire_api_key(stored, get_settings()) == api_key
        audit = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "admin.anspire_model_updated")
            .order_by(AuditEvent.created_at.desc())
        )
        assert audit is not None
        assert api_key not in json.dumps(audit.metadata_json)

    premature = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={"model_id": "doubao-seed-2-1-pro", "is_enabled": True},
    )
    assert premature.status_code == 409

    monkeypatch.setattr(
        admin_models,
        "test_anspire_provider",
        lambda _settings, provider_config: {
            "status": "success",
            "latency_ms": 42,
            "model": provider_config["model_id"],
        },
    )
    tested = client.post("/api/v1/admin/model-provider/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["latency_ms"] == 42

    enabled = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={"model_id": "doubao-seed-2-1-pro", "is_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["is_enabled"] is True

    arbitrary_gateway = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={
            "model_id": "doubao-seed-2-1-pro",
            "endpoint_url": "https://attacker.invalid/v1",
        },
    )
    assert arbitrary_gateway.status_code == 422
    invalid_model = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={"model_id": "gpt-arbitrary"},
    )
    assert invalid_model.status_code == 422
    incompatible_model = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={"model_id": "gpt-image-2"},
    )
    assert incompatible_model.status_code == 422
