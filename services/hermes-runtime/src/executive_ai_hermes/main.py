from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ANSPIRE_ENDPOINT_URL = "https://open-gateway.anspire.ai/v6"
ANSPIRE_MODEL_IDS = frozenset(
    {
        "claude-fable-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "doubao-seed-2-1-pro",
        "doubao-seed-2-1-turbo",
        "doubao-seed-1.6-flash",
        "doubao-seed-1.8",
        "doubao-seed-2.0-code",
        "doubao-seed-2.0-lite",
        "doubao-seed-2.0-mini",
        "doubao-seed-2.0-pro",
        "doubao-seed-character",
        "doubao-seed-evolving",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "glm-5.2",
        "glm-5.1",
        "kimi-k2.5",
        "minimax-m2.7",
        "minimax-m2.5",
        "qwen3.5-plus",
        "qwen3.5-flash",
        "qwen3.5-397b-a17b",
        "qwen3.5-122b-a10b",
        "qwen3.5-35b-a3b",
        "qwen3.5-27b",
        "qwen3.7-max",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    hermes_timeout_seconds: int = 120
    hermes_max_concurrent_runs: int = 2
    hermes_runtime_hmac_key: SecretStr = SecretStr("")


settings = Settings()
app = FastAPI(title="Executive AI Anspire Hermes Runtime", docs_url=None, redoc_url=None)
_run_slots = asyncio.Semaphore(max(settings.hermes_max_concurrent_runs, 1))
_replay_lock = asyncio.Lock()
_recent_request_ids: dict[str, int] = {}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,200}$")

PROFILE_INSTRUCTIONS = {
    "route": """
你是企业经营工作台的强制路由器。只输出一个 JSON 对象，不要解释。
route 只能是 data、document、mixed 或 clarification。
tool 只能是输入中 allowed_tools 之一，或 null。
数字、经营、商机、项目、回款、目标问题走 data；当前会话文件走 document；两者都明确需要走 mixed。
仅在确实缺少不可推断的事业部范围时走 clarification。
必须返回 route, tool, rewritten_query, reason, confidence，
以及 clarification_question, clarification_options。
confidence 是 0 到 1 的小数。
""".strip(),
    "data": """
你是董事长的高级经营研究员。你只能使用输入中 authorized_result 里的数据，不得补造数字。
如果输入同时包含 current_conversation_chunks，
只能把这些当前会话文件片段与 authorized_result 结合分析，
并为文件结论标注提供的文件名及页码、工作表区域或幻灯片定位；不得检索或引用其他会话文件。
用简洁、准确、可行动的中文回答。先给结论，再给关键数字和必要的建议。
必须说明数据时间与来源；任何数据域为 stale 或 failed 时必须明确提醒。
输入为演示模拟数据时，不得称为客户真实经营数据。
""".strip(),
    "document": """
你是董事长的文件研究员。只根据 current_conversation_chunks 回答，不得引用其他会话或补造内容。
先给结论，再列出依据。对每个关键结论使用提供的文件名与页码、工作表区域或幻灯片定位。
证据不足时直接说明，不得猜测。
""".strip(),
}


class ProviderConfig(BaseModel):
    provider: Literal["anspire"]
    endpoint_url: Literal[ANSPIRE_ENDPOINT_URL]
    model_id: str = Field(min_length=1, max_length=100)
    api_key: SecretStr

    @field_validator("model_id")
    @classmethod
    def approved_model(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ANSPIRE_MODEL_IDS:
            raise ValueError("model is not approved for the Anspire production channel")
        return normalized

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if len(raw) < 16 or len(raw) > 512 or any(char.isspace() for char in raw):
            raise ValueError("invalid Anspire API key")
        return SecretStr(raw)


class RunRequest(BaseModel):
    profile: Literal["route", "data", "document"]
    payload: dict[str, Any]
    request_id: str = Field(min_length=1, max_length=200)
    provider_config: ProviderConfig


class ProviderTestRequest(BaseModel):
    provider_config: ProviderConfig


class RunResponse(BaseModel):
    text: str
    usage: dict[str, Any]
    model: str
    provider: Literal["anspire"] = "anspire"
    runtime_version: str = "0.19.0"


async def _verify_internal_signature(request: Request) -> None:
    key = settings.hermes_runtime_hmac_key.get_secret_value()
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="internal runtime key is not configured")
    timestamp = request.headers.get("X-Hermes-Timestamp", "")
    request_id = request.headers.get("X-Hermes-Request-Id", "")
    signature = request.headers.get("X-Hermes-Signature", "")
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise HTTPException(status_code=401, detail="invalid internal request id")
    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid internal signature") from exc
    if abs(int(time.time()) - request_time) > 60:
        raise HTTPException(status_code=401, detail="expired internal signature")
    body = await request.body()
    expected = hmac.new(
        key.encode(),
        timestamp.encode() + b"." + request_id.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid internal signature")
    async with _replay_lock:
        cutoff = int(time.time()) - 60
        expired = [key for key, seen_at in _recent_request_ids.items() if seen_at < cutoff]
        for key in expired:
            _recent_request_ids.pop(key, None)
        if request_id in _recent_request_ids:
            raise HTTPException(status_code=409, detail="duplicate internal request")
        _recent_request_ids[request_id] = request_time


async def _post_anspire(config: ProviderConfig) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{ANSPIRE_ENDPOINT_URL}/chat/completions",
            headers={
                "Authorization": config.api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            json={
                "model": config.model_id,
                "stream": False,
                "messages": [
                    {"role": "system", "content": "Reply with only: OK"},
                    {"role": "user", "content": "connection test"},
                ],
                "max_tokens": 8,
                "temperature": 0,
            },
            timeout=min(settings.hermes_timeout_seconds, 60),
        )


async def _run_hermes_process(
    command: list[str],
    *,
    environment: dict[str, str],
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        cwd=tempfile.gettempdir(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.hermes_timeout_seconds,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise HTTPException(status_code=504, detail="Hermes run timed out") from exc
    return process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


@app.get("/health")
def health() -> dict[str, str]:
    if len(settings.hermes_runtime_hmac_key.get_secret_value()) < 32:
        raise HTTPException(status_code=503, detail="internal runtime key is not configured")
    return {
        "status": "ok",
        "provider": "anspire-only",
        "hermes_version": "0.19.0",
    }


@app.post("/v1/provider-test")
async def provider_test(request: Request, payload: ProviderTestRequest) -> dict[str, Any]:
    await _verify_internal_signature(request)
    config = payload.provider_config
    started = time.monotonic()
    try:
        response = await _post_anspire(config)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Anspire gateway is unavailable") from exc
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            detail = "Anspire 拒绝了该凭证，请确认 API Key 有效且已开通所选模型"
        elif response.status_code == 404:
            detail = "所选 Anspire 模型暂不可用，请重新选择模型后测试"
        elif response.status_code == 429:
            detail = "Anspire 当前限流或账户额度不足，请稍后重试并检查账户状态"
        elif response.status_code >= 500:
            detail = "Anspire 网关暂时不可用，请稍后重试"
        else:
            detail = "Anspire 连接测试未通过，请检查凭证与模型权限"
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        result = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Anspire returned an invalid response") from exc
    if not isinstance(result.get("choices"), list):
        raise HTTPException(status_code=502, detail="Anspire response does not contain choices")
    return {"status": "success", "latency_ms": latency_ms, "model": config.model_id}


@app.post("/v1/runs", response_model=RunResponse)
async def run(request: Request, payload: RunRequest) -> RunResponse:
    await _verify_internal_signature(request)
    config = payload.provider_config
    prompt = (
        PROFILE_INSTRUCTIONS[payload.profile]
        + "\n\n<authorized_input>\n"
        + json.dumps(payload.payload, ensure_ascii=False, separators=(",", ":"))
        + "\n</authorized_input>"
    )
    environment = os.environ.copy()
    # Hermes 0.19 routes arbitrary OpenAI-compatible gateways through its
    # `custom` provider. The endpoint and credential are supplied only to the
    # short-lived subprocess and never persisted in Hermes user config.
    environment["ANSPIRE_API_KEY"] = config.api_key.get_secret_value()
    environment["CUSTOM_BASE_URL"] = ANSPIRE_ENDPOINT_URL
    with tempfile.NamedTemporaryFile(suffix=".json") as usage_file:
        command = [
            "hermes",
            "--oneshot",
            prompt,
            "--model",
            config.model_id,
            "--provider",
            "custom",
            "--toolsets",
            "context_engine",
            "--usage-file",
            usage_file.name,
            "--safe-mode",
            "--ignore-rules",
        ]
        async with _run_slots:
            returncode, stdout, stderr = await _run_hermes_process(
                command,
                environment=environment,
            )
        if returncode != 0:
            detail = (stderr or "Hermes run failed").strip()[-2000:]
            detail = detail.replace(config.api_key.get_secret_value(), "[redacted]")
            raise HTTPException(status_code=502, detail=detail)
        usage: dict[str, Any] = {}
        try:
            usage_file.seek(0)
            usage = json.load(usage_file)
        except (json.JSONDecodeError, OSError):
            pass
    return RunResponse(
        text=stdout.strip(),
        usage=usage,
        model=config.model_id,
    )
