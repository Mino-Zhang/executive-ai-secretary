from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import Settings
from .errors import AppError

password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_new_password(password: str, settings: Settings) -> None:
    failures: list[str] = []
    if len(password) < settings.password_min_length:
        failures.append(f"至少 {settings.password_min_length} 个字符")
    if not re.search(r"[A-Z]", password):
        failures.append("至少一个大写字母")
    if not re.search(r"[a-z]", password):
        failures.append("至少一个小写字母")
    if not re.search(r"\d", password):
        failures.append("至少一个数字")
    if not re.search(r"[^A-Za-z0-9]", password):
        failures.append("至少一个特殊字符")
    if failures:
        raise AppError(422, "weak_password", "密码强度不足", failures)


def generate_token(bytes_length: int = 32) -> str:
    return secrets.token_urlsafe(bytes_length)


def token_hash(token: str, secret: str = "") -> str:
    value = f"{secret}:{token}".encode()
    return hashlib.sha256(value).hexdigest()


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def session_expirations(settings: Settings) -> tuple[datetime, datetime]:
    now = utc_now()
    return (
        now + timedelta(seconds=settings.session_ttl_seconds),
        now + timedelta(seconds=min(settings.session_idle_seconds, settings.session_ttl_seconds)),
    )


class RateLimiter(Protocol):
    def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]: ...


class InMemoryRateLimiter:
    """Single-process adapter; replace with Redis without changing router contracts."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                return False, retry_after
            events.append(now)
            return True, 0


rate_limiter: RateLimiter = InMemoryRateLimiter()


@dataclass(frozen=True)
class SessionTokens:
    session_token: str
    csrf_token: str


def new_session_tokens() -> SessionTokens:
    return SessionTokens(session_token=generate_token(48), csrf_token=generate_token(32))
