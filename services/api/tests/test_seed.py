from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from executive_ai_api import seed as seed_module
from executive_ai_api.database import SessionLocal
from executive_ai_api.models import (
    AppConfig,
    Conversation,
    DataSource,
    Message,
    Report,
    UserCredential,
)


def test_demo_seed_requires_existing_identity_and_is_idempotent(monkeypatch, seeded) -> None:
    monkeypatch.setattr(
        seed_module,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="local-demo",
            app_mode="demo",
            seed_demo_data=True,
        ),
    )
    with SessionLocal.begin() as db:
        enterprise = db.get(seed_module.Enterprise, seeded["enterprise_id"])
        enterprise.slug = "local-demo"
        credentials_before = db.scalar(select(func.count()).select_from(UserCredential))
    seed_module.seed("local-demo")
    with SessionLocal() as db:
        credentials_after = db.scalar(select(func.count()).select_from(UserCredential))
        conversations_after_first = db.scalar(select(func.count()).select_from(Conversation))
        marker = db.scalar(select(AppConfig).where(AppConfig.key == "demo.seed"))
        data_source = db.scalar(
            select(DataSource).where(DataSource.key == "demo-sanitized-source")
        )
        assert marker.value_json["classification"] == "synthetic"
        assert data_source is not None
        assert data_source.source_type == "feishu_three_table"
        assert data_source.schema_version == "3.0"
        assert data_source.configuration_json["schema"] == "executive_source_v3"
        assert db.scalar(select(Report.status)) == "published"
        assert set(db.scalars(select(Message.status)).all()) == {"completed"}
    seed_module.seed("local-demo")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserCredential)) == credentials_after
        assert credentials_after == credentials_before
        assert (
            db.scalar(select(func.count()).select_from(Conversation)) == conversations_after_first
        )
