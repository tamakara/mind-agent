import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def _app(tmp_path: Path):
    return create_app(
        WorkHubSettings(
            data_dir=tmp_path / "workhub",
            static_dir=tmp_path / "frontend-not-built",
            admin_username="admin",
            admin_password=PASSWORD,
        )
    )


def _headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    return {"Origin": ORIGIN}


def test_runtime_settings_are_revisioned_and_applied(tmp_path: Path) -> None:
    app = _app(tmp_path)
    applied: list[object] = []

    with TestClient(app) as client:
        headers = _headers(client)

        async def apply(value: object) -> None:
            applied.append(value)

        app.state.apply_runtime_settings = apply
        initial = client.get("/api/v1/settings/runtime")
        payload = {
            **initial.json(),
            "agent_timeout_seconds": 90,
            "expected_revision": initial.json()["revision"],
        }
        saved = client.put("/api/v1/settings/runtime", json=payload, headers=headers)
        updated = client.put(
            "/api/v1/settings/runtime",
            json={**payload, "agent_timeout_seconds": 91, "expected_revision": 0},
            headers=headers,
        )
        stale = client.put("/api/v1/settings/runtime", json=payload, headers=headers)

    assert initial.status_code == 200
    assert saved.status_code == 200
    assert saved.json()["agent_timeout_seconds"] == 90
    assert saved.json()["revision"] == 0
    assert updated.status_code == 200
    assert len(applied) == 2
    assert stale.status_code == 409


def test_feishu_settings_are_secret_safe_and_deletable(tmp_path: Path) -> None:
    app = _app(tmp_path)

    with TestClient(app) as client:
        headers = _headers(client)

        async def apply() -> None:
            return None

        app.state.apply_feishu_settings = apply
        created = client.put(
            "/api/v1/settings/feishu",
            json={"app_id": "cli_test", "app_secret": "top-secret"},
            headers=headers,
        )
        listing = client.get("/api/v1/settings/feishu")
        deleted = client.request(
            "DELETE",
            "/api/v1/settings/feishu",
            json={"expected_revision": created.json()["revision"]},
            headers=headers,
        )
        after_delete = client.get("/api/v1/settings/feishu")

    assert created.status_code == 200
    assert created.json()["app_secret_configured"] is True
    assert "top-secret" not in created.text
    assert "top-secret" not in listing.text
    assert deleted.status_code == 204
    assert after_delete.json() is None


def test_provider_and_feishu_environment_values_are_ignored(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKHUB_CHAT_BASE_URL", "https://ignored.example/v1")
    monkeypatch.setenv("WORKHUB_CHAT_API_KEY", "ignored-secret")
    monkeypatch.setenv("WORKHUB_CHAT_MODEL", "ignored-model")
    monkeypatch.setenv("WORKHUB_FEISHU_APP_ID", "ignored-app")
    monkeypatch.setenv("WORKHUB_FEISHU_APP_SECRET", "ignored-secret")
    app = _app(tmp_path)

    with TestClient(app) as client:
        _headers(client)
        providers = client.get("/api/v1/providers")
        feishu = client.get("/api/v1/settings/feishu")

    assert providers.json() == []
    assert feishu.json() is None


def test_jwt_signing_key_is_generated_and_persisted(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        _headers(client)
    with sqlite3.connect(tmp_path / "workhub" / "app.db") as connection:
        first_secret = connection.execute(
            "SELECT secret FROM instance_secrets WHERE name = 'admin_jwt_signing_key'"
        ).fetchone()

    second_app = _app(tmp_path)
    with TestClient(second_app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
    with sqlite3.connect(tmp_path / "workhub" / "app.db") as connection:
        second_secret = connection.execute(
            "SELECT secret FROM instance_secrets WHERE name = 'admin_jwt_signing_key'"
        ).fetchone()

    assert first_secret is not None
    assert first_secret == second_secret
    assert len(first_secret[0]) >= 48
