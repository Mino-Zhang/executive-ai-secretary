from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings
from .models import ModelProviderConfig

ANSPIRE_PROVIDER = "anspire"
ANSPIRE_ENDPOINT_URL = "https://open-gateway.anspire.ai/v6"
DEFAULT_ANSPIRE_MODEL = "glm-5.2"

# This is deliberately a release-controlled allowlist. The management surface
# cannot redirect enterprise credentials to an arbitrary OpenAI-compatible URL.
ANSPIRE_CHAT_MODELS: tuple[dict[str, str], ...] = (
    {"id": "glm-5.2", "name": "GLM 5.2", "family": "GLM", "profile": "默认经营研究模型"},
    {"id": "gpt-5.6-sol", "name": "GPT 5.6 Sol", "family": "GPT", "profile": "旗舰复杂推理"},
    {"id": "gpt-5.6-terra", "name": "GPT 5.6 Terra", "family": "GPT", "profile": "平衡型知识工作"},
    {"id": "gpt-5.6-luna", "name": "GPT 5.6 Luna", "family": "GPT", "profile": "低延迟日常任务"},
    {"id": "gpt-5.5", "name": "GPT 5.5", "family": "GPT", "profile": "通用复杂推理"},
    {"id": "gpt-5.4", "name": "GPT 5.4", "family": "GPT", "profile": "高级经营研究"},
    {"id": "gpt-5.4-mini", "name": "GPT 5.4 Mini", "family": "GPT", "profile": "轻量快速分析"},
    {
        "id": "claude-fable-5",
        "name": "Claude Fable 5",
        "family": "Claude",
        "profile": "旗舰知识工作",
    },
    {
        "id": "claude-opus-4-8",
        "name": "Claude Opus 4.8",
        "family": "Claude",
        "profile": "深度研究与推理",
    },
    {
        "id": "claude-opus-4-7",
        "name": "Claude Opus 4.7",
        "family": "Claude",
        "profile": "复杂长程分析",
    },
    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "family": "Claude", "profile": "复杂推理"},
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "family": "Claude",
        "profile": "质量与速度平衡",
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "name": "Claude Haiku 4.5",
        "family": "Claude",
        "profile": "快速日常问答",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro Preview",
        "family": "Gemini",
        "profile": "长上下文复杂分析",
    },
    {
        "id": "gemini-3-flash-preview",
        "name": "Gemini 3 Flash Preview",
        "family": "Gemini",
        "profile": "快速多模态分析",
    },
    {
        "id": "gemini-3.5-flash",
        "name": "Gemini 3.5 Flash",
        "family": "Gemini",
        "profile": "高吞吐问答",
    },
    {"id": "glm-5.1", "name": "GLM 5.1", "family": "GLM", "profile": "稳定通用"},
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "family": "DeepSeek",
        "profile": "深度推理",
    },
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "family": "DeepSeek",
        "profile": "快速推理",
    },
    {
        "id": "doubao-seed-2-1-pro",
        "name": "豆包 Seed 2.1 Pro",
        "family": "豆包",
        "profile": "复杂经营分析",
    },
    {
        "id": "doubao-seed-2-1-turbo",
        "name": "豆包 Seed 2.1 Turbo",
        "family": "豆包",
        "profile": "低延迟日常问数",
    },
    {
        "id": "doubao-seed-evolving",
        "name": "豆包 Seed Evolving",
        "family": "豆包",
        "profile": "自适应复杂任务",
    },
    {
        "id": "doubao-seed-character",
        "name": "豆包 Seed Character",
        "family": "豆包",
        "profile": "角色化交互",
    },
    {
        "id": "doubao-seed-2.0-pro",
        "name": "豆包 Seed 2.0 Pro",
        "family": "豆包",
        "profile": "稳定复杂分析",
    },
    {
        "id": "doubao-seed-2.0-lite",
        "name": "豆包 Seed 2.0 Lite",
        "family": "豆包",
        "profile": "轻量日常问答",
    },
    {
        "id": "doubao-seed-2.0-mini",
        "name": "豆包 Seed 2.0 Mini",
        "family": "豆包",
        "profile": "低成本高并发",
    },
    {
        "id": "doubao-seed-2.0-code",
        "name": "豆包 Seed 2.0 Code",
        "family": "豆包",
        "profile": "数据与代码分析",
    },
    {"id": "doubao-seed-1.8", "name": "豆包 Seed 1.8", "family": "豆包", "profile": "稳定通用"},
    {
        "id": "doubao-seed-1.6-flash",
        "name": "豆包 Seed 1.6 Flash",
        "family": "豆包",
        "profile": "极速问答",
    },
    {"id": "kimi-k2.5", "name": "Kimi K2.5", "family": "Kimi", "profile": "长文档分析"},
    {"id": "minimax-m2.7", "name": "MiniMax M2.7", "family": "MiniMax", "profile": "复杂对话"},
    {"id": "minimax-m2.5", "name": "MiniMax M2.5", "family": "MiniMax", "profile": "稳定通用"},
    {"id": "qwen3.5-plus", "name": "Qwen 3.5 Plus", "family": "Qwen", "profile": "综合推理"},
    {"id": "qwen3.5-flash", "name": "Qwen 3.5 Flash", "family": "Qwen", "profile": "高并发低延迟"},
    {
        "id": "qwen3.5-397b-a17b",
        "name": "Qwen 3.5 397B A17B",
        "family": "Qwen",
        "profile": "大规模专家推理",
    },
    {
        "id": "qwen3.5-122b-a10b",
        "name": "Qwen 3.5 122B A10B",
        "family": "Qwen",
        "profile": "复杂通用分析",
    },
    {
        "id": "qwen3.5-35b-a3b",
        "name": "Qwen 3.5 35B A3B",
        "family": "Qwen",
        "profile": "平衡型推理",
    },
    {"id": "qwen3.5-27b", "name": "Qwen 3.5 27B", "family": "Qwen", "profile": "轻量推理"},
    {"id": "qwen3.7-max", "name": "Qwen 3.7 Max", "family": "Qwen", "profile": "旗舰综合推理"},
)

# The backend exposes the complete provider catalog returned by Anspire's
# global gateway. Only chat/reasoning models are selectable by the Hermes
# answer channel; keeping the other modalities visible in the controlled
# catalog avoids silently presenting a partial provider integration.
ANSPIRE_NON_CHAT_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "cinema-generate-2.0",
        "name": "Cinema Generate 2.0",
        "family": "视频",
        "profile": "视频生成",
    },
    {
        "id": "doubao-seedance-1.5-pro",
        "name": "豆包 Seedance 1.5 Pro",
        "family": "视频",
        "profile": "视频生成",
    },
    {
        "id": "doubao-seedance-2.0-fast",
        "name": "豆包 Seedance 2.0 Fast",
        "family": "视频",
        "profile": "快速视频生成",
    },
    {
        "id": "doubao-seedance-2.0-mini",
        "name": "豆包 Seedance 2.0 Mini",
        "family": "视频",
        "profile": "轻量视频生成",
    },
    {
        "id": "doubao-seedream-4.5",
        "name": "豆包 Seedream 4.5",
        "family": "图像",
        "profile": "图像生成",
    },
    {
        "id": "doubao-seedream-5.0-lite",
        "name": "豆包 Seedream 5.0 Lite",
        "family": "图像",
        "profile": "轻量图像生成",
    },
    {
        "id": "doubao-seedream-5.0-pro",
        "name": "豆包 Seedream 5.0 Pro",
        "family": "图像",
        "profile": "高质量图像生成",
    },
    {"id": "gpt-image-2", "name": "GPT Image 2", "family": "图像", "profile": "图像生成"},
    {"id": "nano-banana-2", "name": "Nano Banana 2", "family": "图像", "profile": "图像生成与编辑"},
    {
        "id": "nano-banana-pro",
        "name": "Nano Banana Pro",
        "family": "图像",
        "profile": "专业图像生成与编辑",
    },
    {"id": "qwen3-rerank", "name": "Qwen 3 Rerank", "family": "检索", "profile": "文本重排序"},
    {
        "id": "qwen3-vl-embedding",
        "name": "Qwen 3 VL Embedding",
        "family": "向量",
        "profile": "多模态向量化",
    },
    {
        "id": "qwen3-vl-rerank",
        "name": "Qwen 3 VL Rerank",
        "family": "检索",
        "profile": "多模态重排序",
    },
    {
        "id": "text-embedding-v4",
        "name": "Text Embedding V4",
        "family": "向量",
        "profile": "文本向量化",
    },
)

ANSPIRE_MODELS: tuple[dict[str, object], ...] = tuple(
    {**item, "capability": "chat", "selectable": True} for item in ANSPIRE_CHAT_MODELS
) + tuple(
    {
        **item,
        "capability": (
            "video"
            if item["family"] == "视频"
            else "image"
            if item["family"] == "图像"
            else "embedding"
            if item["family"] == "向量"
            else "rerank"
        ),
        "selectable": False,
    }
    for item in ANSPIRE_NON_CHAT_MODELS
)
ANSPIRE_MODEL_IDS = frozenset(item["id"] for item in ANSPIRE_CHAT_MODELS)


class AnspireConfigurationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str
    hint: str
    key_version: str


def validate_anspire_model(model_id: str) -> str:
    normalized = model_id.strip().lower()
    if normalized not in ANSPIRE_MODEL_IDS:
        raise AnspireConfigurationError(
            "anspire_model_not_allowed",
            "所选模型不在当前 Anspire 生产白名单中",
        )
    return normalized


def validate_anspire_api_key(api_key: str) -> str:
    normalized = api_key.strip()
    if len(normalized) < 16 or len(normalized) > 512 or any(char.isspace() for char in normalized):
        raise AnspireConfigurationError("anspire_api_key_invalid", "Anspire API Key 格式无效")
    return normalized


def _aad(enterprise_id: uuid.UUID, key_version: str) -> bytes:
    return f"executive-ai\x00{enterprise_id}\x00{ANSPIRE_PROVIDER}\x00{key_version}".encode()


def encrypt_anspire_api_key(
    api_key: str,
    *,
    enterprise_id: uuid.UUID,
    settings: Settings,
) -> EncryptedCredential:
    normalized = validate_anspire_api_key(api_key)
    version = settings.integration_encryption_key_version
    nonce = os.urandom(12)
    ciphertext = AESGCM(settings.integration_encryption_keys()[version]).encrypt(
        nonce,
        normalized.encode(),
        _aad(enterprise_id, version),
    )
    return EncryptedCredential(
        ciphertext=base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
        hint=normalized[-4:],
        key_version=version,
    )


def decrypt_anspire_api_key(config: ModelProviderConfig, settings: Settings) -> str:
    if not config.api_key_ciphertext or not config.api_key_nonce:
        raise AnspireConfigurationError(
            "anspire_not_configured",
            "尚未配置 Anspire API Key",
        )
    encryption_key = settings.integration_encryption_keys().get(config.encryption_key_version)
    if encryption_key is None:
        raise AnspireConfigurationError(
            "anspire_key_version_unavailable",
            "Anspire 凭证所需的历史加密密钥未加载",
        )
    try:
        plaintext = AESGCM(encryption_key).decrypt(
            base64.urlsafe_b64decode(config.api_key_nonce.encode("ascii")),
            base64.urlsafe_b64decode(config.api_key_ciphertext.encode("ascii")),
            _aad(config.enterprise_id, config.encryption_key_version),
        )
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise AnspireConfigurationError(
            "anspire_credential_integrity_error",
            "Anspire 凭证完整性校验失败",
        ) from exc
    try:
        return validate_anspire_api_key(plaintext.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise AnspireConfigurationError(
            "anspire_credential_integrity_error",
            "Anspire 凭证无法解密",
        ) from exc


def masked_api_key(config: ModelProviderConfig | None) -> str | None:
    if config is None or not config.api_key_hint:
        return None
    return f"sk-••••••••{config.api_key_hint}"


def runtime_provider_config(config: ModelProviderConfig, settings: Settings) -> dict[str, str]:
    if config.provider != ANSPIRE_PROVIDER or config.endpoint_url != ANSPIRE_ENDPOINT_URL:
        raise AnspireConfigurationError(
            "anspire_provider_invalid",
            "模型配置不是受控的 Anspire 接入",
        )
    if not config.is_enabled or config.last_test_status != "success":
        raise AnspireConfigurationError(
            "anspire_not_enabled",
            "Anspire 模型尚未通过测试并启用",
        )
    return {
        "provider": ANSPIRE_PROVIDER,
        "endpoint_url": ANSPIRE_ENDPOINT_URL,
        "model_id": validate_anspire_model(config.model_id),
        "api_key": decrypt_anspire_api_key(config, settings),
    }
