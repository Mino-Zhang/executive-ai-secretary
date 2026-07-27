from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx

from .config import Settings


class HermesRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, *, permanent: bool = False) -> None:
        self.code = code
        self.permanent = permanent
        super().__init__(message)


def _signed_request(
    settings: Settings,
    *,
    path: str,
    payload: dict[str, Any],
    timeout: float,
) -> httpx.Response:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    request_nonce = str(uuid.uuid4())
    key = settings.hermes_runtime_hmac_key.get_secret_value()
    if len(key) < 32:
        raise HermesRuntimeError(
            "hermes_internal_auth_invalid",
            "Hermes 内部鉴权密钥未正确配置",
            permanent=True,
        )
    signature = hmac.new(
        key.encode(),
        timestamp.encode() + b"." + request_nonce.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return httpx.post(
        f"{settings.hermes_runtime_url.rstrip('/')}{path}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hermes-Timestamp": timestamp,
            "X-Hermes-Request-Id": request_nonce,
            "X-Hermes-Signature": signature,
        },
        timeout=timeout,
    )


def run_hermes(
    settings: Settings,
    *,
    profile: str,
    payload: dict[str, Any],
    request_id: str,
    provider_config: dict[str, str],
) -> dict[str, Any]:
    try:
        response = _signed_request(
            settings,
            path="/v1/runs",
            payload={
                "profile": profile,
                "payload": payload,
                "request_id": request_id,
                "provider_config": provider_config,
            },
            timeout=settings.hermes_timeout_seconds + 10,
        )
    except httpx.HTTPError as exc:
        raise HermesRuntimeError("hermes_unavailable", str(exc)) from exc
    if response.status_code == 503:
        raise HermesRuntimeError(
            "model_not_configured",
            "Anspire 模型凭证尚未配置",
            permanent=True,
        )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        raise HermesRuntimeError(
            "hermes_failed",
            f"Hermes 执行失败：{str(detail)[:1600]}",
            permanent=response.status_code in {400, 401, 403, 422},
        )
    return response.json()


def test_anspire_provider(
    settings: Settings,
    provider_config: dict[str, str],
) -> dict[str, Any]:
    try:
        response = _signed_request(
            settings,
            path="/v1/provider-test",
            payload={"provider_config": provider_config},
            timeout=min(settings.hermes_timeout_seconds, 60) + 5,
        )
    except httpx.HTTPError as exc:
        raise HermesRuntimeError("hermes_unavailable", str(exc)) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        raise HermesRuntimeError(
            "anspire_connection_failed",
            f"Anspire 连接测试失败：{str(detail)[:1200]}",
            permanent=response.status_code in {400, 401, 403, 422},
        )
    return response.json()


def parse_json_response(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        value = value.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HermesRuntimeError(
            "hermes_invalid_route",
            "Hermes 路由返回了无效结构",
        ) from exc
    if not isinstance(parsed, dict):
        raise HermesRuntimeError("hermes_invalid_route", "Hermes 路由结构无效")
    return parsed
