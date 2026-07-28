from __future__ import annotations

import json
import os
import uuid
from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from executive_ai_api import source_contract_v3
from executive_ai_api.feishu_live import (
    COLLECTION_FIELDS,
    DELIVERY_FIELDS,
    OPPORTUNITY_FIELDS,
    FieldBinding,
    TableBinding,
)
from executive_ai_api.operating_data_v3 import (
    OperatingDataV3Error,
    validate_business_v3_batch,
    validate_local_demo_first_cutover_fingerprint,
)
from executive_ai_api.source_contract import SourceContractError
from executive_ai_api.source_contract_v3 import (
    SOURCE_V3_COLUMNS,
    SOURCE_V3_FOREIGN_KEYS,
    SOURCE_V3_IMMUTABLE_TRIGGERS,
    SOURCE_V3_INDEXES,
    SOURCE_V3_PRIMARY_KEYS,
    SOURCE_V3_SCHEMA_VERSION,
    SOURCE_V3_SYSTEM,
    SOURCE_V3_UNIQUE_CONSTRAINTS,
    _expected_v3_column_contracts,
    inspect_source_v3_contract,
    latest_ready_source_v3_batch,
    prepare_source_v3_batch,
    source_v3_domain_fingerprint,
    table_schema_hashes,
    write_source_v3_batch,
)


def _bindings() -> tuple[TableBinding, ...]:
    return (
        TableBinding("opportunity", "app-opp", "tbl-opp", "商机总览", OPPORTUNITY_FIELDS),
        TableBinding("delivery", "app-del", "tbl-del", "项目交付", DELIVERY_FIELDS),
        TableBinding("collection", "app-col", "tbl-col", "财务回款", COLLECTION_FIELDS),
    )


def _snapshot(*, stage: str = "赢单", signed_amount: Decimal | None = Decimal("100")):
    updated_at = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
    opportunity = {
        "source_native_record_id": "rec-opp",
        "source_modified_at": updated_at,
        "opportunity_id": "OPP-1",
        "source_record_id": "legacy-1",
        "organization_name": "华东事业部",
        "opportunity_name": "客户甲丨企业知识助手",
        "customer_name": "客户甲",
        "customer_value_level": "高价值",
        "sales_owner": "销售甲",
        "presales_owners": "售前甲、售前乙",
        "reliability": "高",
        "stage": stage,
        "expected_amount": Decimal("100"),
        "signed_amount": signed_amount,
        "expected_close_date": date(2026, 8, 30),
        "products_services": "AI场景开发、企业知识库",
        "entered_date": date(2026, 5, 1),
        "latest_progress": "已签约",
        "industry": "工业制造",
        "is_archived": "否",
        "archived_date": None,
    }
    delivery = {
        "source_native_record_id": "rec-del",
        "source_modified_at": updated_at,
        "project_id": "PRJ-1",
        "opportunity_id": "OPP-1",
        "opportunity_name": opportunity["opportunity_name"],
        "customer_name": "客户甲",
        "organization_name": "华东事业部",
        "project_name": "客户甲企业知识助手项目",
        "project_manager": "项目经理甲",
        "delivery_owner": "交付甲、交付乙",
        "status": "进行中",
        "risk_level": "正常",
        "contract_amount": Decimal("100"),
        "recognized_revenue": Decimal("30"),
        "gross_margin_rate": Decimal("0.35"),
        "planned_start_date": date(2026, 7, 1),
        "planned_end_date": date(2026, 12, 1),
        "actual_start_date": date(2026, 7, 1),
        "actual_end_date": None,
        "current_milestone": "项目启动",
        "completion_rate": Decimal("0.3"),
        "delay_days": Decimal("0"),
        "latest_progress": "按计划推进。",
        "source_updated_date": date(2026, 7, 28),
    }
    collections = []
    for index, (receivable, collected, outstanding) in enumerate(
        (("30", "30", "0"), ("40", "10", "30"), ("30", "0", "30")), start=1
    ):
        collections.append(
            {
                "source_native_record_id": f"rec-col-{index}",
                "source_modified_at": updated_at,
                "collection_id": f"COL-{index}",
                "project_id": "PRJ-1",
                "opportunity_id": "OPP-1",
                "customer_name": "客户甲",
                "organization_name": "华东事业部",
                "payment_type": "进度款",
                "payment_milestone": f"付款节点{index}",
                "receivable_amount": Decimal(receivable),
                "planned_collection_date": date(2026, 8, index),
                "actual_collection_date": date(2026, 8, index) if collected != "0" else None,
                "collected_amount": Decimal(collected),
                "outstanding_amount": Decimal(outstanding),
                "status": "已回款" if outstanding == "0" else "待回款",
                "overdue_days": Decimal("0"),
                "aging_bucket": "未逾期",
                "invoice_status": "已开票" if collected != "0" else "待开票",
                "invoice_number": f"INV-{index}" if collected != "0" else None,
                "collection_owner": "销售甲",
                "latest_follow_up": "按节点跟进。",
                "source_updated_date": date(2026, 7, 28),
            }
        )
    return SimpleNamespace(
        fetched_at=updated_at,
        opportunities=(opportunity,),
        deliveries=(delivery,),
        collections=tuple(collections),
        content_sha256="a" * 64,
        validation={
            "valid": True,
            "record_counts": {"opportunities": 1, "deliveries": 1, "collections": 3},
        },
    )


def test_prepare_source_v3_batch_preserves_three_table_business_fields() -> None:
    prepared = prepare_source_v3_batch(_snapshot(), _bindings())

    assert prepared.record_counts == {"opportunity": 1, "delivery": 1, "collection": 3}
    assert prepared.validation["contract_version"] == SOURCE_V3_SCHEMA_VERSION
    assert prepared.reference_date == date(2026, 7, 28)
    assert len(set(prepared.table_content_sha256.values())) == 3

    opportunity = prepared.rows["opportunity"][0]
    assert opportunity["source_record_id"] == "OPP-1"
    assert opportunity["source_native_record_id"] == "rec-opp"
    assert opportunity["status_code"] == "won"
    assert opportunity["reliability_level"] == "high"
    assert opportunity["presales_owners"] == ["售前甲", "售前乙"]
    assert opportunity["products_services"] == ["AI场景开发", "企业知识库"]
    assert "probability" not in SOURCE_V3_COLUMNS["opportunity"]
    assert "expected_gross_profit" not in SOURCE_V3_COLUMNS["opportunity"]

    delivery = prepared.rows["delivery"][0]
    assert delivery["delivery_owners"] == ["交付甲", "交付乙"]
    assert delivery["recognized_revenue"] == Decimal("30")
    assert delivery["completion_rate"] == Decimal("0.3")

    collection = prepared.rows["collection"][1]
    assert collection["receivable_amount"] == Decimal("40")
    assert collection["collected_amount"] == Decimal("10")
    assert collection["outstanding_amount"] == Decimal("30")
    assert "invoice_amount" not in SOURCE_V3_COLUMNS["collection"]


def test_prepare_source_v3_batch_rejects_non_won_signed_amount() -> None:
    with pytest.raises(SourceContractError) as error:
        prepare_source_v3_batch(
            _snapshot(stage="商务报价", signed_amount=Decimal("100")), _bindings()
        )

    assert error.value.code == "source_non_won_amount_present"


def test_prepare_source_v3_batch_rejects_unknown_business_statuses() -> None:
    opportunity_snapshot = _snapshot()
    opportunity_snapshot.opportunities[0]["stage"] = "自定义阶段"
    with pytest.raises(SourceContractError) as opportunity_error:
        prepare_source_v3_batch(opportunity_snapshot, _bindings())
    assert opportunity_error.value.code == "source_opportunity_stage_invalid"

    delivery_snapshot = _snapshot()
    delivery_snapshot.deliveries[0]["status"] = "自定义状态"
    with pytest.raises(SourceContractError) as delivery_error:
        prepare_source_v3_batch(delivery_snapshot, _bindings())
    assert delivery_error.value.code == "source_delivery_status_invalid"


def test_v3_hashes_are_deterministic_and_field_id_sensitive() -> None:
    first = prepare_source_v3_batch(_snapshot(), _bindings())
    second = prepare_source_v3_batch(_snapshot(), _bindings())
    assert first.table_content_sha256 == second.table_content_sha256
    assert source_v3_domain_fingerprint(first.rows["opportunity"]) == (
        source_v3_domain_fingerprint(second.rows["opportunity"])
    )

    replay_identity = prepare_source_v3_batch(
        _snapshot(), _bindings(), batch_id="feishu-v3-another-event"
    )
    assert first.table_content_sha256 == replay_identity.table_content_sha256

    changed_fields = (
        *OPPORTUNITY_FIELDS[:-1],
        FieldBinding("archived_date", "new-id", "归档时间", 5, False),
    )
    changed_bindings = (
        TableBinding("opportunity", "app-opp", "tbl-opp", "商机总览", changed_fields),
        *_bindings()[1:],
    )
    assert (
        table_schema_hashes(_bindings())["opportunity"]
        != (table_schema_hashes(changed_bindings)["opportunity"])
    )
    changed = prepare_source_v3_batch(_snapshot(), changed_bindings)
    assert changed.batch_id != first.batch_id


def test_v3_domain_hash_ignores_source_record_order() -> None:
    snapshot = _snapshot()
    snapshot.collections = tuple(reversed(snapshot.collections))
    reordered = prepare_source_v3_batch(snapshot, _bindings())
    original = prepare_source_v3_batch(_snapshot(), _bindings())

    assert reordered.table_content_sha256 == original.table_content_sha256
    assert source_v3_domain_fingerprint(reordered.rows["collection"]) == (
        source_v3_domain_fingerprint(tuple(reversed(original.rows["collection"])))
    )


class _WriterCursor:
    def __init__(self, connection: _WriterConnection) -> None:
        self.connection = connection
        self.row: dict[str, object] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters=None):
        text = str(statement)
        parameters = tuple(parameters or ())
        self.row = None
        if "pg_advisory_xact_lock" in text:
            return self
        if "WHERE status IN ('ready', 'activated')" in text:
            usable = [
                row
                for row in self.connection.batches.values()
                if row["status"] in {"ready", "activated"}
            ]
            self.row = dict(max(usable, key=lambda row: int(row["order"]))) if usable else None
            return self
        if "FROM" in text and "source_batches WHERE batch_id = %s" in text:
            stored = self.connection.batches.get(str(parameters[0]))
            self.row = dict(stored) if stored is not None else None
            return self
        if "SELECT 1 FROM" in text and "source_batches WHERE batch_id = %s" in text:
            self.row = {"exists": 1} if str(parameters[0]) in self.connection.batches else None
            return self
        if "INSERT INTO" in text and "source_batches" in text:
            batch_id = str(parameters[0])
            if batch_id in self.connection.batches:
                return self
            self.connection.sequence += 1
            self.connection.batches[batch_id] = {
                "batch_id": batch_id,
                "source_system": str(parameters[1]),
                "dataset_version": str(parameters[2]),
                "reference_date": parameters[3],
                "source_data_as_of": parameters[4],
                "status": "building",
                "record_counts": json.loads(str(parameters[5])),
                "table_content_sha256": json.loads(str(parameters[6])),
                "table_schema_sha256": json.loads(str(parameters[7])),
                "content_sha256": str(parameters[8]),
                "validation_result": json.loads(str(parameters[9])),
                "created_at": self.connection.sequence,
                "completed_at": None,
                "order": self.connection.sequence,
            }
            self.row = {"batch_id": batch_id}
            return self
        if "UPDATE" in text and "SET status = 'ready'" in text:
            batch_id = str(parameters[0])
            self.connection.sequence += 1
            self.connection.batches[batch_id]["status"] = "ready"
            self.connection.batches[batch_id]["completed_at"] = self.connection.sequence
            self.connection.batches[batch_id]["order"] = self.connection.sequence
            return self
        if "source_table_bindings" in text or "source_sync_checkpoints" in text:
            return self
        raise AssertionError(f"unexpected writer query: {text}")

    def executemany(self, _statement, rows):
        for row in rows:
            self.connection.rows.append(dict(row))

    def fetchone(self):
        return self.row


class _WriterConnection:
    def __init__(self) -> None:
        self.batches: dict[str, dict[str, object]] = {}
        self.rows: list[dict[str, object]] = []
        self.sequence = 0

    def cursor(self):
        return _WriterCursor(self)

    def transaction(self):
        return nullcontext()

    def activate(self, batch_id: str) -> None:
        for row in self.batches.values():
            if row["status"] == "activated":
                row["status"] = "superseded"
        self.batches[batch_id]["status"] = "activated"


def test_source_v3_write_is_idempotent_but_a_historical_revert_is_a_new_batch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        source_contract_v3, "require_valid_source_v3_contract", lambda *_a, **_k: None
    )
    connection = _WriterConnection()
    first_prepared = prepare_source_v3_batch(_snapshot(), _bindings())

    first = write_source_v3_batch(  # type: ignore[arg-type]
        connection, first_prepared, _bindings(), dataset_version="demo-v3"
    )
    connection.activate(first["batch_id"])
    repeated = write_source_v3_batch(  # type: ignore[arg-type]
        connection, first_prepared, _bindings(), dataset_version="demo-v3"
    )
    assert repeated["batch_id"] == first["batch_id"]
    assert repeated["idempotent"] is True

    changed_snapshot = _snapshot()
    changed_snapshot.content_sha256 = "b" * 64
    changed_snapshot.opportunities[0]["latest_progress"] = "进入新的交付准备阶段。"
    changed_prepared = prepare_source_v3_batch(changed_snapshot, _bindings())
    changed = write_source_v3_batch(  # type: ignore[arg-type]
        connection, changed_prepared, _bindings(), dataset_version="demo-v3"
    )
    connection.activate(changed["batch_id"])

    reverted = write_source_v3_batch(  # type: ignore[arg-type]
        connection, first_prepared, _bindings(), dataset_version="demo-v3"
    )
    assert reverted["batch_id"] != first["batch_id"]
    assert reverted["batch_id"].endswith("-replay-000001")
    assert reverted["status"] == "ready"
    assert reverted["idempotent"] is False
    assert all(row["load_batch_id"] == reverted["batch_id"] for row in connection.rows[-5:])

    repeated_revert = write_source_v3_batch(  # type: ignore[arg-type]
        connection, first_prepared, _bindings(), dataset_version="demo-v3"
    )
    assert repeated_revert["batch_id"] == reverted["batch_id"]
    assert repeated_revert["idempotent"] is True
    assert latest_ready_source_v3_batch(connection)["batch_id"] == reverted["batch_id"]  # type: ignore[arg-type]


@pytest.mark.postgres
def test_source_v3_writer_and_latest_ready_query_execute_on_postgres(monkeypatch) -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL query test")

    schema = f"source_v3_latest_{uuid.uuid4().hex}"
    repository = Path(__file__).resolve().parents[3]
    ddl = (repository / "deploy/source-postgres/standard-ods-v3.sql").read_text()
    ddl = ddl.replace("executive_source_v3", schema)
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(ddl)

            monkeypatch.setattr(
                source_contract_v3, "require_valid_source_v3_contract", lambda *_a, **_k: None
            )
            first_prepared = prepare_source_v3_batch(_snapshot(), _bindings())
            first = write_source_v3_batch(
                connection,
                first_prepared,
                _bindings(),
                dataset_version="demo-v3",
                schema=schema,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.source_batches SET status = 'activated', "
                        "completed_at = now() - interval '2 minutes' WHERE batch_id = %s"
                    ).format(sql.Identifier(schema)),
                    (first["batch_id"],),
                )

            changed_snapshot = _snapshot()
            changed_snapshot.content_sha256 = "b" * 64
            changed_snapshot.opportunities[0]["latest_progress"] = "进入新的交付准备阶段。"
            changed_prepared = prepare_source_v3_batch(changed_snapshot, _bindings())
            changed = write_source_v3_batch(
                connection,
                changed_prepared,
                _bindings(),
                dataset_version="demo-v3",
                schema=schema,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.source_batches SET status = 'superseded' WHERE batch_id = %s"
                    ).format(sql.Identifier(schema)),
                    (first["batch_id"],),
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.source_batches SET status = 'activated', "
                        "completed_at = now() - interval '1 minute' WHERE batch_id = %s"
                    ).format(sql.Identifier(schema)),
                    (changed["batch_id"],),
                )

            reverted = write_source_v3_batch(
                connection,
                first_prepared,
                _bindings(),
                dataset_version="demo-v3",
                schema=schema,
            )
            repeated = write_source_v3_batch(
                connection,
                first_prepared,
                _bindings(),
                dataset_version="demo-v3",
                schema=schema,
            )
            assert reverted["batch_id"].endswith("-replay-000001")
            assert repeated["batch_id"] == reverted["batch_id"]
            assert repeated["idempotent"] is True
            assert (
                latest_ready_source_v3_batch(connection, schema=schema)["batch_id"]
                == reverted["batch_id"]
            )

            with connection.cursor() as cursor:
                checkpoints = cursor.execute(
                    sql.SQL(
                        "SELECT domain, last_batch_id FROM {}.source_sync_checkpoints "
                        "ORDER BY domain"
                    ).format(sql.Identifier(schema))
                ).fetchall()
            assert len(checkpoints) == 3
            assert {row["last_batch_id"] for row in checkpoints} == {reverted["batch_id"]}
        finally:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_business_v3_validation_preserves_exact_three_table_amounts() -> None:
    prepared = prepare_source_v3_batch(_snapshot(), _bindings())
    rows = {domain: list(values) for domain, values in prepared.rows.items()}
    validation = validate_business_v3_batch(
        rows,
        {
            "record_counts": prepared.record_counts,
            "batch_id": prepared.batch_id,
        },
    )

    assert validation["valid"] is True
    assert validation["source_system"] == SOURCE_V3_SYSTEM
    assert validation["amount_checks"] == {
        "signed_amount": "100",
        "contract_amount": "100",
        "receivable_amount": "100",
        "collected_amount": "40",
        "outstanding_amount": "60",
    }

    rows["collection"][0]["outstanding_amount"] = Decimal("1")
    with pytest.raises(OperatingDataV3Error) as error:
        validate_business_v3_batch(rows, {"record_counts": prepared.record_counts})
    assert error.value.code == "collection_amount_invariant_failed"


def test_business_v3_validation_rejects_mixed_source_systems() -> None:
    prepared = prepare_source_v3_batch(_snapshot(), _bindings())
    rows = {domain: list(values) for domain, values in prepared.rows.items()}
    rows["delivery"][0] = {**rows["delivery"][0], "source_system": "another_source"}

    with pytest.raises(OperatingDataV3Error) as error:
        validate_business_v3_batch(rows, {"record_counts": prepared.record_counts})

    assert error.value.code == "source_system_mismatch"


def test_local_demo_first_cutover_fingerprint_is_executable() -> None:
    valid = {
        "record_counts": {"opportunity": 100, "delivery": 18, "collection": 54},
        "amount_checks": {
            "signed_amount": "5336000",
            "contract_amount": "5336000",
            "receivable_amount": "5336000",
            "collected_amount": "2385000",
            "outstanding_amount": "2951000",
        },
    }
    assert validate_local_demo_first_cutover_fingerprint(valid)["status"] == "passed"

    changed = {**valid, "record_counts": {**valid["record_counts"], "collection": 53}}
    with pytest.raises(OperatingDataV3Error) as error:
        validate_local_demo_first_cutover_fingerprint(changed)
    assert error.value.code == "local_demo_first_cutover_count_mismatch"


def test_v3_sql_contract_is_batch_immutable() -> None:
    repository = Path(__file__).resolve().parents[3]
    sql_text = (repository / "deploy/source-postgres/standard-ods-v3.sql").read_text()

    assert "VALUES (true, '3.0-validating')" in sql_text
    assert "VALUES (true, '3.0')" in sql_text
    assert sql_text.index("VALUES (true, '3.0-validating')") < sql_text.index(
        "VALUES (true, '3.0')"
    )
    assert sql_text.count("UNIQUE (load_batch_id, source_record_id)") == 3
    assert "source_table_bindings" in sql_text
    assert "source_validation_issues" in sql_text
    assert "source_sync_checkpoints" in sql_text
    assert "reject_source_v3_snapshot_mutation" in sql_text
    assert "ODS 3.0 column contract invalid" in sql_text
    assert "ODS 3.0 key contract invalid" in sql_text
    assert "ODS 3.0 index contract invalid" in sql_text
    assert "ODS 3.0 immutable trigger contract invalid" in sql_text


class _CatalogCursor:
    def __init__(self, connection: _CatalogConnection) -> None:
        self.connection = connection
        self.rows: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters=None):
        text = str(statement)
        if text == "SHOW server_version":
            self.rows = [{"server_version": "17.5"}]
        elif "database_version_num" in text:
            self.rows = [
                {
                    "database_version_num": 170005,
                    "current_user": "source_reader",
                    "transaction_read_only": True,
                    "role_is_privileged": False,
                    "tls_active": True,
                }
            ]
        elif "AS formatted_type" in text:
            self.rows = self.connection.column_rows
        elif "pg_catalog.pg_constraint" in text:
            self.rows = self.connection.constraint_rows
        elif "pg_catalog.pg_index" in text:
            self.rows = self.connection.index_rows
        elif "pg_catalog.pg_trigger" in text:
            self.rows = self.connection.trigger_rows
        elif "ods_schema_version" in text:
            self.rows = [{"schema_version": SOURCE_V3_SCHEMA_VERSION}]
        else:  # pragma: no cover - makes catalog query additions fail loudly
            raise AssertionError(f"unexpected catalog query: {text}")
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _CatalogConnection:
    def __init__(self) -> None:
        self.column_rows = [
            {
                "table_name": table,
                "column_name": column,
                "formatted_type": formatted_type,
                "not_null": not_null,
            }
            for table, columns in _expected_v3_column_contracts().items()
            for column, (formatted_type, not_null) in columns.items()
        ]
        self.constraint_rows = [
            {
                "table_name": table,
                "constraint_type": "p",
                "is_valid": True,
                "is_ready": True,
                "columns": list(columns),
                "foreign_table_schema": None,
                "foreign_table_name": None,
                "foreign_columns": [],
            }
            for table, columns in SOURCE_V3_PRIMARY_KEYS.items()
        ]
        self.constraint_rows.extend(
            {
                "table_name": table,
                "constraint_type": "u",
                "is_valid": True,
                "is_ready": True,
                "columns": list(columns),
                "foreign_table_schema": None,
                "foreign_table_name": None,
                "foreign_columns": [],
            }
            for table, constraints in SOURCE_V3_UNIQUE_CONSTRAINTS.items()
            for columns in constraints
        )
        self.constraint_rows.extend(
            {
                "table_name": table,
                "constraint_type": "f",
                "is_valid": True,
                "is_ready": True,
                "columns": list(columns),
                "foreign_table_schema": "executive_source_v3",
                "foreign_table_name": foreign_table,
                "foreign_columns": list(foreign_columns),
            }
            for table, columns, foreign_table, foreign_columns in SOURCE_V3_FOREIGN_KEYS
        )
        self.index_rows = [
            {
                "index_name": index_name,
                "table_name": table,
                "columns": list(columns),
                "is_valid": True,
                "is_ready": True,
            }
            for index_name, (table, columns) in SOURCE_V3_INDEXES.items()
        ]
        self.trigger_rows = [
            {
                "table_name": table,
                "trigger_name": trigger_name,
                "function_schema": "executive_source_v3",
                "function_name": "reject_source_v3_snapshot_mutation",
                "enabled": "O",
                "row_level": True,
                "before_event": True,
                "delete_event": True,
                "update_event": True,
            }
            for table, trigger_name in SOURCE_V3_IMMUTABLE_TRIGGERS.items()
        ]

    def cursor(self):
        return _CatalogCursor(self)


def test_runtime_contract_inspection_rejects_catalog_drift() -> None:
    valid_connection = _CatalogConnection()
    valid = inspect_source_v3_contract(valid_connection)  # type: ignore[arg-type]
    assert valid.valid is True

    drifted_connection = _CatalogConnection()
    industry = next(
        row
        for row in drifted_connection.column_rows
        if row["table_name"] == "ods_opportunity" and row["column_name"] == "industry"
    )
    industry["formatted_type"] = "character varying(161)"
    drifted_connection.constraint_rows = [
        row
        for row in drifted_connection.constraint_rows
        if not (
            (row["constraint_type"] == "f" and row["table_name"] == "ods_delivery")
            or (row["constraint_type"] == "p" and row["table_name"] == "ods_collection")
            or (row["constraint_type"] == "u" and row["table_name"] == "ods_opportunity")
        )
    ]
    drifted_connection.index_rows = [
        row
        for row in drifted_connection.index_rows
        if row["index_name"] != "ix_source_v3_collection_batch"
    ]
    drifted_connection.trigger_rows = [
        row for row in drifted_connection.trigger_rows if row["table_name"] != "ods_opportunity"
    ]

    drifted = inspect_source_v3_contract(drifted_connection)  # type: ignore[arg-type]

    assert drifted.valid is False
    assert drifted.invalid_columns == {
        "ods_opportunity": (
            "industry: expected character varying(160) NOT NULL, "
            "got character varying(161) NOT NULL",
        )
    }
    assert drifted.missing_foreign_keys == (
        "ods_delivery(load_batch_id) -> source_batches(batch_id)",
    )
    assert drifted.missing_primary_keys == ("ods_collection(id)",)
    assert drifted.missing_unique_constraints == (
        "ods_opportunity(load_batch_id, source_record_id)",
    )
    assert drifted.missing_indexes == (
        "ix_source_v3_collection_batch:ods_collection("
        "load_batch_id, organization_code, planned_collection_date)",
    )
    assert drifted.invalid_immutable_triggers == ("trg_immutable_ods_opportunity:ods_opportunity",)
