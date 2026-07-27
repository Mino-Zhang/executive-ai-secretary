from __future__ import annotations

from sqlalchemy import select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import AuditEvent, McpToolConfig

from .conftest import login, login_and_change_password


def test_mcp_registry_is_visible_but_only_operations_roles_can_configure_it(
    client, seeded
) -> None:
    login_and_change_password(client)
    forbidden = client.get("/api/v1/admin/mcp-tools")
    assert forbidden.status_code == 403

    with client.__class__(client.app) as admin_client:
        admin = login(admin_client, "admin@example.com")
        catalog = admin_client.get("/api/v1/admin/mcp-tools")
        assert catalog.status_code == 200, catalog.text
        body = catalog.json()
        assert len(body["tools"]) == 11
        assert body["enabled_count"] == 11
        assert body["planner_count"] == 11
        scope_tool = next(
            item for item in body["tools"] if item["tool_name"] == "list_query_scopes"
        )
        assert scope_tool["readiness"] == "ready"
        assert scope_tool["parameters"] == {}

        updated = admin_client.patch(
            "/api/v1/admin/mcp-tools/get_collection_aging",
            headers={"X-CSRF-Token": admin["csrf_token"]},
            json={
                "display_name": "回款账龄核对",
                "is_enabled": False,
                "planner_enabled": True,
                "timeout_seconds": 18,
                "max_rows": 30,
                "operator_note": "财务域核验后再启用",
            },
        )
        assert updated.status_code == 200, updated.text
        changed = updated.json()
        assert changed["display_name"] == "回款账龄核对"
        assert changed["is_enabled"] is False
        assert changed["planner_enabled"] is False
        assert changed["max_rows"] == 30
        assert changed["readiness"] == "disabled"

        validated = admin_client.post(
            "/api/v1/admin/mcp-tools/list_query_scopes/validate",
            headers={"X-CSRF-Token": admin["csrf_token"]},
        )
        assert validated.status_code == 200
        assert validated.json()["ready"] is True

        unknown = admin_client.patch(
            "/api/v1/admin/mcp-tools/arbitrary_sql",
            headers={"X-CSRF-Token": admin["csrf_token"]},
            json={"is_enabled": True},
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "mcp_tool_not_found"

    with SessionLocal() as db:
        config = db.scalar(
            select(McpToolConfig).where(
                McpToolConfig.tool_name == "get_collection_aging"
            )
        )
        assert config is not None
        assert config.updated_by_user_id == seeded["users"]["admin@example.com"]
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.action.in_(
                        ["admin.mcp_tool_updated", "admin.mcp_tool_validated"]
                    )
                )
            ).all()
        )
        assert actions == {"admin.mcp_tool_updated", "admin.mcp_tool_validated"}
