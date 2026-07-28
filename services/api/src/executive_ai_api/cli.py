from __future__ import annotations

import argparse
import getpass
import sys
import uuid

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .ingestion import IngestionError, require_isolated_data_source, test_source_connection
from .models import (
    AuditEvent,
    DataScopeGrant,
    DataSource,
    Enterprise,
    Job,
    OrganizationUnit,
    User,
    UserCredential,
)
from .operating_data_reset import (
    LOCAL_RESET_CONFIRMATION,
    OperatingDataResetError,
    inventory_operating_data,
    render_inventory,
    reset_local_demo_operating_data,
)
from .security import hash_password, utc_now, validate_new_password

SOURCE_DATABASE_CONFIG_REFERENCE = "SOURCE_DATABASE_URL"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Executive AI administration CLI")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="Create the first enterprise administrator")
    create.add_argument("--email", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--enterprise-name", required=True)
    create.add_argument("--enterprise-slug", required=True)
    create.add_argument("--password-stdin", action="store_true", required=True)
    create.add_argument(
        "--force-password-change", action=argparse.BooleanOptionalAction, default=True
    )
    create_user = commands.add_parser(
        "create-user", help="Create a user in an initialized enterprise"
    )
    create_user.add_argument("--enterprise-slug", required=True)
    create_user.add_argument("--email", required=True)
    create_user.add_argument("--display-name", required=True)
    create_user.add_argument(
        "--role", choices=("executive", "enterprise_admin", "fde"), default="executive"
    )
    create_user.add_argument("--password-stdin", action="store_true", required=True)
    create_user.add_argument(
        "--force-password-change", action=argparse.BooleanOptionalAction, default=True
    )
    scope = create_user.add_mutually_exclusive_group(required=True)
    scope.add_argument("--enterprise-wide-scope", action="store_true")
    scope.add_argument("--organization-unit-code", action="append", default=[])
    configure_source = commands.add_parser(
        "configure-source",
        help="Validate and register the standard sanitized PostgreSQL source",
    )
    configure_source.add_argument("--enterprise-slug", required=True)
    configure_source.add_argument("--display-name", required=True)
    configure_source.add_argument(
        "--secret-reference-key",
        default=SOURCE_DATABASE_CONFIG_REFERENCE,
        help="Environment variable containing this DataSource's read-only PostgreSQL URL",
    )
    trigger_sync = commands.add_parser(
        "trigger-sync",
        help="Enqueue an immediate sanitized-source synchronization",
    )
    trigger_sync.add_argument("--enterprise-slug", required=True)
    trigger_sync.add_argument("--source-key")
    reset_data = commands.add_parser(
        "reset-local-demo-operating-data-v3",
        help="Clear simulated operating facts only after a verified encrypted backup",
    )
    reset_data.add_argument("--enterprise-slug", required=True)
    reset_data.add_argument("--execute", action="store_true")
    reset_data.add_argument("--confirmation", default="")
    reset_data.add_argument("--backup-reference", default="")
    return root


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        value = getpass.getpass("Temporary password: ")
    if not value:
        raise SystemExit("A non-empty password is required")
    return value


def normalize_login_email(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
        raise SystemExit("A valid login email is required")
    return normalized


def create_admin(args: argparse.Namespace) -> None:
    settings = get_settings()
    password = read_password(args.password_stdin)
    validate_new_password(password, settings)
    email = normalize_login_email(args.email)
    with SessionLocal.begin() as db:
        existing_enterprises = db.scalars(select(Enterprise)).all()
        if existing_enterprises and all(
            item.slug != args.enterprise_slug for item in existing_enterprises
        ):
            raise SystemExit(
                "An enterprise already exists; refusing accidental second initialization"
            )
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
        if enterprise is None:
            enterprise = Enterprise(
                name=args.enterprise_name.strip(), slug=args.enterprise_slug.strip()
            )
            db.add(enterprise)
            db.flush()
        if db.scalar(
            select(User.id).where(
                User.enterprise_id == enterprise.id,
                User.role == "enterprise_admin",
            )
        ):
            raise SystemExit(
                "An enterprise administrator already exists; use the admin API for more users"
            )
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("A user with this login email already exists")
        user = User(
            enterprise_id=enterprise.id,
            email=email,
            display_name=args.display_name.strip(),
            preferred_name=args.display_name.strip(),
            role="enterprise_admin",
            password_change_required=args.force_password_change,
        )
        db.add(user)
        db.flush()
        db.add(UserCredential(user_id=user.id, password_hash=hash_password(password)))
        db.add(DataScopeGrant(user_id=user.id, scope_kind="enterprise", can_read=True))
        db.flush()
        db.add(
            AuditEvent(
                enterprise_id=enterprise.id,
                actor_user_id=user.id,
                action="cli.enterprise_initialized",
                target_type="user",
                target_id=str(user.id),
                outcome="success",
                metadata_json={"role": "enterprise_admin"},
            )
        )
        user_id = user.id
    print(f"Created enterprise administrator {user_id}")


def create_user(args: argparse.Namespace) -> None:
    settings = get_settings()
    password = read_password(args.password_stdin)
    validate_new_password(password, settings)
    email = normalize_login_email(args.email)
    with SessionLocal.begin() as db:
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
        if enterprise is None:
            raise SystemExit("Enterprise does not exist; run create-admin first")
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("A user with this login email already exists")
        units: list[OrganizationUnit] = []
        if args.organization_unit_code:
            units = db.scalars(
                select(OrganizationUnit).where(
                    OrganizationUnit.enterprise_id == enterprise.id,
                    OrganizationUnit.code.in_(set(args.organization_unit_code)),
                    OrganizationUnit.is_active.is_(True),
                )
            ).all()
            if {item.code for item in units} != set(args.organization_unit_code):
                raise SystemExit("One or more organization unit codes do not exist")
        user = User(
            enterprise_id=enterprise.id,
            email=email,
            display_name=args.display_name.strip(),
            preferred_name=args.display_name.strip(),
            role=args.role,
            password_change_required=args.force_password_change,
        )
        db.add(user)
        db.flush()
        db.add(UserCredential(user_id=user.id, password_hash=hash_password(password)))
        if args.enterprise_wide_scope:
            db.add(DataScopeGrant(user_id=user.id, scope_kind="enterprise", can_read=True))
        else:
            for unit in units:
                db.add(
                    DataScopeGrant(
                        user_id=user.id,
                        scope_kind="organization_unit",
                        organization_unit_id=unit.id,
                        can_read=True,
                    )
                )
        db.add(
            AuditEvent(
                enterprise_id=enterprise.id,
                action="cli.user_created",
                target_type="user",
                target_id=str(user.id),
                outcome="success",
                metadata_json={
                    "role": user.role,
                    "enterprise_wide": args.enterprise_wide_scope,
                    "organization_unit_codes": [item.code for item in units],
                },
            )
        )
        user_id = user.id
    print(f"Created user {user_id}")


def configure_source(args: argparse.Namespace) -> None:
    settings = get_settings()
    live_feishu_configured = settings.app_env == "local-demo" and all(
        (
            settings.feishu_opportunity_app_token,
            settings.feishu_opportunity_table_id,
            settings.feishu_delivery_app_token,
            settings.feishu_delivery_table_id,
            settings.feishu_collection_app_token,
            settings.feishu_collection_table_id,
        )
    )
    source_type = (
        "feishu_three_table"
        if live_feishu_configured
        else "simulated_generator"
        if settings.app_env == "local-demo"
        else "customer_sanitized_database"
    )
    source_key = (
        "demo-sanitized-source" if settings.app_env == "local-demo" else "customer-sanitized-source"
    )
    with SessionLocal.begin() as db:
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
        if enterprise is None:
            raise SystemExit("Enterprise does not exist; run create-admin first")
        source = db.scalar(
            select(DataSource).where(
                DataSource.enterprise_id == enterprise.id,
                DataSource.key == source_key,
            )
        )
        if source is None:
            source = DataSource(
                enterprise_id=enterprise.id,
                key=source_key,
                display_name=args.display_name.strip(),
                source_type=source_type,
                schema_version=settings.source_schema_version,
                secret_reference_key=args.secret_reference_key.strip(),
            )
            db.add(source)
        source.display_name = args.display_name.strip()
        source.source_type = source_type
        source.schema_version = settings.source_schema_version
        source.is_enabled = True
        source.configuration_json = {
            "schema": settings.source_schema,
            "connection_mode": settings.source_connection_mode,
            **(
                {
                    "folder_token": settings.feishu_source_folder_token,
                    "tables": {
                        "opportunity": {
                            "app_token": settings.feishu_opportunity_app_token,
                            "table_id": settings.feishu_opportunity_table_id,
                        },
                        "delivery": {
                            "app_token": settings.feishu_delivery_app_token,
                            "table_id": settings.feishu_delivery_table_id,
                        },
                        "collection": {
                            "app_token": settings.feishu_collection_app_token,
                            "table_id": settings.feishu_collection_table_id,
                        },
                    },
                    "activation_policy": "all_three_atomic",
                    "experience_weights_percent": {"high": 20, "medium": 10, "low": 5},
                    "source_contract": "3.0",
                }
                if live_feishu_configured
                else {}
            ),
        }
        source.secret_reference_key = args.secret_reference_key.strip()
        db.flush()
        require_isolated_data_source(db, source)
        inspection = test_source_connection(source, db=db, settings=settings, allow_empty=True)
        source.configuration_json = {
            **source.configuration_json,
            "database_version": inspection["database_version"],
            "read_only": inspection["read_only"],
            "tls_active": inspection["tls_active"],
        }
        source.last_tested_at = utc_now()
        source.last_test_status = "success"
        source.last_test_error = None
        db.flush()
        db.add(
            AuditEvent(
                enterprise_id=enterprise.id,
                action="cli.data_source_configured",
                target_type="data_source",
                target_id=str(source.id),
                outcome="success",
                metadata_json={
                    "source_type": source_type,
                    "schema_version": settings.source_schema_version,
                },
            )
        )
        source_id = source.id
    print(f"Configured sanitized source {source_id}")


def trigger_sync(args: argparse.Namespace) -> None:
    settings = get_settings()
    with SessionLocal.begin() as db:
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
        if enterprise is None:
            raise SystemExit("Enterprise does not exist; run create-admin first")
        source_statement = select(DataSource).where(
            DataSource.enterprise_id == enterprise.id,
            DataSource.is_enabled.is_(True),
        )
        if args.source_key:
            source_statement = source_statement.where(DataSource.key == args.source_key)
        sources = db.scalars(source_statement.order_by(DataSource.created_at)).all()
        if not sources:
            raise SystemExit("No enabled sanitized data source exists; run configure-source first")
        if len(sources) > 1 and not args.source_key:
            raise SystemExit("Multiple enabled data sources exist; specify --source-key")
        source = sources[0]
        try:
            require_isolated_data_source(db, source)
        except IngestionError as exc:
            raise SystemExit(str(exc)) from exc
        organization_ids = db.scalars(
            select(OrganizationUnit.id).where(
                OrganizationUnit.enterprise_id == enterprise.id,
                OrganizationUnit.is_active.is_(True),
                OrganizationUnit.enabled_for_analysis.is_(True),
                OrganizationUnit.data_connected.is_(True),
            )
        ).all()
        job = Job(
            enterprise_id=enterprise.id,
            created_by_user_id=None,
            job_type="data.sync",
            status="queued",
            scheduled_at=utc_now(),
            max_attempts=settings.worker_job_max_attempts,
            payload_json={
                "data_source_id": str(source.id),
                "scheduled_task_id": None,
                "trigger_type": "fde_cli",
                "request_id": uuid.uuid4().hex,
            },
            scope_snapshot_json={
                "system": True,
                "enterprise_id": str(enterprise.id),
                "organization_unit_ids": [str(value) for value in organization_ids],
            },
        )
        db.add(job)
        db.flush()
        db.add(
            AuditEvent(
                enterprise_id=enterprise.id,
                action="cli.data_sync_requested",
                target_type="job",
                target_id=str(job.id),
                outcome="success",
                metadata_json={
                    "data_source_id": str(source.id),
                    "source_key": source.key,
                    "trigger_type": "fde_cli",
                },
            )
        )
        job_id = job.id
    print(f"Enqueued sanitized source sync {job_id}")


def reset_operating_data_v3(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.app_env != "local-demo":
        raise SystemExit("该命令只允许在 APP_ENV=local-demo 执行")
    with SessionLocal.begin() as db:
        enterprise = db.scalar(select(Enterprise).where(Enterprise.slug == args.enterprise_slug))
        if enterprise is None:
            raise SystemExit("Enterprise does not exist")
        if not args.execute:
            print(render_inventory(inventory_operating_data(db, enterprise.id)))
            print(f"Dry run only. Execute with --confirmation {LOCAL_RESET_CONFIRMATION!r}")
            return
        try:
            removed = reset_local_demo_operating_data(
                db,
                enterprise_id=enterprise.id,
                confirmation=args.confirmation,
                backup_reference=args.backup_reference,
            )
        except OperatingDataResetError as exc:
            raise SystemExit(str(exc)) from exc
    print(render_inventory(removed))
    print("本机 Production 模拟经营数据已清理，等待 V3 三表批次原子激活")


def main() -> None:
    args = parser().parse_args()
    if args.command == "create-admin":
        create_admin(args)
    elif args.command == "create-user":
        create_user(args)
    elif args.command == "configure-source":
        configure_source(args)
    elif args.command == "trigger-sync":
        trigger_sync(args)
    elif args.command == "reset-local-demo-operating-data-v3":
        reset_operating_data_v3(args)


if __name__ == "__main__":
    main()
