from __future__ import annotations

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import Enterprise, OrganizationUnit, User

from .conftest import login


def admin_headers(client) -> dict[str, str]:
    auth = login(client, "admin@example.com")
    return {"X-CSRF-Token": auth["csrf_token"]}


def test_admin_cannot_demote_self_or_remove_last_active_admin(client, seeded) -> None:
    headers = admin_headers(client)
    admin_id = seeded["users"]["admin@example.com"]

    demote = client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers=headers,
        json={"role": "executive"},
    )
    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "cannot_demote_self"

    disable = client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers=headers,
        json={"is_active": False},
    )
    assert disable.status_code == 409
    assert disable.json()["error"]["code"] == "cannot_disable_self"


def test_organization_parent_must_stay_in_enterprise_and_remain_acyclic(client, seeded) -> None:
    headers = admin_headers(client)
    first = client.post(
        "/api/v1/admin/organization-units",
        headers=headers,
        json={"name": "一级组织", "code": "level-one"},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/admin/organization-units",
        headers=headers,
        json={
            "name": "二级组织",
            "code": "level-two",
            "parent_id": first.json()["id"],
        },
    )
    assert second.status_code == 201, second.text

    cycle = client.patch(
        f"/api/v1/admin/organization-units/{first.json()['id']}",
        headers=headers,
        json={"parent_id": second.json()["id"]},
    )
    assert cycle.status_code == 422
    assert cycle.json()["error"]["code"] == "organization_cycle"

    with SessionLocal.begin() as db:
        other_enterprise = Enterprise(name="其他企业", slug="other-enterprise")
        db.add(other_enterprise)
        db.flush()
        foreign_unit = OrganizationUnit(
            enterprise_id=other_enterprise.id,
            name="外部组织",
            code="foreign-unit",
        )
        db.add(foreign_unit)
        db.flush()
        foreign_id = foreign_unit.id

    cross_enterprise = client.patch(
        f"/api/v1/admin/organization-units/{first.json()['id']}",
        headers=headers,
        json={"parent_id": str(foreign_id)},
    )
    assert cross_enterprise.status_code == 422
    assert cross_enterprise.json()["error"]["code"] == "invalid_parent"


def test_login_email_is_globally_unique_and_must_be_valid(client, seeded) -> None:
    headers = admin_headers(client)
    invalid = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "email": "not-an-email",
            "display_name": "无效账号",
            "temporary_password": "Valid-Temporary-Password1!",
            "role": "executive",
            "enterprise_wide_scope": True,
        },
    )
    assert invalid.status_code == 422

    with SessionLocal.begin() as db:
        foreign_enterprise = Enterprise(name="外部租户", slug="foreign-tenant")
        db.add(foreign_enterprise)
        db.flush()
        db.add(
            User(
                enterprise_id=foreign_enterprise.id,
                email="global-duplicate@example.com",
                display_name="外部账号",
                role="executive",
            )
        )

    duplicate = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "email": "GLOBAL-DUPLICATE@example.com",
            "display_name": "重复登录名",
            "temporary_password": "Valid-Temporary-Password1!",
            "role": "executive",
            "enterprise_wide_scope": True,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_exists"
