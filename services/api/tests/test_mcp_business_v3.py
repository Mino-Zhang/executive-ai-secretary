from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from executive_ai_api.business_tools import execute_business_tool
from executive_ai_api.capabilities import CapabilityClaims
from executive_ai_api.database import SessionLocal
from executive_ai_api.mcp_registry import planner_catalog
from executive_ai_api.models import (
    DailySnapshot,
    DataSource,
    DataSyncRun,
    DimCustomer,
    DimPerson,
    FactDelivery,
    FactFinanceCollection,
    FactOpportunity,
    FactOpportunityParticipant,
    FactOpportunityProduct,
    OpportunityExperienceWeightPolicy,
)


def _seed_operating_v3(seeded: dict) -> CapabilityClaims:
    timestamp = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="operating-v3",
            display_name="飞书经营三表",
            source_type="feishu_three_table",
            schema_version="3.0",
        )
        db.add(source)
        db.flush()
        policy = OpportunityExperienceWeightPolicy(
            enterprise_id=seeded["enterprise_id"],
            version=3,
            label="董事长演示口径",
            weights_json={"high": 0.20, "medium": 0.10, "low": 0.05},
            observation_window_days=90,
            is_active=True,
            activated_at=timestamp,
        )
        db.add(policy)
        db.flush()
        sync_run = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="manual",
            status="completed",
            dataset_version="operating-v3-batch-1",
            source_schema_version="3.0",
            atomic_activation_status="activated",
            experience_weight_policy_id=policy.id,
            activated_at=timestamp,
        )
        db.add(sync_run)
        db.flush()
        customer = DimCustomer(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            organization_unit_id=seeded["east_id"],
            source_record_id="customer-1",
            display_name="云海智造",
            industry="智能制造",
            source_system="feishu",
            dataset_version=sync_run.dataset_version,
            source_updated_at=timestamp,
            synced_at=timestamp,
        )
        sales = DimPerson(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            organization_unit_id=seeded["east_id"],
            source_record_id="person-sales",
            display_name="陈销售",
            role_title="销售负责人",
            source_system="feishu",
            dataset_version=sync_run.dataset_version,
            source_updated_at=timestamp,
            synced_at=timestamp,
        )
        presales = DimPerson(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            organization_unit_id=seeded["east_id"],
            source_record_id="person-presales",
            display_name="周售前",
            role_title="售前负责人",
            source_system="feishu",
            dataset_version=sync_run.dataset_version,
            source_updated_at=timestamp,
            synced_at=timestamp,
        )
        db.add_all([customer, sales, presales])
        db.flush()

        def opportunity(
            code: str,
            *,
            status: str,
            reliability: str,
            expected: int,
            signed: int | None = None,
        ) -> FactOpportunity:
            return FactOpportunity(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                sync_run_id=sync_run.id,
                organization_unit_id=seeded["east_id"],
                customer_id=customer.id,
                owner_person_id=sales.id,
                source_record_id=code,
                opportunity_code=code,
                title=f"{code}-智能运营项目",
                stage="赢单" if status == "won" else "方案沟通",
                status=status,
                stage_label="赢单" if status == "won" else "方案沟通",
                status_code=status,
                reliability_level=reliability,
                customer_value_level="A",
                industry="智能制造",
                probability=None,
                expected_amount=expected,
                signed_amount=signed,
                expected_gross_profit=None,
                created_date=date(2026, 6, 1),
                expected_close_date=date(2026, 7, 31),
                closed_date=date(2026, 7, 20) if status == "won" else None,
                source_system="feishu",
                source_updated_at=timestamp,
                dataset_version=sync_run.dataset_version,
                is_current=True,
            )

        active_high = opportunity(
            "opp-active-high", status="active", reliability="高", expected=1000
        )
        active_medium = opportunity(
            "opp-active-medium", status="active", reliability="medium", expected=500
        )
        paused_high = opportunity(
            "opp-paused-high", status="paused", reliability="high", expected=5000
        )
        won = opportunity("opp-won", status="won", reliability="高", expected=900, signed=800)
        db.add_all([active_high, active_medium, paused_high, won])
        db.flush()
        db.add_all(
            [
                FactOpportunityParticipant(
                    enterprise_id=seeded["enterprise_id"],
                    sync_run_id=sync_run.id,
                    opportunity_id=active_high.id,
                    person_id=presales.id,
                    participant_role="pre_sales",
                ),
                FactOpportunityProduct(
                    enterprise_id=seeded["enterprise_id"],
                    sync_run_id=sync_run.id,
                    opportunity_id=active_high.id,
                    product_name="AI 场景开发",
                    normalized_product_name="ai 场景开发",
                ),
            ]
        )
        delivery = FactDelivery(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            sync_run_id=sync_run.id,
            organization_unit_id=seeded["east_id"],
            customer_id=customer.id,
            manager_person_id=presales.id,
            delivery_owner_person_id=presales.id,
            opportunity_fact_id=won.id,
            source_record_id="project-1",
            opportunity_source_record_id=won.source_record_id,
            project_code="project-1",
            project_name="云海智造智能运营交付",
            status="active",
            risk_level="attention",
            completion_percent=60,
            contract_amount=800,
            recognized_revenue=400,
            gross_margin_rate=0.25,
            planned_start_date=date(2026, 7, 1),
            planned_end_date=date(2026, 9, 30),
            actual_start_date=date(2026, 7, 2),
            current_milestone="联调",
            latest_progress="已完成首轮联调",
            source_system="feishu",
            source_updated_at=timestamp,
            dataset_version=sync_run.dataset_version,
            is_current=True,
        )
        db.add(delivery)
        db.flush()
        db.add(
            FactFinanceCollection(
                enterprise_id=seeded["enterprise_id"],
                data_source_id=source.id,
                sync_run_id=sync_run.id,
                organization_unit_id=seeded["east_id"],
                customer_id=customer.id,
                opportunity_fact_id=won.id,
                delivery_fact_id=delivery.id,
                collection_owner_person_id=sales.id,
                source_record_id="collection-1",
                project_source_record_id=delivery.source_record_id,
                receivable_amount=800,
                collected_amount=300,
                outstanding_amount=500,
                planned_collection_date=date(2026, 7, 15),
                actual_collection_date=date(2026, 7, 10),
                overdue_days=13,
                aging_bucket="1-30天",
                status="partial",
                payment_type="项目回款",
                payment_milestone="首付款",
                invoice_status="已开票",
                invoice_number="FP-001",
                latest_follow_up="客户承诺月底支付",
                source_system="feishu",
                source_updated_at=timestamp,
                dataset_version=sync_run.dataset_version,
                is_current=True,
            )
        )
    return CapabilityClaims(
        enterprise_id=seeded["enterprise_id"],
        user_id=seeded["users"]["other@example.com"],
        organization_unit_ids=frozenset({seeded["east_id"]}),
        tools=frozenset(
            {
                "get_overall_business",
                "get_opportunity_funnel",
                "get_sales_forecast",
                "get_delivery_status",
                "get_finance_margin",
                "get_collection_aging",
                "get_daily_changes",
            }
        ),
        message_id=uuid.uuid4(),
        expires_at=int(time.time()) + 60,
    )


def test_v3_forecast_uses_versioned_experience_weights_and_active_only(seeded) -> None:
    claims = _seed_operating_v3(seeded)
    with SessionLocal() as db:
        forecast = execute_business_tool(db, claims, "get_sales_forecast", {})
        filtered_funnel = execute_business_tool(
            db,
            claims,
            "get_opportunity_funnel",
            {
                "industries": ["智能制造"],
                "product_services": ["AI 场景"],
                "presales_owner_query": "周售前",
            },
        )
        catalog = planner_catalog(db, seeded["enterprise_id"])

    assert forecast["data"]["experience_weighted_forecast_amount"] == pytest.approx(250)
    assert forecast["data"]["won_signed_amount"] == pytest.approx(800)
    assert forecast["data"]["experience_weight_policy"] == {
        "id": forecast["data"]["experience_weight_policy"]["id"],
        "version": 3,
        "label": "董事长演示口径",
        "weights": {"high": 0.2, "medium": 0.1, "low": 0.05},
        "observation_window_days": 90,
        "source": "enterprise_configuration",
    }
    assert {item["source_record_id"] for item in forecast["data"]["opportunities"]} == {
        "opp-active-high",
        "opp-active-medium",
    }
    assert "probability" not in forecast["data"]["opportunities"][0]
    assert filtered_funnel["data"]["stages"] == [
        {
            "stage": "方案沟通",
            "count": 1,
            "amount": 1000.0,
            "experience_weighted_amount": 200.0,
            "signed_amount": 0.0,
        }
    ]
    assert "get_target_completion" not in {item["tool_name"] for item in catalog}


def test_v3_tools_keep_signed_contract_revenue_and_collection_distinct(seeded) -> None:
    claims = _seed_operating_v3(seeded)
    with SessionLocal() as db:
        overall = execute_business_tool(db, claims, "get_overall_business", {})
        delivery = execute_business_tool(db, claims, "get_delivery_status", {})
        finance = execute_business_tool(db, claims, "get_finance_margin", {})
        collection = execute_business_tool(db, claims, "get_collection_aging", {})

    assert overall["data"]["signed_amount"] == pytest.approx(800)
    assert overall["data"]["contract_amount"] == pytest.approx(800)
    assert overall["data"]["recognized_revenue"] == pytest.approx(400)
    assert overall["data"]["receivable_amount"] == pytest.approx(800)
    assert overall["data"]["collected_amount"] == pytest.approx(300)
    assert overall["data"]["outstanding_amount"] == pytest.approx(500)
    assert delivery["data"]["projects"][0]["recognized_revenue"] == pytest.approx(400)
    assert delivery["data"]["projects"][0]["delivery_owner"] == "周售前"
    assert finance["data"]["recognized_gross_profit_amount"] == pytest.approx(100)
    assert finance["data"]["gross_margin_rate"] == pytest.approx(0.25)
    assert collection["data"]["items"][0]["payment_milestone"] == "首付款"
    assert collection["data"]["items"][0]["invoice_status"] == "已开票"
    assert collection["data"]["items"][0]["collection_owner"] == "陈销售"


def test_v3_daily_changes_compare_adjacent_atomic_batches_on_the_same_day(seeded) -> None:
    claims = _seed_operating_v3(seeded)
    first_as_of = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    second_activation_at = first_as_of + timedelta(hours=6)
    # A newer full snapshot can legitimately have an older business cutoff
    # after the record carrying the previous max timestamp is deleted.
    second_source_as_of = first_as_of - timedelta(hours=1)
    with SessionLocal.begin() as db:
        source = db.scalar(
            select(DataSource).where(
                DataSource.enterprise_id == seeded["enterprise_id"],
                DataSource.key == "operating-v3",
            )
        )
        assert source is not None
        first_run = db.scalar(
            select(DataSyncRun).where(
                DataSyncRun.enterprise_id == seeded["enterprise_id"],
                DataSyncRun.dataset_version == "operating-v3-batch-1",
            )
        )
        assert first_run is not None
        first_run.source_batch_id = "source-batch-1"
        second_run = DataSyncRun(
            enterprise_id=seeded["enterprise_id"],
            data_source_id=source.id,
            trigger_type="scheduled",
            status="completed",
            dataset_version="operating-v3-batch-2",
            source_schema_version="3.0",
            source_batch_id="source-batch-2",
            atomic_activation_status="activated",
            activated_at=second_activation_at,
            completed_at=second_activation_at,
        )
        db.add(second_run)
        db.add_all(
            [
                DailySnapshot(
                    enterprise_id=seeded["enterprise_id"],
                    organization_unit_id=seeded["east_id"],
                    snapshot_date=date(2026, 7, 28),
                    source_data_as_of=first_as_of,
                    dataset_version="operating-v3-batch-1",
                    source_batch_id="source-batch-1",
                    metrics_json={"signed_amount": 800.0},
                    anomalies_json=[],
                ),
                DailySnapshot(
                    enterprise_id=seeded["enterprise_id"],
                    organization_unit_id=seeded["east_id"],
                    snapshot_date=date(2026, 7, 28),
                    source_data_as_of=second_source_as_of,
                    dataset_version="operating-v3-batch-2",
                    source_batch_id="source-batch-2",
                    metrics_json={"signed_amount": 900.0},
                    anomalies_json=[],
                ),
            ]
        )

    with SessionLocal() as db:
        result = execute_business_tool(db, claims, "get_daily_changes", {"days": 2})

    assert [item["source_batch_id"] for item in result["data"]["snapshots"]] == [
        "source-batch-2",
        "source-batch-1",
    ]
    assert result["data"]["changes"] == [
        {
            "organization_unit_id": str(seeded["east_id"]),
            "current_snapshot_date": "2026-07-28",
            "previous_snapshot_date": "2026-07-28",
            "current_source_batch_id": "source-batch-2",
            "previous_source_batch_id": "source-batch-1",
            "metric_deltas": {"signed_amount": 100.0},
        }
    ]
    assert {item["source_batch_id"] for item in result["evidence"]} == {
        "source-batch-1",
        "source-batch-2",
    }
