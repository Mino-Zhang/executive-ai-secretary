from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .config import Settings


class CapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityClaims:
    enterprise_id: uuid.UUID
    user_id: uuid.UUID
    organization_unit_ids: frozenset[uuid.UUID]
    tools: frozenset[str]
    message_id: uuid.UUID
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_capability_token(
    *,
    settings: Settings,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    organization_unit_ids: set[uuid.UUID],
    tools: set[str],
    message_id: uuid.UUID,
) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "ent": str(enterprise_id),
        "sub": str(user_id),
        "org": sorted(str(value) for value in organization_unit_ids),
        "tools": sorted(tools),
        "msg": str(message_id),
        "iat": now,
        "exp": now + settings.capability_token_ttl_seconds,
        "nonce": uuid.uuid4().hex,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(
        settings.capability_hmac_key.get_secret_value().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_encode(signature)}"


def verify_capability_token(token: str, settings: Settings) -> CapabilityClaims:
    try:
        encoded, signature = token.split(".", 1)
        decoded_signature = _decode(signature)
        expected = hmac.new(
            settings.capability_hmac_key.get_secret_value().encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        # Reject non-canonical Base64URL encodings as well as changed bytes. Without
        # this check, changing unused padding bits in the final character can produce
        # a different token string that decodes to the same HMAC bytes.
        if (
            len(decoded_signature) != hashlib.sha256().digest_size
            or _encode(decoded_signature) != signature
            or not hmac.compare_digest(expected, decoded_signature)
        ):
            raise CapabilityError("capability signature is invalid")
        decoded_payload = _decode(encoded)
        if _encode(decoded_payload) != encoded:
            raise CapabilityError("capability token is malformed")
        payload: dict[str, Any] = json.loads(decoded_payload)
        if payload.get("v") != 1 or int(payload["exp"]) < int(time.time()):
            raise CapabilityError("capability token is expired or unsupported")
        organization_ids = frozenset(uuid.UUID(value) for value in payload["org"])
        tools = frozenset(str(value) for value in payload["tools"])
        if not organization_ids or not tools:
            raise CapabilityError("capability token has no scope")
        return CapabilityClaims(
            enterprise_id=uuid.UUID(payload["ent"]),
            user_id=uuid.UUID(payload["sub"]),
            organization_unit_ids=organization_ids,
            tools=tools,
            message_id=uuid.UUID(payload["msg"]),
            expires_at=int(payload["exp"]),
        )
    except CapabilityError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CapabilityError("capability token is malformed") from exc
