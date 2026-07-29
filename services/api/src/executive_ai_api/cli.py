from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import (
    AuditEvent,
    DataScopeGrant,
    Enterprise,
    OrganizationUnit,
    User,
    UserCredential,
)
from .security import hash_password, validate_new_password


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


def main() -> None:
    args = parser().parse_args()
    if args.command == "create-admin":
        create_admin(args)
    elif args.command == "create-user":
        create_user(args)


if __name__ == "__main__":
    main()
