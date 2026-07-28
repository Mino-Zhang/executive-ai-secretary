from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .anspire import ANSPIRE_MODELS, validate_anspire_model
from .errors import AppError
from .models import EnterpriseModelAuthorization, ModelProviderConfig


def catalog_by_id() -> dict[str, dict[str, object]]:
    return {str(item["id"]): dict(item) for item in ANSPIRE_MODELS}


def model_catalog_item(model_id: str) -> dict[str, object]:
    normalized = validate_anspire_model(model_id)
    item = catalog_by_id().get(normalized)
    if item is None or item.get("capability") != "chat" or not item.get("selectable"):
        raise AppError(422, "anspire_model_not_chat", "该模型不能用于董事长对话工作台")
    return item


def provider_config(db: Session, enterprise_id: uuid.UUID) -> ModelProviderConfig | None:
    return db.scalar(
        select(ModelProviderConfig).where(ModelProviderConfig.enterprise_id == enterprise_id)
    )


def model_authorization(
    db: Session,
    enterprise_id: uuid.UUID,
    model_id: str,
) -> EnterpriseModelAuthorization | None:
    return db.scalar(
        select(EnterpriseModelAuthorization).where(
            EnterpriseModelAuthorization.enterprise_id == enterprise_id,
            EnterpriseModelAuthorization.model_id == model_id,
        )
    )


def authorization_is_current(
    authorization: EnterpriseModelAuthorization,
    config: ModelProviderConfig,
) -> bool:
    return bool(
        config.is_enabled
        and config.api_key_ciphertext
        and authorization.is_authorized
        and authorization.test_status == "success"
        and authorization.tested_credential_version == config.credential_version
    )


def authorized_model_rows(
    db: Session,
    enterprise_id: uuid.UUID,
) -> list[EnterpriseModelAuthorization]:
    config = provider_config(db, enterprise_id)
    if config is None:
        return []
    rows = db.scalars(
        select(EnterpriseModelAuthorization)
        .where(
            EnterpriseModelAuthorization.enterprise_id == enterprise_id,
            EnterpriseModelAuthorization.is_authorized.is_(True),
        )
        .order_by(
            EnterpriseModelAuthorization.is_default.desc(),
            EnterpriseModelAuthorization.display_name,
        )
    ).all()
    return [row for row in rows if authorization_is_current(row, config)]


def resolve_authorized_model(
    db: Session,
    enterprise_id: uuid.UUID,
    requested_model_id: str | None,
) -> str:
    rows = authorized_model_rows(db, enterprise_id)
    if not rows:
        raise AppError(
            409,
            "authorized_model_missing",
            "管理员尚未授权可用模型",
        )
    if requested_model_id:
        normalized = validate_anspire_model(requested_model_id)
        if any(row.model_id == normalized for row in rows):
            return normalized
        raise AppError(
            409,
            "model_not_authorized",
            "所选模型已不在企业授权范围，请重新选择",
        )
    default = next((row for row in rows if row.is_default), None)
    return (default or rows[0]).model_id


def ensure_authorization_row(
    db: Session,
    enterprise_id: uuid.UUID,
    model_id: str,
) -> EnterpriseModelAuthorization:
    item = model_catalog_item(model_id)
    row = model_authorization(db, enterprise_id, str(item["id"]))
    if row is None:
        row = EnterpriseModelAuthorization(
            enterprise_id=enterprise_id,
            model_id=str(item["id"]),
            display_name=str(item["name"]),
            test_status="pending",
            is_authorized=False,
            is_default=False,
        )
        db.add(row)
        db.flush()
    return row
