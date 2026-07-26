from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Select, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuditChainHead, AuditEvent, DataScopeGrant, User

AUDIT_GENESIS_HASH = "0" * 64
GLOBAL_CHAIN_SCOPE = "global"


@dataclass(frozen=True)
class AuditChainVerification:
    valid: bool
    checked_count: int
    invalid_event_ids: list[uuid.UUID]
    errors: list[str]


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat()
    return str(value)


def chain_scope_for(enterprise_id: uuid.UUID | None) -> str:
    return f"enterprise:{enterprise_id}" if enterprise_id is not None else GLOBAL_CHAIN_SCOPE


def canonical_payload(event: AuditEvent) -> bytes:
    chained = (
        event.chain_scope is not None
        and event.chain_sequence is not None
        and event.previous_integrity_hash is not None
    )
    versioned = event.audit_key_version is not None
    payload = {
        "schema": "audit-v3" if versioned else ("audit-v2" if chained else "audit-v1"),
        "id": _string(event.id),
        "created_at": _string(event.created_at),
        "enterprise_id": _string(event.enterprise_id),
        "environment": event.environment,
        "actor_user_id": _string(event.actor_user_id),
        "actor_role": event.actor_role,
        "session_id": _string(event.session_id),
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "outcome": event.outcome,
        "failure_reason_code": event.failure_reason_code,
        "request_id": event.request_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "metadata": event.metadata_json or {},
        "scope_summary": event.scope_summary_json or {},
    }
    if chained:
        payload.update(
            {
                "chain_scope": event.chain_scope,
                "chain_sequence": event.chain_sequence,
                "previous_integrity_hash": event.previous_integrity_hash,
            }
        )
    if versioned:
        payload["audit_key_version"] = event.audit_key_version
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


def calculate_integrity_hash(event: AuditEvent, key: str) -> str:
    return hmac.new(key.encode(), canonical_payload(event), hashlib.sha256).hexdigest()


def _legacy_root(rows: list[tuple[uuid.UUID, str]]) -> str:
    digest = hashlib.sha256()
    for event_id, integrity_hash in rows:
        digest.update(str(event_id).encode())
        digest.update(b":")
        digest.update(integrity_hash.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _head_payload(head: Mapping[str, Any]) -> bytes:
    versioned = head.get("anchor_key_version") is not None
    payload = {
        "schema": "audit-chain-head-v2" if versioned else "audit-chain-head-v1",
        "chain_scope": head["chain_scope"],
        "legacy_event_count": head["legacy_event_count"],
        "legacy_root_hash": head["legacy_root_hash"],
        "last_sequence": head["last_sequence"],
        "last_integrity_hash": head["last_integrity_hash"],
    }
    if versioned:
        payload["anchor_key_version"] = head["anchor_key_version"]
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def calculate_chain_anchor(head: Mapping[str, Any], key: str) -> str:
    return hmac.new(key.encode(), _head_payload(head), hashlib.sha256).hexdigest()


def _legacy_statement(enterprise_id: uuid.UUID | None) -> Select:
    enterprise_filter = (
        AuditEvent.enterprise_id.is_(None)
        if enterprise_id is None
        else AuditEvent.enterprise_id == enterprise_id
    )
    return (
        select(AuditEvent.id, AuditEvent.integrity_hash)
        .where(AuditEvent.chain_scope.is_(None), enterprise_filter)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    )


def _legacy_rows(
    executor: Connection | Session,
    enterprise_id: uuid.UUID | None,
) -> list[tuple[uuid.UUID, str]]:
    return [
        (row.id, row.integrity_hash) for row in executor.execute(_legacy_statement(enterprise_id))
    ]


def _create_chain_head_if_missing(
    connection: Connection,
    enterprise_id: uuid.UUID | None,
    key: str,
    key_version: str,
) -> None:
    chain_scope = chain_scope_for(enterprise_id)
    legacy_rows = _legacy_rows(connection, enterprise_id)
    values: dict[str, Any] = {
        "chain_scope": chain_scope,
        "legacy_event_count": len(legacy_rows),
        "legacy_root_hash": _legacy_root(legacy_rows),
        "last_sequence": 0,
        "last_integrity_hash": AUDIT_GENESIS_HASH,
        "anchor_key_version": key_version,
        "updated_at": datetime.now(UTC),
    }
    values["anchor_hash"] = calculate_chain_anchor(values, key)
    if connection.dialect.name == "postgresql":
        statement = (
            postgresql_insert(AuditChainHead)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["chain_scope"])
        )
    elif connection.dialect.name == "sqlite":
        statement = (
            sqlite_insert(AuditChainHead)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["chain_scope"])
        )
    else:
        exists = connection.scalar(
            select(AuditChainHead.chain_scope).where(AuditChainHead.chain_scope == chain_scope)
        )
        if exists is not None:
            return
        statement = AuditChainHead.__table__.insert().values(**values)
    connection.execute(statement)


def _locked_head(connection: Connection, chain_scope: str) -> Mapping[str, Any]:
    statement = select(AuditChainHead.__table__).where(AuditChainHead.chain_scope == chain_scope)
    if connection.dialect.name == "postgresql":
        statement = statement.with_for_update()
    return connection.execute(statement).mappings().one()


def _append_to_chain(
    connection: Connection,
    event: AuditEvent,
    keys: Mapping[str, str],
    current_key_version: str,
    legacy_key_version: str,
) -> None:
    chain_scope = chain_scope_for(event.enterprise_id)
    current_key = keys[current_key_version]
    _create_chain_head_if_missing(
        connection,
        event.enterprise_id,
        current_key,
        current_key_version,
    )
    head = _locked_head(connection, chain_scope)
    head_key_version = str(head["anchor_key_version"] or legacy_key_version)
    head_key = keys.get(head_key_version)
    if head_key is None:
        raise RuntimeError(f"audit key version {head_key_version!r} is unavailable")
    expected_anchor = calculate_chain_anchor(head, head_key)
    if not hmac.compare_digest(expected_anchor, str(head["anchor_hash"])):
        raise RuntimeError(f"audit chain anchor integrity check failed for {chain_scope}")

    event.chain_scope = chain_scope
    event.chain_sequence = int(head["last_sequence"]) + 1
    event.previous_integrity_hash = str(head["last_integrity_hash"])
    event.audit_key_version = current_key_version
    event.integrity_hash = calculate_integrity_hash(event, current_key)

    next_head = {
        "chain_scope": chain_scope,
        "legacy_event_count": int(head["legacy_event_count"]),
        "legacy_root_hash": str(head["legacy_root_hash"]),
        "last_sequence": event.chain_sequence,
        "last_integrity_hash": event.integrity_hash,
        "anchor_key_version": current_key_version,
    }
    connection.execute(
        update(AuditChainHead)
        .where(AuditChainHead.chain_scope == chain_scope)
        .values(
            last_sequence=event.chain_sequence,
            last_integrity_hash=event.integrity_hash,
            anchor_key_version=current_key_version,
            anchor_hash=calculate_chain_anchor(next_head, current_key),
            updated_at=datetime.now(UTC),
        )
    )


def prepare_audit_event(connection: Connection, event: AuditEvent) -> None:
    settings = get_settings()
    if event.id is None:
        event.id = uuid.uuid4()
    if event.created_at is None:
        event.created_at = datetime.now(UTC)
    if not event.environment:
        event.environment = settings.app_env
    if not event.outcome:
        event.outcome = "success"
    if event.actor_user_id and not event.actor_role:
        event.actor_role = connection.scalar(
            select(User.role).where(User.id == event.actor_user_id)
        )
    if event.actor_user_id and not event.scope_summary_json:
        grants = connection.execute(
            select(
                DataScopeGrant.scope_kind,
                DataScopeGrant.organization_unit_id,
            ).where(
                DataScopeGrant.user_id == event.actor_user_id,
                DataScopeGrant.can_read.is_(True),
            )
        ).all()
        event.scope_summary_json = {
            "enterprise_wide": any(item.scope_kind == "enterprise" for item in grants),
            "organization_unit_ids": sorted(
                str(item.organization_unit_id)
                for item in grants
                if item.organization_unit_id is not None
            ),
        }
    elif not event.scope_summary_json:
        event.scope_summary_json = {"enterprise_wide": False, "organization_unit_ids": []}
    _append_to_chain(
        connection,
        event,
        settings.audit_hmac_keys(),
        settings.audit_hmac_key_version,
        settings.audit_hmac_legacy_key_version,
    )


def verify_audit_event(event: AuditEvent, key: str | None = None) -> bool:
    if key is not None:
        signing_key = key
    else:
        settings = get_settings()
        version = event.audit_key_version or settings.audit_hmac_legacy_key_version
        signing_key = settings.audit_hmac_keys().get(version)
        if signing_key is None:
            return False
    expected = calculate_integrity_hash(event, signing_key)
    return hmac.compare_digest(expected, event.integrity_hash or "")


def initialize_audit_chains(connection: Connection) -> None:
    """Anchor legacy v1 events before the API starts accepting requests."""

    settings = get_settings()
    keys = settings.audit_hmac_keys()
    signing_key = keys[settings.audit_hmac_key_version]
    enterprise_ids = connection.scalars(
        select(AuditEvent.enterprise_id).where(AuditEvent.chain_scope.is_(None)).distinct()
    ).all()
    for enterprise_id in enterprise_ids:
        _create_chain_head_if_missing(
            connection,
            enterprise_id,
            signing_key,
            settings.audit_hmac_key_version,
        )


def verify_audit_chain(
    executor: Connection | Session,
    enterprise_id: uuid.UUID | None,
    key: str | None = None,
) -> AuditChainVerification:
    settings = get_settings()
    chain_scope = chain_scope_for(enterprise_id)
    legacy_events = executor.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.chain_scope.is_(None),
            (
                AuditEvent.enterprise_id.is_(None)
                if enterprise_id is None
                else AuditEvent.enterprise_id == enterprise_id
            ),
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    events = executor.scalars(
        select(AuditEvent)
        .where(AuditEvent.chain_scope == chain_scope)
        .order_by(AuditEvent.chain_sequence, AuditEvent.id)
    ).all()
    invalid_ids = [
        event.id for event in [*legacy_events, *events] if not verify_audit_event(event, key)
    ]
    errors: list[str] = []
    head = (
        executor.execute(
            select(AuditChainHead.__table__).where(AuditChainHead.chain_scope == chain_scope)
        )
        .mappings()
        .one_or_none()
    )
    if head is None:
        # Enterprise verification always expects an anchor. This also detects an
        # attacker deleting the complete event set together with its chain head.
        errors.append("chain_anchor_missing")
        return AuditChainVerification(
            valid=not invalid_ids and not errors,
            checked_count=len(legacy_events) + len(events),
            invalid_event_ids=invalid_ids,
            errors=errors,
        )

    if key is not None:
        head_key = key
    else:
        head_key_version = str(head["anchor_key_version"] or settings.audit_hmac_legacy_key_version)
        head_key = settings.audit_hmac_keys().get(head_key_version)
        if head_key is None:
            errors.append("chain_anchor_key_unavailable")
            head_key = ""
    expected_anchor = calculate_chain_anchor(head, head_key)
    if not hmac.compare_digest(expected_anchor, str(head["anchor_hash"])):
        errors.append("chain_anchor_hmac_mismatch")
    legacy_rows = [(event.id, event.integrity_hash) for event in legacy_events]
    if int(head["legacy_event_count"]) != len(legacy_rows):
        errors.append("legacy_event_count_mismatch")
    if not hmac.compare_digest(str(head["legacy_root_hash"]), _legacy_root(legacy_rows)):
        errors.append("legacy_root_mismatch")

    expected_sequence = 1
    previous_hash = AUDIT_GENESIS_HASH
    for event in events:
        if event.chain_sequence != expected_sequence:
            errors.append("chain_sequence_gap")
            invalid_ids.append(event.id)
        if event.previous_integrity_hash != previous_hash:
            errors.append("chain_previous_hash_mismatch")
            invalid_ids.append(event.id)
        previous_hash = event.integrity_hash
        expected_sequence += 1
    if int(head["last_sequence"]) != len(events):
        errors.append("chain_head_sequence_mismatch")
    expected_tail = events[-1].integrity_hash if events else AUDIT_GENESIS_HASH
    if not hmac.compare_digest(str(head["last_integrity_hash"]), expected_tail):
        errors.append("chain_head_hash_mismatch")

    unique_invalid_ids = list(dict.fromkeys(invalid_ids))
    unique_errors = list(dict.fromkeys(errors))
    return AuditChainVerification(
        valid=not unique_invalid_ids and not unique_errors,
        checked_count=len(legacy_events) + len(events),
        invalid_event_ids=unique_invalid_ids,
        errors=unique_errors,
    )
