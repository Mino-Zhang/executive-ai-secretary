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
from ..models import ModelProviderConfig
from ..schemas import ModelProviderOut, ModelProviderTestOut, ModelProviderUpdate
from ..security import utc_now

router = APIRouter(prefix="/admin/model-provider", tags=["admin-model-provider"])
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

    if credential_changed or model_changed:
        config.last_tested_at = None
        config.last_test_status = "pending"
        config.last_test_latency_ms = None
        config.last_test_error = None
        config.is_enabled = False

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
