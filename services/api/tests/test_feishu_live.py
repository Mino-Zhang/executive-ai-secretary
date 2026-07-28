from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from executive_ai_api.feishu import FeishuBitableClient
from executive_ai_api.feishu_live import (
    COLLECTION_FIELDS,
    DELIVERY_FIELDS,
    OPPORTUNITY_FIELDS,
    FeishuLiveSourceError,
    TableBinding,
    fetch_fixed_live_snapshot,
    live_snapshot_to_source_rows,
)


def test_bitable_client_requests_automatic_record_metadata(monkeypatch) -> None:
    client = FeishuBitableClient(
        app_id="app-id",
        app_secret="app-secret",
        app_token="app-token",
        table_id="table-id",
    )
    requests: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, **kwargs):
        requests.append((method, path, kwargs))
        return {"data": {"items": [{"record_id": "rec-1"}], "has_more": False}}

    monkeypatch.setattr(client, "_request", fake_request)
    try:
        assert client.iter_records(page_size=200) == [{"record_id": "rec-1"}]
    finally:
        client.close()

    assert requests == [
        (
            "GET",
            "/bitable/v1/apps/app-token/tables/table-id/records",
            {"params": {"page_size": 200, "automatic_fields": True}},
        )
    ]


class FakeClient:
    def __init__(self, fields: tuple, records: list[dict]) -> None:
        self.fields = fields
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def list_fields(self):
        return [
            {
                "field_id": item.field_id,
                "field_name": item.field_name,
                "type": item.field_type,
            }
            for item in self.fields
        ]

    def iter_records(self):
        return self.records


def _record(fields: tuple, values: dict[str, object], record_id: str) -> dict:
    return {
        "record_id": record_id,
        "last_modified_time": int(datetime(2026, 7, 28, tzinfo=UTC).timestamp() * 1000),
        "fields": {item.field_name: values.get(item.key) for item in fields},
    }


def _bindings() -> tuple[TableBinding, ...]:
    return (
        TableBinding("opportunity", "app-opp", "tbl-opp", "商机", OPPORTUNITY_FIELDS),
        TableBinding("delivery", "app-prj", "tbl-prj", "项目", DELIVERY_FIELDS),
        TableBinding("collection", "app-col", "tbl-col", "回款", COLLECTION_FIELDS),
    )


def _records() -> dict[str, list[dict]]:
    opportunity = {
        "opportunity_id": "OPP-1",
        "source_record_id": "legacy-1",
        "organization_name": "华东事业部",
        "opportunity_name": "客户甲｜企业知识助手",
        "customer_name": ["客户甲"],
        "customer_value_level": "高价值",
        "sales_owner": "销售甲",
        "presales_owners": "售前甲、售前乙",
        "reliability": "高",
        "stage": "赢单",
        "expected_amount": "100.00",
        "signed_amount": "100.00",
        "expected_close_date": 1785168000000,
        "products_services": "AI场景开发",
        "entered_date": 1782576000000,
        "latest_progress": "已签约",
        "industry": "工业制造",
        "is_archived": "否",
        "archived_date": None,
    }
    delivery = {
        "project_id": "PRJ-1",
        "opportunity_id": "OPP-1",
        "opportunity_name": opportunity["opportunity_name"],
        "customer_name": "客户甲",
        "organization_name": "华东事业部",
        "project_name": "客户甲企业知识助手项目",
        "project_manager": "项目经理甲",
        "delivery_owner": "交付甲",
        "status": "进行中",
        "risk_level": "正常",
        "contract_amount": "100.00",
        "recognized_revenue": "30.00",
        "gross_margin_rate": "0.35",
        "planned_start_date": 1785168000000,
        "planned_end_date": 1790784000000,
        "actual_start_date": 1785168000000,
        "actual_end_date": None,
        "current_milestone": "项目启动",
        "completion_rate": "0.3",
        "delay_days": "0",
        "latest_progress": ["按计划推进。"],
        "source_updated_date": 1785168000000,
    }
    collections = []
    for index, (receivable, collected, outstanding) in enumerate(
        (("30", "30", "0"), ("40", "10", "30"), ("30", "0", "30")),
        start=1,
    ):
        collections.append(
            {
                "collection_id": f"COL-{index}",
                "project_id": "PRJ-1",
                "opportunity_id": "OPP-1",
                "customer_name": "客户甲",
                "organization_name": "华东事业部",
                "payment_type": ("预付款", "进度款", "尾款")[index - 1],
                "payment_milestone": ("合同签署款", "里程碑交付款", "终验尾款")[index - 1],
                "receivable_amount": receivable,
                "planned_collection_date": 1785168000000 + index * 86_400_000,
                "actual_collection_date": 1785168000000 if collected != "0" else None,
                "collected_amount": collected,
                "outstanding_amount": outstanding,
                "status": "已回款" if outstanding == "0" else "待回款",
                "overdue_days": "0",
                "aging_bucket": "未逾期",
                "invoice_status": "已开票" if collected != "0" else "待开票",
                "invoice_number": f"INV-{index}" if collected != "0" else None,
                "collection_owner": "销售甲",
                "latest_follow_up": ["按节点跟进。"],
                "source_updated_date": 1785168000000,
            }
        )
    return {
        "opportunity": [_record(OPPORTUNITY_FIELDS, opportunity, "rec-opp")],
        "delivery": [_record(DELIVERY_FIELDS, delivery, "rec-prj")],
        "collection": [
            _record(COLLECTION_FIELDS, value, f"rec-col-{index}")
            for index, value in enumerate(collections, start=1)
        ],
    }


def test_fixed_live_snapshot_validates_all_three_tables() -> None:
    bindings = _bindings()
    records = _records()
    by_domain = {binding.domain: binding for binding in bindings}

    snapshot = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(
            by_domain[binding.domain].fields, records[binding.domain]
        ),
    )

    assert snapshot.record_counts == {
        "opportunities": 1,
        "deliveries": 1,
        "collections": 3,
    }
    assert snapshot.validation["totals"] == {
        "receivable_amount": "100",
        "collected_amount": "40",
        "outstanding_amount": "60",
    }
    assert [item["domain"] for item in snapshot.validation["warnings"]] == [
        "opportunity",
        "delivery",
        "collection",
    ]
    assert len(snapshot.content_sha256) == 64


def test_fixed_live_snapshot_hash_ignores_feishu_record_order() -> None:
    bindings = _bindings()
    records = _records()
    first = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
    )
    reordered = {domain: list(reversed(values)) for domain, values in records.items()}
    second = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, reordered[binding.domain]),
    )

    assert first.content_sha256 == second.content_sha256
    assert [row["collection_id"] for row in second.collections] == ["COL-1", "COL-2", "COL-3"]


def test_fixed_live_snapshot_hash_ignores_automatic_record_metadata() -> None:
    bindings = _bindings()
    first_records = _records()
    second_records = _records()
    for rows in second_records.values():
        for record in rows:
            record["last_modified_time"] += 60_000

    first = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, first_records[binding.domain]),
    )
    second = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, second_records[binding.domain]),
    )

    assert first.content_sha256 == second.content_sha256


def test_fixed_live_snapshot_hash_is_stable_when_automatic_metadata_is_missing() -> None:
    bindings = _bindings()
    records = _records()
    for rows in records.values():
        for record in rows:
            record.pop("last_modified_time")

    first = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
    )
    second = fetch_fixed_live_snapshot(
        bindings,
        client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
    )

    assert first.content_sha256 == second.content_sha256


def test_fixed_live_snapshot_rejects_field_id_drift() -> None:
    bindings = _bindings()
    records = _records()

    def factory(binding: TableBinding):
        fields = binding.fields
        if binding.domain == "opportunity":
            fields = fields[1:]
        return FakeClient(fields, records[binding.domain])

    with pytest.raises(FeishuLiveSourceError) as error:
        fetch_fixed_live_snapshot(bindings, client_factory=factory)

    assert error.value.code == "feishu_schema_drift"


def test_fixed_live_snapshot_allows_display_name_change_when_field_id_is_stable() -> None:
    bindings = _bindings()
    records = _records()

    def factory(binding: TableBinding):
        fields = list(binding.fields)
        if binding.domain == "opportunity":
            original = fields[3]
            fields[3] = type(original)(
                original.key,
                original.field_id,
                "商机名称（演示）",
                original.field_type,
                original.required,
            )
            record = records[binding.domain][0]
            record["fields"]["商机名称（演示）"] = record["fields"].pop("商机名称")
        return FakeClient(tuple(fields), records[binding.domain])

    snapshot = fetch_fixed_live_snapshot(bindings, client_factory=factory)
    assert snapshot.record_counts["opportunities"] == 1


def test_fixed_live_snapshot_rejects_broken_financial_equation() -> None:
    bindings = _bindings()
    records = _records()
    records["collection"][0]["fields"]["未回款金额"] = "1"

    with pytest.raises(FeishuLiveSourceError) as error:
        fetch_fixed_live_snapshot(
            bindings,
            client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
        )

    assert error.value.code == "feishu_financial_invariant_failed"


def test_fixed_live_snapshot_reconstructs_customer_split_inside_parentheses() -> None:
    records = _records()
    records["opportunity"][0]["fields"]["客户名称"] = ["法国电信（Orange", "新加坡）"]
    records["delivery"][0]["fields"]["客户名称"] = "法国电信（Orange，新加坡）"
    for collection in records["collection"]:
        collection["fields"]["客户名称"] = "法国电信（Orange，新加坡）"

    snapshot = fetch_fixed_live_snapshot(
        _bindings(),
        client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
    )

    assert snapshot.opportunities[0]["customer_name"] == "法国电信（Orange，新加坡）"


def test_fixed_live_snapshot_still_rejects_genuine_multiple_customers() -> None:
    records = _records()
    records["opportunity"][0]["fields"]["客户名称"] = ["客户甲", "客户乙"]

    with pytest.raises(FeishuLiveSourceError) as error:
        fetch_fixed_live_snapshot(
            _bindings(),
            client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
        )

    assert error.value.code == "feishu_customer_cardinality_invalid"


def test_live_snapshot_maps_three_tables_to_one_conservative_source_batch() -> None:
    records = _records()
    snapshot = fetch_fixed_live_snapshot(
        _bindings(),
        client_factory=lambda binding: FakeClient(binding.fields, records[binding.domain]),
    )

    rows = live_snapshot_to_source_rows(snapshot, batch_id="batch-1")

    assert [len(rows[key]) for key in ("opportunities", "deliveries", "collections")] == [
        1,
        1,
        3,
    ]
    assert rows["opportunities"][0]["probability"] == 20
    assert rows["opportunities"][0]["organization_code"] == "east"
    assert rows["deliveries"][0]["completion_percent"] == 30
    assert rows["deliveries"][0]["gross_margin_rate"] == Decimal("0.35")
    assert rows["targets"] == []
    assert all(row["load_batch_id"] == "batch-1" for row in rows["collections"])
