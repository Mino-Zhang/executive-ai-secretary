from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from executive_ai_api import seed as seed_module
from executive_ai_api.database import SessionLocal
from executive_ai_api.models import AppConfig, Conversation, Message, Report, UserCredential


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
        assert marker.value_json["classification"] == "synthetic"
        assert db.scalar(select(Report.status)) == "published"
        assert set(db.scalars(select(Message.status)).all()) == {"completed"}
    seed_module.seed("local-demo")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserCredential)) == credentials_after
        assert credentials_after == credentials_before
        assert (
            db.scalar(select(func.count()).select_from(Conversation)) == conversations_after_first
        )
