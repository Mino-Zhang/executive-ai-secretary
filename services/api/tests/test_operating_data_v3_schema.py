from __future__ import annotations

from sqlalchemy import func, select

from executive_ai_api.database import Base, SessionLocal
from executive_ai_api.metric_policy import ensure_default_opportunity_weight_policy
from executive_ai_api.models import (
    FactFinanceCollection,
    FactOpportunity,
    OpportunityExperienceWeightPolicy,
)


def test_operating_data_v3_metadata_contains_atomic_contract() -> None:
    expected_columns = {
        "data_sync_runs": {
            "source_schema_hashes_json",
            "source_record_counts_json",
            "source_content_hashes_json",
            "cross_table_validation_json",
            "activation_mode",
            "atomic_activation_status",
            "experience_weight_policy_id",
            "activation_started_at",
            "activated_at",
        },
        "fact_opportunity": {
            "upstream_record_id",
            "stage_label",
            "status_code",
            "reliability_level",
            "signed_amount",
            "is_archived",
            "archived_at",
            "latest_progress",
        },
        "fact_delivery": {
            "opportunity_fact_id",
            "delivery_owner_person_id",
            "recognized_revenue",
            "actual_start_date",
            "latest_progress",
        },
        "fact_finance_collection": {
            "opportunity_fact_id",
            "delivery_fact_id",
            "collection_owner_person_id",
            "payment_type",
            "payment_milestone",
            "invoice_status",
            "invoice_number",
            "latest_follow_up",
        },
    }

    for table_name, expected in expected_columns.items():
        actual = set(Base.metadata.tables[table_name].columns.keys())
        assert expected <= actual

    assert "fact_opportunity_participant" in Base.metadata.tables
    assert "fact_opportunity_product" in Base.metadata.tables
    assert FactOpportunity.__table__.c.probability.nullable is True
    assert FactOpportunity.__table__.c.expected_gross_profit.nullable is True
    assert FactFinanceCollection.__table__.c.invoice_amount.nullable is True


def test_default_experience_weight_policy_is_conservative_and_observed(seeded) -> None:
    with SessionLocal.begin() as db:
        policy = OpportunityExperienceWeightPolicy(
            enterprise_id=seeded["enterprise_id"],
            version=1,
            label="经验权重初始观察口径",
        )
        db.add(policy)
        db.flush()

        assert policy.weights_json == {"high": 0.20, "medium": 0.10, "low": 0.05}
        assert policy.observation_windows_json == [30, 60, 90]
        assert policy.observation_window_days == 90
        assert policy.is_active is True


def test_ensure_default_experience_weight_policy_is_idempotent(seeded) -> None:
    with SessionLocal.begin() as db:
        first = ensure_default_opportunity_weight_policy(db, seeded["enterprise_id"])
        first_id = first.id

    with SessionLocal.begin() as db:
        second = ensure_default_opportunity_weight_policy(db, seeded["enterprise_id"])
        count = db.scalar(
            select(func.count(OpportunityExperienceWeightPolicy.id)).where(
                OpportunityExperienceWeightPolicy.enterprise_id == seeded["enterprise_id"]
            )
        )

        assert second.id == first_id
        assert second.version == 1
        assert second.weights_json == {"high": 0.20, "medium": 0.10, "low": 0.05}
        assert second.observation_windows_json == [30, 60, 90]
        assert count == 1
