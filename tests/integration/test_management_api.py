import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.audit import AuditEvent
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def test_overview_and_filtered_audit_are_authenticated_and_redacted(tmp_path: Path) -> None:
    app = create_app(
        WorkHubSettings(
            data_dir=tmp_path / "workhub",
            static_dir=tmp_path / "frontend-not-built",
            admin_username="admin",
            admin_password=PASSWORD,
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/overview").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        employee = client.post(
            "/api/v1/employees",
            json={
                "employee_no": "E10001",
                "display_name": "Employee",
                "department": "Engineering",
                "timezone": "Asia/Shanghai",
            },
            headers=headers,
        ).json()
        asyncio.run(
            app.state.audit.write(
                AuditEvent(
                    event_type="mcp.snapshot_test",
                    actor_type="employee",
                    actor_id=employee["employee_id"],
                    subject_type="pending_action",
                    subject_id="action-100",
                    summary={
                        "tool_name": "submit_leave_request",
                        "business_id": "LR-100",
                        "arguments": {"reason": "private", "days": 1},
                        "action_token": "never-store-this",
                        "authorization": "Bearer never-store-this",
                    },
                )
            )
        )
        overview = client.get("/api/v1/overview")
        filtered = client.get(
            "/api/v1/audit-events",
            params={
                "employee_id": employee["employee_id"],
                "tool": "submit_leave_request",
                "action_id": "action-100",
                "business_id": "LR-100",
            },
        )

    assert overview.status_code == 200
    assert overview.json()["employees_total"] == 1
    assert overview.json()["employees_active"] == 1
    assert overview.json()["mock_oa_state"] == "disabled"
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    body = filtered.text
    assert "submit_leave_request" in body
    assert "LR-100" in body
    assert "private" not in body
    assert "never-store-this" not in body
    summary = filtered.json()["items"][0]["summary"]
    assert summary["action_token"] == "[REDACTED]"
    assert summary["authorization"] == "[REDACTED]"
    assert set(summary["arguments"]) == {"keys", "sha256"}
