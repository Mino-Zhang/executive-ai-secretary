from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from psycopg import sql
from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .demo_dataset import build_demo_dataset
from .models import Enterprise
from .source_contract import (
    SOURCE_COLUMNS,
    SOURCE_SCHEMA,
    SOURCE_TABLES,
    _upsert_rows,
    connect_source,
    require_valid_source_contract,
)

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_FIELDS = {
    "source_record_id": "源记录ID",
    "organization_code": "事业部编码",
    "customer_record_id": "客户ID",
    "owner_person_record_id": "负责人ID",
    "opportunity_code": "商机编号",
    "title": "商机名称",
    "stage": "商机阶段",
    "status": "商机状态",
    "probability": "赢单概率",
    "expected_amount": "预计金额",
    "expected_gross_profit": "预计毛利",
    "created_date": "创建日期",
    "expected_close_date": "预计签约日期",
    "closed_date": "关闭日期",
    "source_updated_at": "源更新时间",
}


class FeishuError(RuntimeError):
    pass


def _feishu_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class FeishuBitableClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        timeout_seconds: float = 30,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.client = httpx.Client(base_url=FEISHU_API_BASE, timeout=timeout_seconds)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> FeishuBitableClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _tenant_token(self) -> str:
        if self._token and self._token_expires_at > time.monotonic() + 60:
            return self._token
        response = self.client.post(
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("tenant_access_token"):
            raise FeishuError(f"飞书鉴权失败: {payload.get('msg', 'unknown error')}")
        self._token = str(payload["tenant_access_token"])
        self._token_expires_at = time.monotonic() + int(payload.get("expire", 7200))
        return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._tenant_token()}"
        response = self.client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuError(f"飞书接口失败: {payload.get('msg', 'unknown error')}")
        return payload

    @property
    def records_path(self) -> str:
        return f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"

    def iter_records(self, *, page_size: int = 500) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            payload = self._request("GET", self.records_path, params=params)
            data = payload.get("data") or {}
            records.extend(data.get("items") or [])
            if not data.get("has_more"):
                return records
            page_token = data.get("page_token")
            if not page_token:
                raise FeishuError("飞书分页响应缺少 page_token")

    def publish_opportunities(self, opportunities: list[dict[str, Any]]) -> dict[str, int]:
        existing = {
            str(record.get("fields", {}).get(FEISHU_FIELDS["source_record_id"])): str(
                record["record_id"]
            )
            for record in self.iter_records()
            if record.get("fields", {}).get(FEISHU_FIELDS["source_record_id"])
        }
        create_records: list[dict[str, Any]] = []
        update_records: list[dict[str, Any]] = []
        for opportunity in opportunities:
            fields = {
                FEISHU_FIELDS[key]: _feishu_value(opportunity.get(key))
                for key in FEISHU_FIELDS
                if opportunity.get(key) is not None
            }
            source_record_id = str(opportunity["source_record_id"])
            if source_record_id in existing:
                update_records.append({"record_id": existing[source_record_id], "fields": fields})
            else:
                create_records.append({"fields": fields})
        for start in range(0, len(create_records), 500):
            self._request(
                "POST",
                f"{self.records_path}/batch_create",
                json={"records": create_records[start : start + 500]},
            )
        for start in range(0, len(update_records), 500):
            self._request(
                "POST",
                f"{self.records_path}/batch_update",
                json={"records": update_records[start : start + 500]},
            )
        return {"created": len(create_records), "updated": len(update_records)}

    def read_opportunities(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for record in self.iter_records():
            fields = record.get("fields") or {}
            inverse = {key: fields.get(field_name) for key, field_name in FEISHU_FIELDS.items()}
            source_record_id = inverse.get("source_record_id")
            if not source_record_id:
                continue
            try:
                output.append(
                    {
                        "source_record_id": str(source_record_id),
                        "organization_code": str(inverse["organization_code"]),
                        "customer_record_id": str(inverse["customer_record_id"]),
                        "owner_person_record_id": str(inverse["owner_person_record_id"]),
                        "opportunity_code": str(inverse["opportunity_code"]),
                        "title": str(inverse["title"]),
                        "stage": str(inverse["stage"]),
                        "status": str(inverse["status"]),
                        "probability": int(float(inverse["probability"])),
                        "expected_amount": Decimal(str(inverse["expected_amount"])),
                        "expected_gross_profit": Decimal(str(inverse["expected_gross_profit"])),
                        "created_date": date.fromisoformat(str(inverse["created_date"])[:10]),
                        "expected_close_date": date.fromisoformat(
                            str(inverse["expected_close_date"])[:10]
                        ),
                        "closed_date": (
                            date.fromisoformat(str(inverse["closed_date"])[:10])
                            if inverse.get("closed_date")
                            else None
                        ),
                        "source_updated_at": (
                            datetime.fromisoformat(str(inverse["source_updated_at"]))
                            if inverse.get("source_updated_at")
                            else datetime.now(UTC)
                        ),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FeishuError(
                    f"飞书记录 {record.get('record_id', 'unknown')} 字段格式不符合约定"
                ) from exc
        return output


def sync_feishu_opportunities_to_source(
    client: FeishuBitableClient,
    *,
    source_writer_database_url: str,
    dataset_version: str,
    reference_date: date,
    schema: str = SOURCE_SCHEMA,
) -> dict[str, Any]:
    records = client.read_opportunities()
    now = datetime.now(UTC)
    content_hash = hashlib.sha256(
        json.dumps(records, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    batch_id = f"feishu-{dataset_version}-{reference_date.isoformat()}-{content_hash[:16]}"
    rows = [
        {
            "source_system": "simulated_feishu",
            "source_record_id": item["source_record_id"],
            "source_updated_at": item["source_updated_at"],
            "load_batch_id": batch_id,
            "is_deleted": False,
            **{
                key: value
                for key, value in item.items()
                if key not in {"source_record_id", "source_updated_at"}
            },
        }
        for item in records
    ]
    with connect_source(
        source_writer_database_url,
        application_name="executive-ai-feishu-importer",
        read_only=False,
    ) as connection:
        require_valid_source_contract(connection, schema=schema, require_read_only=False)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.source_batches (
                            batch_id, source_system, dataset_version, reference_date,
                            source_data_as_of, status, record_counts, content_sha256,
                            validation_result, completed_at
                        ) VALUES (
                            %s, 'simulated_feishu', %s, %s, %s, 'ready',
                            %s::jsonb, %s, %s::jsonb, now()
                        ) ON CONFLICT (batch_id) DO UPDATE SET
                            source_data_as_of = EXCLUDED.source_data_as_of,
                            status = 'ready', record_counts = EXCLUDED.record_counts,
                            content_sha256 = EXCLUDED.content_sha256, completed_at = now()
                        """
                    ).format(sql.Identifier(schema)),
                    (
                        batch_id,
                        dataset_version,
                        reference_date,
                        now,
                        json.dumps({"opportunities": len(rows)}),
                        content_hash,
                        json.dumps({"valid": True, "source": "feishu_bitable"}),
                    ),
                )
            _upsert_rows(
                connection,
                schema=schema,
                table=SOURCE_TABLES["opportunities"],
                columns=SOURCE_COLUMNS["opportunities"],
                rows=rows,
            )
            source_ids = [row["source_record_id"] for row in rows]
            with connection.cursor() as cursor:
                if source_ids:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.ods_opportunity SET is_deleted = true, "
                            "source_updated_at = %s, load_batch_id = %s "
                            "WHERE source_system = 'simulated_generator'"
                        ).format(sql.Identifier(schema)),
                        (now, batch_id),
                    )
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.ods_opportunity SET is_deleted = true, "
                            "source_updated_at = %s, load_batch_id = %s "
                            "WHERE source_system = 'simulated_feishu' "
                            "AND NOT (source_record_id = ANY(%s))"
                        ).format(sql.Identifier(schema)),
                        (now, batch_id, source_ids),
                    )
                else:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.ods_opportunity SET is_deleted = false "
                            "WHERE source_system = 'simulated_generator'"
                        ).format(sql.Identifier(schema))
                    )
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.ods_opportunity SET is_deleted = true, "
                            "source_updated_at = %s, load_batch_id = %s "
                            "WHERE source_system = 'simulated_feishu'"
                        ).format(sql.Identifier(schema)),
                        (now, batch_id),
                    )
    return {
        "batch_id": batch_id,
        "records": len(rows),
        "content_sha256": content_hash,
    }


def _configured_client(*, credential_env_name: str) -> FeishuBitableClient:
    settings = get_settings()
    secret = os.environ.get(credential_env_name, "")
    if not all(
        (
            settings.feishu_app_id,
            secret,
            settings.feishu_bitable_app_token,
            settings.feishu_bitable_table_id,
        )
    ):
        raise SystemExit("飞书 App ID、凭证、App Token 和 Table ID 必须完整配置")
    return FeishuBitableClient(
        app_id=settings.feishu_app_id or "",
        app_secret=secret,
        app_token=settings.feishu_bitable_app_token or "",
        table_id=settings.feishu_bitable_table_id or "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision or import simulated SA opportunities")
    parser.add_argument("action", choices=("publish", "import"))
    enterprise = parser.add_mutually_exclusive_group(required=True)
    enterprise.add_argument("--enterprise-id")
    enterprise.add_argument("--enterprise-slug")
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env != "local-demo" or settings.app_mode != "demo":
        raise SystemExit("飞书模拟商机工具只允许在 local-demo 环境运行")
    reference_date = date.fromisoformat(settings.demo_reference_date)
    enterprise_id = args.enterprise_id
    if args.enterprise_slug:
        with SessionLocal() as db:
            item = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
            if item is None:
                raise SystemExit("Enterprise does not exist; run create-admin first")
            enterprise_id = str(item.id)
    if args.action == "publish":
        dataset = build_demo_dataset(
            enterprise_id=str(enterprise_id),
            dataset_version=settings.demo_dataset_version,
            reference_date=reference_date,
        )
        with _configured_client(credential_env_name="FEISHU_PROVISIONING_SECRET") as client:
            result = client.publish_opportunities(dataset.opportunities)
    else:
        source_writer_url = os.environ.get("SOURCE_WRITER_DATABASE_URL", "")
        if not source_writer_url:
            raise SystemExit("SOURCE_WRITER_DATABASE_URL is required")
        with _configured_client(credential_env_name="FEISHU_RUNTIME_SECRET") as client:
            result = sync_feishu_opportunities_to_source(
                client,
                source_writer_database_url=source_writer_url,
                dataset_version=settings.demo_dataset_version,
                reference_date=reference_date,
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
