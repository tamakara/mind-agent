from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def test_authenticated_employee_crud_revision_and_constraints(tmp_path: Path) -> None:
    app = create_app(
        WorkHubSettings(
            data_dir=tmp_path / "workhub",
            static_dir=tmp_path / "frontend-not-built",
            admin_username="admin",
            admin_password=PASSWORD,
        )
    )

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        write_headers = {"Origin": ORIGIN}
        manager = client.post(
            "/api/v1/employees",
            json={
                "employee_no": "E10001",
                "display_name": "Manager",
                "department": "Engineering",
                "timezone": "Asia/Shanghai",
            },
            headers=write_headers,
        )
        employee = client.post(
            "/api/v1/employees",
            json={
                "employee_no": "E10002",
                "display_name": "Employee",
                "department": "Engineering",
                "manager_employee_id": manager.json()["employee_id"],
                "timezone": "Asia/Shanghai",
            },
            headers=write_headers,
        )
        duplicate = client.post(
            "/api/v1/employees",
            json={
                "employee_no": "E10002",
                "display_name": "Duplicate",
                "department": "Engineering",
            },
            headers=write_headers,
        )
        disabled = client.patch(
            f"/api/v1/employees/{employee.json()['employee_id']}",
            json={"expected_revision": 0, "status": "disabled"},
            headers=write_headers,
        )
        stale = client.patch(
            f"/api/v1/employees/{employee.json()['employee_id']}",
            json={"expected_revision": 0, "display_name": "Stale"},
            headers=write_headers,
        )
        listing = client.get("/api/v1/employees?limit=1&offset=0")

    assert manager.status_code == 201
    assert employee.status_code == 201
    assert employee.json()["manager_employee_id"] == manager.json()["employee_id"]
    assert duplicate.status_code == 409
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["revision"] == 1
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 1
