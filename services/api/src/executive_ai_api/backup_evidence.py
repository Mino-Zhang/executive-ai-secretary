from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


@dataclass(frozen=True)
class VerifiedBackupEvidence:
    directory: Path
    environment: str
    created_at: datetime
    alembic_revision: str

    @property
    def reference(self) -> str:
        return f"{self.environment}:{self.directory.name}:{self.created_at.isoformat()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise RuntimeError("backup manifest has an invalid or duplicate field")
        values[key] = value
    return values


def verify_backup_evidence(
    backup_directory: Path,
    signing_public_key: Path,
    *,
    expected_environment: str,
    max_age: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> VerifiedBackupEvidence:
    directory = backup_directory.expanduser().resolve()
    if not directory.is_dir():
        raise RuntimeError("backup directory does not exist")
    manifest_path = directory / "manifest.env"
    signature_path = directory / "manifest.sig"
    if not manifest_path.is_file() or not signature_path.is_file():
        raise RuntimeError("backup manifest or signature is missing")
    public_key_path = signing_public_key.expanduser().resolve()
    if not public_key_path.is_file():
        raise RuntimeError("backup signing public key is missing")
    public_key = load_pem_public_key(public_key_path.read_bytes())
    if not isinstance(public_key, Ed25519PublicKey):
        raise RuntimeError("backup signing public key must be Ed25519")
    try:
        public_key.verify(signature_path.read_bytes(), manifest_path.read_bytes())
    except InvalidSignature as exc:
        raise RuntimeError("backup manifest signature verification failed") from exc

    manifest = _parse_manifest(manifest_path)
    required = {
        "format_version",
        "environment",
        "created_at_utc",
        "alembic_revision",
        "consistency",
        "database_file",
        "database_sha256",
        "files_file",
        "files_sha256",
    }
    if not required.issubset(manifest):
        raise RuntimeError("backup manifest is incomplete")
    if manifest["format_version"] != "1" or manifest["consistency"] != "application-quiesced":
        raise RuntimeError("backup is not a supported quiesced snapshot")
    if manifest["environment"] != expected_environment:
        raise RuntimeError("backup environment does not match the rotation environment")
    if not manifest["alembic_revision"] or manifest["alembic_revision"] == "unknown":
        raise RuntimeError("backup does not record an Alembic revision")
    try:
        created_at = datetime.strptime(manifest["created_at_utc"], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as exc:
        raise RuntimeError("backup timestamp is invalid") from exc
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if created_at > current + timedelta(minutes=5) or current - created_at > max_age:
        raise RuntimeError("backup is too old or has a future timestamp")

    for file_field, digest_field in (
        ("database_file", "database_sha256"),
        ("files_file", "files_sha256"),
    ):
        relative_name = Path(manifest[file_field])
        if relative_name.name != manifest[file_field]:
            raise RuntimeError("backup manifest contains an unsafe artifact path")
        artifact = directory / relative_name
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise RuntimeError("backup artifact is missing or empty")
        if not hmac.compare_digest(_sha256(artifact), manifest[digest_field]):
            raise RuntimeError("backup artifact checksum verification failed")

    return VerifiedBackupEvidence(
        directory=directory,
        environment=manifest["environment"],
        created_at=created_at,
        alembic_revision=manifest["alembic_revision"],
    )
