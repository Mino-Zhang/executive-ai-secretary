from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from executive_ai_hermes.main import (
    ANSPIRE_ENDPOINT_URL,
    _runtime_config_for_model,
    app,
    settings,
)

INTERNAL_KEY = "hermes-test-internal-key-with-at-least-32-characters"
PROVIDER_CONFIG = {
    "provider": "anspire",
    "endpoint_url": ANSPIRE_ENDPOINT_URL,
    "model_id": "glm-5.2",
    "api_key": "unit-test-anspire-key-runtime-123456",
}


def test_health_fails_closed_without_internal_signature_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(""))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def _signed_request(
    payload: dict,
    *,
    key: str = INTERNAL_KEY,
    request_id: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    request_id = request_id or str(uuid.uuid4())
    signature = hmac.new(
        key.encode(),
        timestamp.encode() + b"." + request_id.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Hermes-Timestamp": timestamp,
        "X-Hermes-Request-Id": request_id,
        "X-Hermes-Signature": signature,
    }


def test_runtime_requires_internal_signature_and_rejects_other_providers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    payload = {
        "profile": "route",
        "payload": {"question": "test"},
        "request_id": "one",
        "provider_config": PROVIDER_CONFIG,
    }
    with TestClient(app) as client:
        unsigned = client.post("/v1/runs", json=payload)
        invalid_payload = {
            **payload,
            "provider_config": {
                **PROVIDER_CONFIG,
                "provider": "openai",
                "endpoint_url": "https://api.openai.com/v1",
            },
        }
        body, headers = _signed_request(invalid_payload)
        invalid_provider = client.post("/v1/runs", content=body, headers=headers)

    assert unsigned.status_code == 401
    assert invalid_provider.status_code == 422


def test_provider_test_uses_only_fixed_anspire_gateway(monkeypatch) -> None:
    observed: dict[str, object] = {}

    async def fake_post(config):
        observed["url"] = f"{ANSPIRE_ENDPOINT_URL}/chat/completions"
        observed["headers"] = {"Authorization": config.api_key.get_secret_value()}
        observed["json"] = {"model": config.model_id}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
            request=httpx.Request("POST", observed["url"]),
        )

    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    monkeypatch.setattr("executive_ai_hermes.main._post_anspire", fake_post)
    payload = {"provider_config": PROVIDER_CONFIG}
    body, headers = _signed_request(payload)
    with TestClient(app) as client:
        response = client.post("/v1/provider-test", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert observed["url"] == f"{ANSPIRE_ENDPOINT_URL}/chat/completions"
    assert observed["headers"]["Authorization"] == PROVIDER_CONFIG["api_key"]
    assert observed["json"]["model"] == PROVIDER_CONFIG["model_id"]


def test_provider_test_translates_gateway_auth_failure(monkeypatch) -> None:
    async def fake_post(_config):
        return httpx.Response(
            403,
            json={"code": "GW_AUTH_KEY_FORBIDDEN", "request_id": "internal-gateway-id"},
            request=httpx.Request("POST", f"{ANSPIRE_ENDPOINT_URL}/chat/completions"),
        )

    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    monkeypatch.setattr("executive_ai_hermes.main._post_anspire", fake_post)
    body, headers = _signed_request({"provider_config": PROVIDER_CONFIG})
    with TestClient(app) as client:
        response = client.post("/v1/provider-test", content=body, headers=headers)

    assert response.status_code == 403
    assert "API Key 有效" in response.json()["detail"]
    assert "internal-gateway-id" not in response.text


def test_runtime_invokes_hermes_019_with_ephemeral_anspire_credential(monkeypatch) -> None:
    observed: dict[str, object] = {}

    async def fake_run(command, *, environment):
        observed["command"] = command
        observed["environment"] = environment
        observed["runtime_config"] = Path(
            environment["HERMES_HOME"], "config.yaml"
        ).read_text(encoding="utf-8")
        usage_path = Path(command[command.index("--usage-file") + 1])
        usage_path.write_text(
            json.dumps({"input_tokens": 10, "output_tokens": 5}),
            encoding="utf-8",
        )
        return 0, '{"route":"data"}', ""

    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    monkeypatch.setattr("executive_ai_hermes.main._run_hermes_process", fake_run)
    payload = {
        "profile": "route",
        "payload": {"question": "本月回款如何？"},
        "request_id": "two",
        "provider_config": PROVIDER_CONFIG,
    }
    body, headers = _signed_request(payload)
    with TestClient(app) as client:
        response = client.post("/v1/runs", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "anspire"
    assert response.json()["runtime_version"] == "0.19.0"
    command = observed["command"]
    assert command[command.index("--provider") + 1] == "custom"
    assert command[command.index("--model") + 1] == "glm-5.2"
    assert command[command.index("--toolsets") + 1] == "context_engine"
    assert "--safe-mode" in command
    assert "--ignore-rules" in command
    assert PROVIDER_CONFIG["api_key"] not in command
    assert observed["environment"]["ANSPIRE_API_KEY"] == PROVIDER_CONFIG["api_key"]
    assert observed["environment"]["CUSTOM_BASE_URL"] == ANSPIRE_ENDPOINT_URL
    assert observed["environment"]["HERMES_MAX_TOKENS"] == "700"
    assert observed["runtime_config"] == "agent:\n  reasoning_effort: none\n"
    assert "OPENAI_API_KEY" not in observed["environment"]
    assert "OPENAI_BASE_URL" not in observed["environment"]


def test_runtime_applies_profile_specific_output_budget(monkeypatch) -> None:
    observed: dict[str, str] = {}

    async def fake_run(_command, *, environment):
        observed["max_tokens"] = environment["HERMES_MAX_TOKENS"]
        return 0, "简洁回答", ""

    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    monkeypatch.setattr("executive_ai_hermes.main._run_hermes_process", fake_run)
    payload = {
        "profile": "data",
        "payload": {"question": "本月回款风险"},
        "request_id": "data-budget",
        "provider_config": PROVIDER_CONFIG,
    }
    body, headers = _signed_request(payload)
    with TestClient(app) as client:
        response = client.post("/v1/runs", content=body, headers=headers)

    assert response.status_code == 200, response.text
    assert observed["max_tokens"] == "1600"


def test_glm_reasoning_control_is_not_applied_to_other_catalog_models() -> None:
    assert _runtime_config_for_model("glm-5.2") == "agent:\n  reasoning_effort: none\n"
    assert _runtime_config_for_model("gpt-5.4") == "agent: {}\n"


def test_runtime_rejects_replayed_signed_request(monkeypatch) -> None:
    async def fake_run(_command, *, environment):
        assert environment["ANSPIRE_API_KEY"] == PROVIDER_CONFIG["api_key"]
        return 0, '{"route":"data"}', ""

    monkeypatch.setattr(settings, "hermes_runtime_hmac_key", SecretStr(INTERNAL_KEY))
    monkeypatch.setattr("executive_ai_hermes.main._run_hermes_process", fake_run)
    payload = {
        "profile": "route",
        "payload": {"question": "test"},
        "request_id": "replay-test",
        "provider_config": PROVIDER_CONFIG,
    }
    body, headers = _signed_request(payload, request_id="fixed-replay-request-id")
    with TestClient(app) as client:
        first = client.post("/v1/runs", content=body, headers=headers)
        second = client.post("/v1/runs", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "duplicate" in second.json()["detail"]
