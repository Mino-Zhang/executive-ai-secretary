from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from executive_ai_api.config import Settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.models import AuditEvent, UserSession

from .conftest import NEW_PASSWORD, TEMP_PASSWORD, login


def test_first_login_is_restricted_until_password_change(client, seeded) -> None:
    session = login(client, "executive@example.com")
    assert session["user"]["password_change_required"] is True
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    blocked = client.get("/api/v1/conversations")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"

    missing_csrf = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEMP_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"

    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"current_password": TEMP_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["password_change_required"] is False
    assert client.get("/api/v1/conversations").status_code == 200


def test_csrf_is_bound_to_session_and_logout_revokes_session(client, seeded) -> None:
    session = login(client, "admin@example.com")
    wrong = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert wrong.status_code == 403
    ok = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert ok.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(UserSession).where(UserSession.revoked_at.is_not(None)))
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.logout"))


def test_production_environment_guards_and_cookie_aliases() -> None:
    key = base64.urlsafe_b64encode(b"K" * 32).decode()
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="customer-template",
            app_mode="production",
            session_secret="development-only-change-me-32-characters",
            csrf_secret="development-only-csrf-secret-change-me",
            file_encryption_key=key,
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            app_mode="production",
            seed_demo_data=True,
            session_secret="S" * 40,
            csrf_secret="C" * 40,
            file_encryption_key=key,
        )
    settings = Settings(
        _env_file=None,
        app_env="customer-template",
        app_mode="production",
        session_secret="S" * 40,
        csrf_secret="C" * 40,
        file_encryption_key=key,
        COOKIE_SAMESITE="strict",
    )
    assert settings.session_cookie_samesite == "strict"
    assert settings.decoded_file_encryption_key() == b"K" * 32
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            app_mode="production",
            session_secret="X" * 40,
            csrf_secret="X" * 40,
            audit_hmac_key="A" * 40,
            file_encryption_key=key,
        )


def test_protected_service_roles_require_only_their_runtime_secrets() -> None:
    key = base64.urlsafe_b64encode(b"K" * 32).decode()

    worker = Settings(
        _env_file=None,
        app_env="customer-template",
        app_mode="production",
        service_role="worker",
        audit_hmac_key="A" * 40,
        file_encryption_key=key,
    )
    assert worker.service_role == "worker"
    assert worker.decoded_file_encryption_key() == b"K" * 32

    bootstrap = Settings(
        _env_file=None,
        app_env="customer-template",
        app_mode="production",
        service_role="bootstrap",
        audit_hmac_key="A" * 40,
    )
    assert bootstrap.service_role == "bootstrap"

    migration = Settings(
        _env_file=None,
        app_env="customer-template",
        app_mode="production",
        service_role="migration",
    )
    assert migration.service_role == "migration"

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="customer-template",
            app_mode="production",
            service_role="worker",
            audit_hmac_key="A" * 40,
            file_encryption_key="",
        )


def test_comma_separated_origin_and_host_environment_contract(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    monkeypatch.setenv("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver")
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert settings.trusted_hosts == ["localhost", "127.0.0.1", "testserver"]


def test_request_id_and_structured_errors(client, seeded) -> None:
    response = client.get("/api/v1/conversations", headers={"X-Request-ID": "test-request-123"})
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.json()["error"]["request_id"] == "test-request-123"


def test_readiness_performs_encrypted_storage_round_trip(client) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["storage"] == "encrypted-round-trip-ok"
    assert response.json()["database_revision"]
