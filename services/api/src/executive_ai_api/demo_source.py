from __future__ import annotations

import argparse
import json
import os
from datetime import date

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .demo_dataset import build_demo_dataset
from .models import Enterprise
from .source_contract import connect_source, write_demo_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the deterministic phase-2 demo source")
    enterprise = parser.add_mutually_exclusive_group(required=True)
    enterprise.add_argument("--enterprise-id")
    enterprise.add_argument("--enterprise-slug")
    parser.add_argument("--dataset-version")
    parser.add_argument("--reference-date")
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env != "local-demo" or settings.app_mode != "demo":
        raise SystemExit("Demo source generation is allowed only in local-demo demo mode")
    database_url = os.environ.get("SOURCE_WRITER_DATABASE_URL", "")
    if not database_url:
        raise SystemExit("SOURCE_WRITER_DATABASE_URL is required")
    reference_date = date.fromisoformat(args.reference_date or settings.demo_reference_date)
    enterprise_id = args.enterprise_id
    if args.enterprise_slug:
        with SessionLocal() as db:
            item = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
            if item is None:
                raise SystemExit("Enterprise does not exist; run create-admin first")
            enterprise_id = str(item.id)
    dataset = build_demo_dataset(
        enterprise_id=str(enterprise_id),
        dataset_version=args.dataset_version or settings.demo_dataset_version,
        reference_date=reference_date,
    )
    with connect_source(
        database_url,
        application_name="executive-ai-demo-generator",
        read_only=False,
    ) as connection:
        counts = write_demo_dataset(connection, dataset)
    print(
        json.dumps(
            {"manifest": dataset.manifest(), "written": counts},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
