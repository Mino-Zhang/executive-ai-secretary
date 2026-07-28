from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class DataSourceConfigurationError(ValueError):
    def __init__(self, code: str, message: str, *, path: tuple[str, ...] = ()) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


_WRITE_TOP_LEVEL_KEYS = {
    "activation_policy",
    "classification",
    "connection_mode",
    "database",
    "database_version",
    "experience_weights_percent",
    "folder_token",
    "read_only",
    "schema",
    "source_contract",
    "tables",
    "tls_active",
}
_TABLE_DOMAINS = {"opportunity", "delivery", "collection"}
_TABLE_KEYS = {"app_token", "table_id"}
_WEIGHT_KEYS = {"high", "medium", "low"}
_SAFE_RESOURCE_TOKEN_PATHS = {
    ("folder_token",),
    *(("tables", domain, "app_token") for domain in _TABLE_DOMAINS),
}
_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "app_secret",
    "appsecret",
    "auth_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "clientsecret",
    "connection_string",
    "connection_uri",
    "connection_url",
    "credential",
    "credentials",
    "database_url",
    "dsn",
    "key",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "pwd",
    "secret",
    "secret_key",
    "token",
}
_SENSITIVE_PARTS = {"credential", "credentials", "password", "passwd", "pwd", "secret"}
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SAFE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}$")
_SAFE_DATABASE_VERSION = re.compile(r"^[A-Za-z0-9 .(),+_-]+$")
_MAX_CONFIGURATION_ITEMS = 64
_MAX_STRING_LENGTH = 240


def _normalized_key(value: object) -> str:
    raw = _CAMEL_CASE_BOUNDARY.sub("_", str(value).strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    parts = set(normalized.split("_"))
    if parts & _SENSITIVE_PARTS:
        return True
    return normalized.endswith(("_key", "_token"))


def _reject_sensitive_keys(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = (*path, key)
            if child_path not in _SAFE_RESOURCE_TOKEN_PATHS and _is_sensitive_key(key):
                raise DataSourceConfigurationError(
                    "data_source_configuration_sensitive_key",
                    "数据源连接凭证不得写入通用配置，请使用独立密钥引用",
                    path=child_path,
                )
            _reject_sensitive_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, (*path, str(index)))


def _require_object(value: object, *, path: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataSourceConfigurationError(
            "data_source_configuration_invalid",
            "数据源配置结构无效",
            path=path,
        )
    return value


def _require_string(
    value: object,
    *,
    path: tuple[str, ...],
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > _MAX_STRING_LENGTH:
        raise DataSourceConfigurationError(
            "data_source_configuration_invalid",
            "数据源配置字段必须是有效的非空文本",
            path=path,
        )
    normalized = value.strip()
    if pattern is not None and not pattern.fullmatch(normalized):
        raise DataSourceConfigurationError(
            "data_source_configuration_invalid",
            "数据源配置字段格式无效",
            path=path,
        )
    return normalized


def _reject_unknown_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    *,
    path: tuple[str, ...],
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise DataSourceConfigurationError(
            "data_source_configuration_key_not_allowed",
            "数据源配置包含不受支持的字段",
            path=(*path, unknown[0]),
        )


def validate_data_source_configuration(value: object) -> dict[str, Any]:
    """Validate and canonicalize configuration accepted by the generic admin API.

    Database credentials and API secrets must be stored behind ``secret_reference_key``.
    Feishu Base tokens are non-secret resource identifiers required by the connector; they
    may be stored in the configuration but are deliberately removed from API responses.
    """

    configuration = _require_object(value, path=("configuration_json",))
    _reject_sensitive_keys(configuration)
    _reject_unknown_keys(configuration, _WRITE_TOP_LEVEL_KEYS, path=("configuration_json",))
    if len(configuration) > _MAX_CONFIGURATION_ITEMS:
        raise DataSourceConfigurationError(
            "data_source_configuration_invalid",
            "数据源配置字段数量超过限制",
            path=("configuration_json",),
        )

    output: dict[str, Any] = {}
    for key in ("database", "classification"):
        if key in configuration:
            output[key] = _require_string(
                configuration[key],
                path=("configuration_json", key),
                pattern=_SAFE_IDENTIFIER,
            )

    if "schema" in configuration:
        output["schema"] = _require_string(
            configuration["schema"],
            path=("configuration_json", "schema"),
            pattern=_SAFE_SCHEMA,
        )

    if "connection_mode" in configuration:
        connection_mode = _require_string(
            configuration["connection_mode"],
            path=("configuration_json", "connection_mode"),
        )
        if connection_mode not in {"internal", "external"}:
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "数据源连接模式只允许 internal 或 external",
                path=("configuration_json", "connection_mode"),
            )
        output["connection_mode"] = connection_mode

    if "folder_token" in configuration:
        output["folder_token"] = _require_string(
            configuration["folder_token"],
            path=("configuration_json", "folder_token"),
            pattern=_SAFE_IDENTIFIER,
        )

    if "activation_policy" in configuration:
        activation_policy = _require_string(
            configuration["activation_policy"],
            path=("configuration_json", "activation_policy"),
        )
        if activation_policy != "all_three_atomic":
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "当前版本只支持三表原子切换",
                path=("configuration_json", "activation_policy"),
            )
        output["activation_policy"] = activation_policy

    if "source_contract" in configuration:
        output["source_contract"] = _require_string(
            configuration["source_contract"],
            path=("configuration_json", "source_contract"),
            pattern=_SAFE_VERSION,
        )

    if "database_version" in configuration:
        output["database_version"] = _require_string(
            configuration["database_version"],
            path=("configuration_json", "database_version"),
            pattern=_SAFE_DATABASE_VERSION,
        )

    for key in ("read_only", "tls_active"):
        if key not in configuration:
            continue
        if not isinstance(configuration[key], bool):
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "数据源状态字段必须是布尔值",
                path=("configuration_json", key),
            )
        output[key] = configuration[key]

    if "tables" in configuration:
        raw_tables = _require_object(
            configuration["tables"],
            path=("configuration_json", "tables"),
        )
        _reject_unknown_keys(
            raw_tables,
            _TABLE_DOMAINS,
            path=("configuration_json", "tables"),
        )
        tables: dict[str, dict[str, str]] = {}
        for domain, raw_binding in raw_tables.items():
            binding = _require_object(
                raw_binding,
                path=("configuration_json", "tables", str(domain)),
            )
            _reject_unknown_keys(
                binding,
                _TABLE_KEYS,
                path=("configuration_json", "tables", str(domain)),
            )
            normalized_binding: dict[str, str] = {}
            for key in _TABLE_KEYS:
                if key in binding:
                    normalized_binding[key] = _require_string(
                        binding[key],
                        path=("configuration_json", "tables", str(domain), key),
                        pattern=_SAFE_IDENTIFIER,
                    )
            tables[str(domain)] = normalized_binding
        output["tables"] = tables

    if "experience_weights_percent" in configuration:
        raw_weights = _require_object(
            configuration["experience_weights_percent"],
            path=("configuration_json", "experience_weights_percent"),
        )
        _reject_unknown_keys(
            raw_weights,
            _WEIGHT_KEYS,
            path=("configuration_json", "experience_weights_percent"),
        )
        if set(raw_weights) != _WEIGHT_KEYS:
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "经验权重必须同时包含 high、medium 和 low",
                path=("configuration_json", "experience_weights_percent"),
            )
        if any(
            isinstance(raw_weights[key], bool)
            or not isinstance(raw_weights[key], (int, float))
            or not 0 <= float(raw_weights[key]) <= 100
            for key in _WEIGHT_KEYS
        ):
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "经验权重必须是 0 至 100 之间的数值",
                path=("configuration_json", "experience_weights_percent"),
            )
        weights = {key: float(raw_weights[key]) for key in ("high", "medium", "low")}
        if not weights["high"] >= weights["medium"] >= weights["low"]:
            raise DataSourceConfigurationError(
                "data_source_configuration_invalid",
                "经验权重必须满足 high ≥ medium ≥ low",
                path=("configuration_json", "experience_weights_percent"),
            )
        output["experience_weights_percent"] = weights

    return output


def merge_data_source_configuration(
    existing: object,
    update: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge a validated update while preserving hidden connector identifiers.

    The generic read API intentionally omits Feishu resource tokens. Treating a returned
    configuration as a full replacement must therefore not erase those bindings. Unknown
    legacy keys and credential-shaped fields are not carried forward by an explicit config
    update.
    """

    current = existing if isinstance(existing, Mapping) else {}
    # Start from the fail-closed public projection so an allowed legacy key
    # carrying an unsafe value (for example a database URL with a password) is
    # also removed during the next explicit edit.
    merged: dict[str, Any] = public_data_source_configuration(current)
    folder_token = current.get("folder_token")
    if (
        isinstance(folder_token, str)
        and len(folder_token) <= _MAX_STRING_LENGTH
        and _SAFE_IDENTIFIER.fullmatch(folder_token)
    ):
        merged["folder_token"] = folder_token
    merged.update({key: value for key, value in update.items() if key != "tables"})

    current_tables = current.get("tables")
    updated_tables = update.get("tables")
    if isinstance(current_tables, Mapping) or isinstance(updated_tables, Mapping):
        tables: dict[str, dict[str, Any]] = {}
        for domain in _TABLE_DOMAINS:
            binding: dict[str, Any] = {}
            current_binding = (
                current_tables.get(domain) if isinstance(current_tables, Mapping) else None
            )
            if isinstance(current_binding, Mapping):
                for key in _TABLE_KEYS:
                    item = current_binding.get(key)
                    if (
                        isinstance(item, str)
                        and len(item) <= _MAX_STRING_LENGTH
                        and _SAFE_IDENTIFIER.fullmatch(item)
                    ):
                        binding[key] = item
            updated_binding = (
                updated_tables.get(domain) if isinstance(updated_tables, Mapping) else None
            )
            if isinstance(updated_binding, Mapping):
                binding.update(updated_binding)
            if binding:
                tables[domain] = binding
        if tables:
            merged["tables"] = tables
    return validate_data_source_configuration(merged)


def public_data_source_configuration(value: object) -> dict[str, Any]:
    """Return a fail-closed view of legacy or current configuration.

    Unknown legacy fields remain stored for runtime compatibility, but are not returned by
    the generic admin API. Resource tokens and every credential-shaped field are omitted.
    """

    if not isinstance(value, Mapping):
        return {}

    output: dict[str, Any] = {}
    string_rules: tuple[tuple[str, re.Pattern[str] | None, set[str] | None], ...] = (
        ("activation_policy", None, {"all_three_atomic"}),
        ("classification", _SAFE_IDENTIFIER, None),
        ("connection_mode", None, {"internal", "external"}),
        ("database", _SAFE_IDENTIFIER, None),
        ("database_version", _SAFE_DATABASE_VERSION, None),
        ("schema", _SAFE_SCHEMA, None),
        ("source_contract", _SAFE_VERSION, None),
    )
    for key, pattern, allowed_values in string_rules:
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > _MAX_STRING_LENGTH:
            continue
        if pattern is not None and not pattern.fullmatch(item):
            continue
        if allowed_values is not None and item not in allowed_values:
            continue
        output[key] = item

    for key in ("read_only", "tls_active"):
        item = value.get(key)
        if isinstance(item, bool):
            output[key] = item

    raw_weights = value.get("experience_weights_percent")
    if isinstance(raw_weights, Mapping):
        weights = {
            key: raw_weights[key]
            for key in ("high", "medium", "low")
            if isinstance(raw_weights.get(key), (int, float))
            and not isinstance(raw_weights.get(key), bool)
        }
        if (
            set(weights) == _WEIGHT_KEYS
            and all(0 <= float(item) <= 100 for item in weights.values())
            and float(weights["high"]) >= float(weights["medium"]) >= float(weights["low"])
        ):
            output["experience_weights_percent"] = weights

    raw_tables = value.get("tables")
    if isinstance(raw_tables, Mapping):
        tables: dict[str, dict[str, str]] = {}
        for domain in ("opportunity", "delivery", "collection"):
            raw_binding = raw_tables.get(domain)
            if not isinstance(raw_binding, Mapping):
                continue
            table_id = raw_binding.get("table_id")
            if (
                isinstance(table_id, str)
                and table_id
                and len(table_id) <= _MAX_STRING_LENGTH
                and _SAFE_IDENTIFIER.fullmatch(table_id)
            ):
                tables[domain] = {"table_id": table_id}
        if tables:
            output["tables"] = tables

    return output
