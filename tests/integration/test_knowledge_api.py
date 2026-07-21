from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def test_authenticated_knowledge_crud_and_revision_contract(tmp_path: Path) -> None:
    app = create_app(
        WorkHubSettings(
            data_dir=tmp_path / "workhub",
            static_dir=tmp_path / "frontend-not-built",
            bootstrap_admin_username="admin",
            bootstrap_admin_password=PASSWORD,
            session_secret="test-session-secret-with-enough-entropy",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/knowledge/nodes").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        directory = client.post(
            "/api/v1/knowledge/nodes",
            json={"name": "policies", "node_type": "directory"},
            headers=headers,
        )
        document = client.post(
            "/api/v1/knowledge/nodes",
            json={
                "parent_id": directory.json()["node_id"],
                "name": "leave.md",
                "node_type": "document",
                "content": "# Leave\nInitial policy\n",
            },
            headers=headers,
        )
        read = client.get(f"/api/v1/knowledge/documents/{document.json()['node_id']}")
        saved = client.put(
            f"/api/v1/knowledge/documents/{document.json()['node_id']}",
            json={"content": "# Leave\nUpdated policy\n", "expected_revision": 0},
            headers=headers,
        )
        stale = client.put(
            f"/api/v1/knowledge/documents/{document.json()['node_id']}",
            json={"content": "stale", "expected_revision": 0},
            headers=headers,
        )
        listing = client.get("/api/v1/knowledge/nodes")

    assert directory.status_code == 201
    assert document.status_code == 201
    assert read.json()["content"] == "# Leave\nInitial policy\n"
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert len(listing.json()) == 2
