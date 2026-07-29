from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from .backup_evidence import verify_backup_evidence
from .config import get_settings
from .database import SessionLocal
from .file_key_rotation import rotate_file_keys, verify_file_key_version
from .storage import LocalEncryptedStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotate encrypted private-file keys safely")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--backup-public-key", type=Path)
    parser.add_argument("--max-backup-age-hours", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    if args.to_version != settings.file_encryption_key_version:
        raise SystemExit("--to-version must equal FILE_ENCRYPTION_KEY_VERSION")
    keys = settings.file_encryption_keys()
    for version in (args.from_version, args.to_version):
        if version not in keys:
            raise SystemExit(f"key version {version!r} is missing from the configured key ring")
    storage = LocalEncryptedStorage(
        settings.file_storage_root,
        current_key_version=settings.file_encryption_key_version,
        key_ring=keys,
    )
    if args.verify_only:
        with SessionLocal() as db:
            verified = verify_file_key_version(
                db,
                storage,
                key_version=args.to_version,
            )
        print(json.dumps({"status": "verified", "files": verified}, ensure_ascii=False))
        return

    backup_reference = "dry-run"
    if not args.dry_run:
        confirmation = f"ROTATE FILE KEYS {args.from_version} TO {args.to_version}"
        if args.confirm != confirmation:
            raise SystemExit(f"refusing rotation; pass --confirm {confirmation!r}")
        if args.backup_dir is None or args.backup_public_key is None:
            raise SystemExit("--backup-dir and --backup-public-key are required")
        if args.max_backup_age_hours < 1:
            raise SystemExit("--max-backup-age-hours must be positive")
        evidence = verify_backup_evidence(
            args.backup_dir,
            args.backup_public_key,
            expected_environment=settings.app_env,
            max_age=timedelta(hours=args.max_backup_age_hours),
        )
        backup_reference = evidence.reference

    with SessionLocal() as db:
        summary = rotate_file_keys(
            db,
            storage,
            source_key_version=args.from_version,
            target_key_version=args.to_version,
            backup_reference=backup_reference,
            batch_size=args.batch_size,
            max_files=args.max_files,
            dry_run=args.dry_run,
        )
        if not args.dry_run and summary.remaining == 0:
            verified = verify_file_key_version(
                db,
                storage,
                key_version=args.to_version,
            )
        else:
            verified = 0
    print(
        json.dumps(
            {
                "status": "dry-run" if args.dry_run else "completed",
                "inspected": summary.inspected,
                "rewritten": summary.rewritten,
                "reconciled": summary.reconciled,
                "remaining": summary.remaining,
                "verified_target_files": verified,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
