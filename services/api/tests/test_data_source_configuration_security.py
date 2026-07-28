from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import DataSource
from tests.conftest import login

LEGACY_PASSWORD = "legacy-password-must-not-leak"
LEGACY_API_KEY = "legacy-api-key-must-not-leak"
LEGACY_APP_TOKEN = "app_legacy_resource_identifier"
NEW_APP_TOKEN = "app_new_resource_identifier"
REQUEST_SECRET = "request-secret-must-not-leak"


def _seed_source(seeded: dict, configuration: dict) -> uuid.UUID:
    with SessionLocal.begin() as db:
        source = DataSource(
            enterprise_id=seeded["enterprise_id"],
            key="configuration-security-source",
            display_name="Configuration security source",
            source_type="feishu_three_table",
            schema_version="3.0",
            is_enabled=True,
            configuration_json=configuration,
            secret_reference_key="SOURCE_DATABASE_URL",
        )
        db.add(source)
        db.flush()
        return source.id


def test_generic_data_source_api_redacts_legacy_secrets_and_resource_tokens(
    client, seeded
) -> None:
    _seed_source(
        seeded,
        {
            "schema": "executive_source_v3",
            "connection_mode": "internal",
            "activation_policy": "all_three_atomic",
            "database": f"postgresql://reader:{LEGACY_PASSWORD}@source/database",
            "folder_token": "folder_resource_identifier",
            "tables": {
                "opportunity": {
                    "app_token": LEGACY_APP_TOKEN,
                    "table_id": "tbl_opportunity",
                    "client_secret": LEGACY_API_KEY,
                }
            },
            "password": LEGACY_PASSWORD,
            "legacy_extension": {"api_key": LEGACY_API_KEY},
        },
    )
    login(client, "admin@example.com")

    response = client.get("/api/v1/admin/data-sources")

    assert response.status_code == 200, response.text
    payload_text = response.text
    assert LEGACY_PASSWORD not in payload_text
    assert LEGACY_API_KEY not in payload_text
    assert LEGACY_APP_TOKEN not in payload_text
    configuration = response.json()["items"][0]["configuration_json"]
    assert configuration == {
        "activation_policy": "all_three_atomic",
        "connection_mode": "internal",
        "schema": "executive_source_v3",
        "tables": {"opportunity": {"table_id": "tbl_opportunity"}},
    }
    assert "token" not in json.dumps(configuration, ensure_ascii=False).lower()
    assert "secret" not in json.dumps(configuration, ensure_ascii=False).lower()
    assert "password" not in json.dumps(configuration, ensure_ascii=False).lower()
    assert "key" not in json.dumps(configuration, ensure_ascii=False).lower()


def test_round_trip_of_public_legacy_configuration_preserves_hidden_bindings(
    client, seeded
) -> None:
    source_id = _seed_source(
        seeded,
        {
            "schema": "executive_source_v3",
            "connection_mode": "internal",
            "database": f"postgresql://reader:{LEGACY_PASSWORD}@source/database",
            "database_version": "PostgreSQL 17.5",
            "read_only": True,
            "tls_active": False,
            "folder_token": "folder_resource_identifier",
            "tables": {
                "opportunity": {
                    "app_token": LEGACY_APP_TOKEN,
                    "table_id": "tbl_opportunity",
                }
            },
            "legacy_extension": {"display_hint": "old-client"},
        },
    )
    session = login(client, "admin@example.com")
    listed = client.get("/api/v1/admin/data-sources")
    public_configuration = listed.json()["items"][0]["configuration_json"]

    response = client.patch(
        f"/api/v1/admin/data-sources/{source_id}",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"configuration_json": public_configuration},
    )

    assert response.status_code == 200, response.text
    assert LEGACY_APP_TOKEN not in response.text
    with SessionLocal() as db:
        stored = db.get(DataSource, source_id)
        assert stored is not None
        assert stored.configuration_json["folder_token"] == "folder_resource_identifier"
        assert stored.configuration_json["tables"]["opportunity"]["app_token"] == LEGACY_APP_TOKEN
        assert stored.configuration_json["database_version"] == "PostgreSQL 17.5"
        assert "database" not in stored.configuration_json
        assert "legacy_extension" not in stored.configuration_json


def test_valid_whitelisted_configuration_is_stored_but_tokens_are_not_returned(
    client, seeded
) -> None:
    source_id = _seed_source(seeded, {"schema": "executive_source_v3"})
    session = login(client, "admin@example.com")
    configuration = {
        "schema": "executive_source_v3",
        "connection_mode": "internal",
        "folder_token": "folder_resource_identifier",
        "tables": {
            "opportunity": {
                "app_token": NEW_APP_TOKEN,
                "table_id": "tbl_opportunity",
            },
            "delivery": {
                "app_token": NEW_APP_TOKEN,
                "table_id": "tbl_delivery",
            },
            "collection": {
                "app_token": NEW_APP_TOKEN,
                "table_id": "tbl_collection",
            },
        },
        "activation_policy": "all_three_atomic",
        "experience_weights_percent": {"high": 20, "medium": 10, "low": 5},
        "source_contract": "3.0",
    }

    response = client.patch(
        f"/api/v1/admin/data-sources/{source_id}",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"configuration_json": configuration},
    )

    assert response.status_code == 200, response.text
    assert NEW_APP_TOKEN not in response.text
    assert "folder_resource_identifier" not in response.text
    public_configuration = response.json()["configuration_json"]
    assert public_configuration["tables"] == {
        "opportunity": {"table_id": "tbl_opportunity"},
        "delivery": {"table_id": "tbl_delivery"},
        "collection": {"table_id": "tbl_collection"},
    }
    with SessionLocal() as db:
        stored = db.scalar(select(DataSource).where(DataSource.id == source_id))
        assert stored is not None
        assert stored.configuration_json["tables"]["opportunity"]["app_token"] == NEW_APP_TOKEN
        assert stored.configuration_json["folder_token"] == "folder_resource_identifier"


@pytest.mark.parametrize(
    "configuration",
    [
        {"schema": "executive_source_v3", "password": REQUEST_SECRET},
        {"schema": "executive_source_v3", "apiKey": REQUEST_SECRET},
        {
            "schema": "executive_source_v3",
            "tables": {
                "opportunity": {
                    "app_token": "app_safe_identifier",
                    "table_id": "tbl_opportunity",
                    "app_secret": REQUEST_SECRET,
                }
            },
        },
        {
            "schema": "executive_source_v3",
            "extension": {"access_token": REQUEST_SECRET},
        },
    ],
    ids=["password", "camel-case-api-key", "nested-app-secret", "unknown-nested-token"],
)
def test_configuration_update_rejects_credentials_without_echoing_values(
    client, seeded, configuration
) -> None:
    source_id = _seed_source(seeded, {"schema": "executive_source_v3"})
    session = login(client, "admin@example.com")

    response = client.patch(
        f"/api/v1/admin/data-sources/{source_id}",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"configuration_json": configuration},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "data_source_configuration_sensitive_key"
    assert REQUEST_SECRET not in response.text
    with SessionLocal() as db:
        stored = db.get(DataSource, source_id)
        assert stored is not None
        assert stored.configuration_json == {"schema": "executive_source_v3"}


def test_configuration_update_rejects_non_whitelisted_fields(client, seeded) -> None:
    source_id = _seed_source(seeded, {"schema": "executive_source_v3"})
    session = login(client, "admin@example.com")

    response = client.patch(
        f"/api/v1/admin/data-sources/{source_id}",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={
            "configuration_json": {
                "schema": "executive_source_v3",
                "custom_retry_count": 4,
            }
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "data_source_configuration_key_not_allowed"
