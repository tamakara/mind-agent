from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def test_provider_configuration_is_revisioned_and_secret_is_never_returned(
    tmp_path: Path,
) -> None:
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
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        created = client.put(
            "/api/v1/providers/chat",
            json={
                "base_url": "https://provider.example/v1",
                "model": "chat-model",
                "api_key": "top-secret-key",
            },
            headers=headers,
        )
        updated = client.put(
            "/api/v1/providers/chat",
            json={
                "base_url": "https://provider.example/v1",
                "model": "chat-model-2",
                "expected_revision": 0,
            },
            headers=headers,
        )
        stale = client.put(
            "/api/v1/providers/chat",
            json={
                "base_url": "https://provider.example/v1",
                "model": "stale-model",
                "expected_revision": 0,
            },
            headers=headers,
        )
        listing = client.get("/api/v1/providers")
        deleted = client.request(
            "DELETE",
            "/api/v1/providers/chat",
            json={"expected_revision": 1},
            headers=headers,
        )
        after_delete = client.get("/api/v1/providers")

    assert created.status_code == 200
    assert created.json()["api_key_configured"] is True
    assert "api_key" not in created.json()
    assert "top-secret-key" not in created.text
    assert updated.json()["revision"] == 1
    assert updated.json()["model"] == "chat-model-2"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert "top-secret-key" not in listing.text
    assert deleted.status_code == 204
    assert after_delete.json() == []
