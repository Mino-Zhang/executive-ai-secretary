from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "demo",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SESSION_SECRET": "test-session-secret-with-at-least-32-characters",
        "CSRF_SECRET": "test-csrf-secret-with-at-least-32-characters",
        "AUDIT_HMAC_KEY": "test-audit-hmac-key-with-at-least-32-characters",
        "COOKIE_SECURE": "false",
        "COOKIE_SAMESITE": "strict",
        "TRUSTED_HOSTS": "testserver,localhost,127.0.0.1",
        "LOGIN_MAX_ATTEMPTS": "100",
    }
)
_storage_root = tempfile.mkdtemp(prefix="executive-ai-api-tests-")
os.environ["STORAGE_ROOT"] = _storage_root

import pytest
from fastapi.testclient import TestClient

from executive_ai_api.config import get_settings
from executive_ai_api.database import Base, SessionLocal, engine
from executive_ai_api.main import app
from executive_ai_api.models import (
    DataScopeGrant,
    Enterprise,
    OrganizationUnit,
    User,
    UserCredential,
)
from executive_ai_api.security import hash_password

TEMP_PASSWORD = "TempStrong!23456"
NEW_PASSWORD = "NewStrong!23456"


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(_storage_root, ignore_errors=True)
    Path(_storage_root).mkdir(parents=True, exist_ok=True)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded() -> dict:
    with SessionLocal.begin() as db:
        enterprise = Enterprise(name="测试集团", slug="test-enterprise")
        db.add(enterprise)
        db.flush()
        east = OrganizationUnit(
            enterprise_id=enterprise.id,
            name="华东事业部",
            code="east",
            enabled_for_analysis=True,
            data_connected=True,
            sort_order=1,
        )
        west = OrganizationUnit(
            enterprise_id=enterprise.id,
            name="西部事业部",
            code="west",
            enabled_for_analysis=True,
            data_connected=True,
            sort_order=2,
        )
        disconnected = OrganizationUnit(
            enterprise_id=enterprise.id,
            name="待接入事业部",
            code="pending",
            enabled_for_analysis=True,
            data_connected=False,
            sort_order=3,
        )
        db.add_all([east, west, disconnected])
        db.flush()

        users = {}
        for role, email, require_change in (
            ("executive", "executive@example.com", True),
            ("executive", "other@example.com", False),
            ("enterprise_admin", "admin@example.com", False),
            ("fde", "fde@example.com", False),
        ):
            user = User(
                enterprise_id=enterprise.id,
                email=email,
                display_name=email.split("@")[0],
                role=role,
                password_change_required=require_change,
            )
            db.add(user)
            db.flush()
            db.add(UserCredential(user_id=user.id, password_hash=hash_password(TEMP_PASSWORD)))
            if email == "executive@example.com":
                db.add(
                    DataScopeGrant(
                        user_id=user.id,
                        scope_kind="organization_unit",
                        organization_unit_id=east.id,
                    )
                )
            else:
                db.add(DataScopeGrant(user_id=user.id, scope_kind="enterprise"))
            users[email] = user.id
        return {
            "enterprise_id": enterprise.id,
            "east_id": east.id,
            "west_id": west.id,
            "pending_id": disconnected.id,
            "users": users,
        }


def login(client: TestClient, email: str, password: str = TEMP_PASSWORD) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def login_and_change_password(client: TestClient, email: str = "executive@example.com") -> dict:
    session = login(client, email)
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"current_password": TEMP_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    get_settings.cache_clear()
    shutil.rmtree(_storage_root, ignore_errors=True)
