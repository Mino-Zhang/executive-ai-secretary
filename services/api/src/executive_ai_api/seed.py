from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import (
    AppConfig,
    AuditEvent,
    Conversation,
    Enterprise,
    Message,
    OrganizationUnit,
    Project,
    ProjectConversation,
    Report,
    ReportVersion,
    User,
)

DEMO_SEED_VERSION = "phase1-v1"


def seed(enterprise_slug: str) -> None:
    settings = get_settings()
    if not (
        settings.app_env == "local-demo" and settings.app_mode == "demo" and settings.seed_demo_data
    ):
        raise SystemExit("Demo seed is allowed only in APP_ENV=local-demo with APP_MODE=demo")
    with SessionLocal.begin() as db:
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == enterprise_slug))
        if enterprise is None:
            raise SystemExit("Enterprise does not exist; run create-admin before seed-demo")
        marker = db.scalar(
            select(AppConfig).where(
                AppConfig.enterprise_id == enterprise.id,
                AppConfig.key == "demo.seed",
            )
        )
        if marker:
            print(f"Demo seed {marker.value_json.get('version', 'unknown')} already exists")
            return
        executive = db.scalar(
            select(User)
            .where(
                User.enterprise_id == enterprise.id,
                User.role == "executive",
                User.is_active.is_(True),
            )
            .order_by(User.created_at)
            .limit(1)
        )
        if executive is None:
            raise SystemExit("No executive user exists; run create-user before seed-demo")

        units_by_code = {
            item.code: item
            for item in db.scalars(
                select(OrganizationUnit).where(OrganizationUnit.enterprise_id == enterprise.id)
            ).all()
        }
        for name, code, order in (
            ("华东事业部", "east-china", 10),
            ("华南事业部", "south-china", 20),
        ):
            if code not in units_by_code:
                unit = OrganizationUnit(
                    enterprise_id=enterprise.id,
                    name=name,
                    code=code,
                    enabled_for_analysis=True,
                    data_connected=True,
                    sort_order=order,
                    config_json={"fixture": "sanitized-demo"},
                )
                db.add(unit)
                db.flush()
                units_by_code[code] = unit
        east = units_by_code["east-china"]

        project = Project(
            enterprise_id=enterprise.id,
            owner_user_id=executive.id,
            organization_unit_id=east.id,
            name="演示：经营与现金流",
            description="仅用于本机现场演示的脱敏样例项目",
            metadata_json={"fixture": "sanitized-demo"},
        )
        db.add(project)
        conversation = Conversation(
            enterprise_id=enterprise.id,
            owner_user_id=executive.id,
            organization_unit_id=east.id,
            title="演示：本月整体经营情况",
            metadata_json={"fixture": "sanitized-demo"},
            last_message_at=datetime.now(UTC),
        )
        db.add(conversation)
        db.flush()
        db.add(ProjectConversation(project_id=project.id, conversation_id=conversation.id))
        db.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    author_user_id=executive.id,
                    role="user",
                    content="本月整体经营情况如何？",
                    content_json={"fixture": "sanitized-demo"},
                    sequence=1,
                    status="completed",
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=("这是脱敏演示数据：经营节奏总体稳定，回款与两项交付偏差需要确认。"),
                    content_json={"fixture": "sanitized-demo", "data_classification": "synthetic"},
                    sequence=2,
                    status="completed",
                    source_data_as_of=datetime(2026, 7, 25, 2, 6, tzinfo=UTC),
                ),
            ]
        )
        report = Report(
            enterprise_id=enterprise.id,
            organization_unit_id=east.id,
            created_by_user_id=executive.id,
            kind="daily",
            title="脱敏演示｜今日经营简报",
            status="published",
            period_start=date(2026, 7, 26),
            period_end=date(2026, 7, 26),
            data_as_of=datetime(2026, 7, 25, 2, 6, tzinfo=UTC),
            published_at=datetime(2026, 7, 26, 5, 3, tzinfo=UTC),
        )
        db.add(report)
        db.flush()
        db.add(
            ReportVersion(
                report_id=report.id,
                version=1,
                created_by_user_id=executive.id,
                content_json={
                    "classification": "synthetic-sanitized-demo",
                    "summary": "经营节奏总体稳定，回款与两项交付偏差需要确认。",
                    "attention_items": 2,
                },
                source_summary="本地脱敏演示数据，不代表任何真实客户经营情况",
            )
        )
        db.add(
            AppConfig(
                enterprise_id=enterprise.id,
                key="demo.seed",
                value_json={"version": DEMO_SEED_VERSION, "classification": "synthetic"},
            )
        )
        db.add(
            AuditEvent(
                enterprise_id=enterprise.id,
                action="seed.demo_data_created",
                target_type="enterprise",
                target_id=str(enterprise.id),
                outcome="success",
                metadata_json={"version": DEMO_SEED_VERSION, "classification": "synthetic"},
            )
        )
    print(f"Created sanitized demo data {DEMO_SEED_VERSION} for {enterprise_slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed sanitized local demo business data")
    parser.add_argument("--enterprise-slug", required=True)
    args = parser.parse_args()
    seed(args.enterprise_slug)


if __name__ == "__main__":
    main()
