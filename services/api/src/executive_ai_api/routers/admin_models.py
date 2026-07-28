from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..anspire import (
    ANSPIRE_ENDPOINT_URL,
    ANSPIRE_MODELS,
    ANSPIRE_PROVIDER,
    DEFAULT_ANSPIRE_MODEL,
    AnspireConfigurationError,
    decrypt_anspire_api_key,
    encrypt_anspire_api_key,
    masked_api_key,
    validate_anspire_model,
)
from ..audit import record_audit
from ..authz import Principal, require_roles
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..hermes_client import HermesRuntimeError, test_anspire_provider
from ..model_authorization import (
    authorized_model_rows,
    ensure_authorization_row,
    model_authorization,
    model_catalog_item,
)
from ..models import EnterpriseModelAuthorization, ModelProviderConfig
from ..schemas import (
    AdminModelAuthorizationOut,
    AdminModelCatalogOut,
    DefaultModelUpdate,
    ModelAuthorizationUpdate,
    ModelProviderOut,
    ModelProviderTestOut,
    ModelProviderUpdate,
)
from ..security import utc_now

router = APIRouter(prefix="/admin/model-provider", tags=["admin-model-provider"])
models_router = APIRouter(prefix="/admin/models", tags=["admin-model-authorization"])
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]


def _get_config(db: Session, principal: Principal) -> ModelProviderConfig | None:
    return db.scalar(
        select(ModelProviderConfig).where(
            ModelProviderConfig.enterprise_id == principal.enterprise_id
        )
    )


def _response(config: ModelProviderConfig | None) -> ModelProviderOut:
    return ModelProviderOut(
        endpoint_url=ANSPIRE_ENDPOINT_URL,
        documentation_url="https://llm.anspire.ai/?tab=models",
        model_id=config.model_id if config else DEFAULT_ANSPIRE_MODEL,
        is_enabled=config.is_enabled if config else False,
        is_configured=bool(config and config.api_key_ciphertext and config.api_key_nonce),
        api_key_masked=masked_api_key(config),
        credential_version=config.credential_version if config else 1,
        last_tested_at=config.last_tested_at if config else None,
        last_test_status=config.last_test_status if config else None,
        last_test_latency_ms=config.last_test_latency_ms if config else None,
        last_test_error=config.last_test_error if config else None,
        models=list(ANSPIRE_MODELS),
        updated_at=config.updated_at if config else None,
    )


@router.get("", response_model=ModelProviderOut)
def get_model_provider(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> ModelProviderOut:
    return _response(_get_config(db, principal))


@router.put("", response_model=ModelProviderOut)
def update_model_provider(
    payload: ModelProviderUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelProviderOut:
    try:
        model_id = validate_anspire_model(payload.model_id)
    except AnspireConfigurationError as exc:
        raise AppError(422, exc.code, str(exc)) from exc
    config = _get_config(db, principal)
    if config is None:
        config = ModelProviderConfig(
            enterprise_id=principal.enterprise_id,
            provider=ANSPIRE_PROVIDER,
            endpoint_url=ANSPIRE_ENDPOINT_URL,
            model_id=model_id,
            is_enabled=False,
            encryption_key_version=settings.integration_encryption_key_version,
        )
        db.add(config)

    credential_changed = payload.api_key is not None
    model_changed = config.model_id != model_id
    config.provider = ANSPIRE_PROVIDER
    config.endpoint_url = ANSPIRE_ENDPOINT_URL
    config.model_id = model_id
    config.updated_by_user_id = principal.user.id
    if payload.api_key is not None:
        try:
            encrypted = encrypt_anspire_api_key(
                payload.api_key.get_secret_value(),
                enterprise_id=principal.enterprise_id,
                settings=settings,
            )
        except AnspireConfigurationError as exc:
            raise AppError(422, exc.code, str(exc)) from exc
        config.api_key_ciphertext = encrypted.ciphertext
        config.api_key_nonce = encrypted.nonce
        config.api_key_hint = encrypted.hint
        config.encryption_key_version = encrypted.key_version
        if config.id is not None:
            config.credential_version += 1

    if credential_changed or model_changed:
        config.last_tested_at = None
        config.last_test_status = "pending"
        config.last_test_latency_ms = None
        config.last_test_error = None
        config.is_enabled = False
    if credential_changed:
        for authorization in db.scalars(
            select(EnterpriseModelAuthorization).where(
                EnterpriseModelAuthorization.enterprise_id == principal.enterprise_id
            )
        ).all():
            authorization.test_status = "pending"
            authorization.tested_credential_version = None
            authorization.is_authorized = False
            authorization.is_default = False
            authorization.last_test_error = None

    if payload.is_enabled is not None:
        if payload.is_enabled and (
            not config.api_key_ciphertext or config.last_test_status != "success"
        ):
            raise AppError(
                409,
                "anspire_test_required",
                "启用前必须先保存 Anspire 凭证并通过连接测试",
            )
        config.is_enabled = payload.is_enabled

    record_audit(
        db,
        request,
        "admin.anspire_model_updated",
        actor=principal.user,
        session=principal.session,
        target_type="model_provider",
        target_id=config.id,
        metadata={
            "provider": ANSPIRE_PROVIDER,
            "model_id": model_id,
            "credential_replaced": credential_changed,
            "enabled": config.is_enabled,
        },
    )
    db.commit()
    db.refresh(config)
    return _response(config)


@router.post("/test", response_model=ModelProviderTestOut)
def test_model_provider(
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelProviderTestOut:
    config = _get_config(db, principal)
    if config is None or not config.api_key_ciphertext:
        raise AppError(409, "anspire_not_configured", "请先保存 Anspire API Key")
    try:
        provider_config = {
            "provider": ANSPIRE_PROVIDER,
            "endpoint_url": ANSPIRE_ENDPOINT_URL,
            "model_id": validate_anspire_model(config.model_id),
            "api_key": decrypt_anspire_api_key(config, settings),
        }
        result = test_anspire_provider(settings, provider_config)
    except (AnspireConfigurationError, HermesRuntimeError) as exc:
        config.is_enabled = False
        config.last_tested_at = utc_now()
        config.last_test_status = "failed"
        config.last_test_latency_ms = None
        config.last_test_error = str(exc)[:1000]
        record_audit(
            db,
            request,
            "admin.anspire_model_tested",
            actor=principal.user,
            session=principal.session,
            target_type="model_provider",
            target_id=config.id,
            outcome="failure",
            failure_reason_code=getattr(exc, "code", "anspire_connection_failed"),
            metadata={"provider": ANSPIRE_PROVIDER, "model_id": config.model_id},
        )
        db.commit()
        raise AppError(
            422,
            getattr(exc, "code", "anspire_connection_failed"),
            str(exc),
        ) from exc

    tested_at = utc_now()
    config.last_tested_at = tested_at
    config.last_test_status = "success"
    config.last_test_latency_ms = int(result["latency_ms"])
    config.last_test_error = None
    record_audit(
        db,
        request,
        "admin.anspire_model_tested",
        actor=principal.user,
        session=principal.session,
        target_type="model_provider",
        target_id=config.id,
        metadata={
            "provider": ANSPIRE_PROVIDER,
            "model_id": config.model_id,
            "latency_ms": config.last_test_latency_ms,
        },
    )
    db.commit()
    return ModelProviderTestOut(
        model=config.model_id,
        latency_ms=config.last_test_latency_ms,
        tested_at=tested_at,
    )


def _authorization_out(
    row: EnterpriseModelAuthorization | None,
    item: dict[str, object],
    config: ModelProviderConfig | None,
) -> AdminModelAuthorizationOut:
    credential_version = config.credential_version if config else 1
    return AdminModelAuthorizationOut(
        model_id=str(item["id"]),
        name=str(item["name"]),
        family=str(item["family"]),
        profile=str(item["profile"]),
        display_name=(
            str(item["name"])
            if row is None or not row.display_name or row.display_name == row.model_id
            else row.display_name
        ),
        capability=str(item["capability"]),
        selectable=bool(item["selectable"]),
        test_status=(row.test_status if row else "pending"),
        tested_credential_version=row.tested_credential_version if row else None,
        current_credential_version=credential_version,
        is_authorized=bool(row and row.is_authorized),
        is_default=bool(row and row.is_default),
        last_tested_at=row.last_tested_at if row else None,
        last_test_latency_ms=row.last_test_latency_ms if row else None,
        last_test_error=row.last_test_error if row else None,
        authorized_at=row.authorized_at if row else None,
    )


@models_router.get("", response_model=AdminModelCatalogOut)
def get_admin_models(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> AdminModelCatalogOut:
    config = _get_config(db, principal)
    rows = {
        row.model_id: row
        for row in db.scalars(
            select(EnterpriseModelAuthorization).where(
                EnterpriseModelAuthorization.enterprise_id == principal.enterprise_id
            )
        ).all()
    }
    return AdminModelCatalogOut(
        credential_version=config.credential_version if config else 1,
        is_configured=bool(config and config.api_key_ciphertext),
        is_enabled=bool(config and config.is_enabled),
        models=[
            _authorization_out(rows.get(str(item["id"])), item, config)
            for item in ANSPIRE_MODELS
        ],
    )


@models_router.post("/{model_id}/test", response_model=ModelProviderTestOut)
def test_authorized_model(
    model_id: str,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelProviderTestOut:
    item = model_catalog_item(model_id)
    config = _get_config(db, principal)
    if config is None or not config.api_key_ciphertext:
        raise AppError(409, "anspire_not_configured", "请先保存 Anspire API Key")
    row = ensure_authorization_row(db, principal.enterprise_id, str(item["id"]))
    tested_at = utc_now()
    try:
        result = test_anspire_provider(
            settings,
            {
                "provider": ANSPIRE_PROVIDER,
                "endpoint_url": ANSPIRE_ENDPOINT_URL,
                "model_id": str(item["id"]),
                "api_key": decrypt_anspire_api_key(config, settings),
            },
        )
    except (AnspireConfigurationError, HermesRuntimeError) as exc:
        row.test_status = "failed"
        row.tested_credential_version = config.credential_version
        row.last_tested_at = tested_at
        row.last_test_latency_ms = None
        row.last_test_error = str(exc)[:1000]
        row.is_authorized = False
        row.is_default = False
        record_audit(
            db,
            request,
            "admin.anspire_model_tested",
            actor=principal.user,
            session=principal.session,
            target_type="model_authorization",
            target_id=row.id,
            outcome="failure",
            failure_reason_code=getattr(exc, "code", "anspire_connection_failed"),
            metadata={"model_id": row.model_id, "credential_version": config.credential_version},
        )
        db.commit()
        raise AppError(422, getattr(exc, "code", "anspire_connection_failed"), str(exc)) from exc
    row.test_status = "success"
    row.tested_credential_version = config.credential_version
    row.last_tested_at = tested_at
    row.last_test_latency_ms = int(result["latency_ms"])
    row.last_test_error = None
    config.last_tested_at = tested_at
    config.last_test_status = "success"
    config.last_test_latency_ms = row.last_test_latency_ms
    config.last_test_error = None
    record_audit(
        db,
        request,
        "admin.anspire_model_tested",
        actor=principal.user,
        session=principal.session,
        target_type="model_authorization",
        target_id=row.id,
        metadata={
            "model_id": row.model_id,
            "credential_version": config.credential_version,
            "latency_ms": row.last_test_latency_ms,
        },
    )
    db.commit()
    return ModelProviderTestOut(
        model=row.model_id,
        latency_ms=row.last_test_latency_ms,
        tested_at=tested_at,
    )


@models_router.patch("/{model_id}/authorization", response_model=AdminModelAuthorizationOut)
def update_model_authorization(
    model_id: str,
    payload: ModelAuthorizationUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> AdminModelAuthorizationOut:
    item = model_catalog_item(model_id)
    config = _get_config(db, principal)
    if config is None or not config.api_key_ciphertext:
        raise AppError(409, "anspire_not_configured", "请先保存 Anspire API Key")
    row = ensure_authorization_row(db, principal.enterprise_id, str(item["id"]))
    already_authorized = [
        authorization
        for authorization in authorized_model_rows(db, principal.enterprise_id)
        if authorization.id != row.id
    ]
    was_default = row.is_default
    if payload.is_authorized and (
        row.test_status != "success"
        or row.tested_credential_version != config.credential_version
    ):
        raise AppError(409, "anspire_test_required", "授权前必须使用当前凭证完成模型测试")
    row.display_name = payload.display_name or row.display_name
    row.is_authorized = payload.is_authorized
    row.authorized_by_user_id = principal.user.id if payload.is_authorized else None
    row.authorized_at = utc_now() if payload.is_authorized else None
    if not payload.is_authorized:
        row.is_default = False
        if was_default and already_authorized:
            # The unique partial index allows only one default per enterprise.
            # Persist the demotion before promoting the replacement so SQLite
            # and PostgreSQL never observe two defaults in one flush batch.
            db.flush()
            already_authorized[0].is_default = True
            config.model_id = already_authorized[0].model_id
    elif not already_authorized:
        row.is_default = True
    config.is_enabled = bool(payload.is_authorized or already_authorized)
    if payload.is_authorized and row.is_default:
        config.model_id = row.model_id
    record_audit(
        db,
        request,
        "admin.anspire_model_authorization_updated",
        actor=principal.user,
        session=principal.session,
        target_type="model_authorization",
        target_id=row.id,
        metadata={"model_id": row.model_id, "authorized": row.is_authorized},
    )
    db.commit()
    db.refresh(row)
    return _authorization_out(row, item, config)


@models_router.patch("/{model_id}/default", response_model=AdminModelAuthorizationOut)
def set_default_model(
    model_id: str,
    payload: DefaultModelUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> AdminModelAuthorizationOut:
    del payload
    item = model_catalog_item(model_id)
    config = _get_config(db, principal)
    row = model_authorization(db, principal.enterprise_id, str(item["id"]))
    if config is None or row is None or not row.is_authorized:
        raise AppError(409, "model_not_authorized", "只能将已授权模型设为默认模型")
    if row.test_status != "success" or row.tested_credential_version != config.credential_version:
        raise AppError(409, "anspire_test_required", "默认模型必须通过当前凭证测试")
    authorizations = db.scalars(
        select(EnterpriseModelAuthorization).where(
            EnterpriseModelAuthorization.enterprise_id == principal.enterprise_id
        )
    ).all()
    for other in authorizations:
        other.is_default = False
    db.flush()
    row.is_default = True
    config.model_id = row.model_id
    record_audit(
        db,
        request,
        "admin.anspire_default_model_updated",
        actor=principal.user,
        session=principal.session,
        target_type="model_authorization",
        target_id=row.id,
        metadata={"model_id": row.model_id},
    )
    db.commit()
    db.refresh(row)
    return _authorization_out(row, item, config)
