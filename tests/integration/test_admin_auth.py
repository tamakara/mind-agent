import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from workhub.app import create_app
from workhub.config import WorkHubSettings

ORIGIN = "http://testserver"
PASSWORD = "correct-horse-battery-staple"


def _settings(tmp_path: Path, **overrides: object) -> WorkHubSettings:
    values: dict[str, object] = {
        "data_dir": tmp_path / "workhub",
        "static_dir": tmp_path / "frontend-not-built",
        "admin_username": "admin",
        "admin_password": PASSWORD,
    }
    values.update(overrides)
    return WorkHubSettings(**values)


def test_admin_bootstrap_login_session_csrf_and_logout(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        status = client.get("/api/v1/auth/bootstrap-status")
        invalid_origin = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": "https://attacker.example"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        csrf_token = login.json()["csrf_token"]
        session = client.get("/api/v1/auth/session")
        missing_origin = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
        missing_csrf = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
        )
        expired_session = client.get("/api/v1/auth/session")

    assert status.json() == {"initialized": True}
    assert invalid_origin.status_code == 403
    assert invalid_origin.json()["error"]["code"] == "origin_forbidden"
    assert login.status_code == 200
    assert login.json()["username"] == "admin"
    assert "workhub_admin_session" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert session.status_code == 200
    assert missing_origin.json()["error"]["code"] == "origin_forbidden"
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"
    assert logout.status_code == 204
    assert expired_session.status_code == 401


def test_login_rate_limit_and_audit_do_not_store_password(tmp_path: Path) -> None:
    settings = _settings(tmp_path, admin_login_max_attempts=2)
    app = create_app(settings)

    with TestClient(app) as client:
        responses = [
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "incorrect-password"},
                headers={"Origin": ORIGIN},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [401, 401, 429]
    assert responses[-1].json()["error"]["code"] == "login_rate_limited"

    with sqlite3.connect(settings.data_dir / "app.db") as connection:
        summaries = [row[0] for row in connection.execute("SELECT summary_json FROM audit_events")]
        attempts = connection.execute("SELECT COUNT(*) FROM admin_login_attempts").fetchone()[0]
    assert attempts == 2
    assert summaries
    assert "incorrect-password" not in json.dumps(summaries)
    assert PASSWORD not in json.dumps(summaries)


def test_expired_admin_session_is_rejected(tmp_path: Path) -> None:
    current = [datetime(2026, 7, 21, 8, 0, tzinfo=UTC)]
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        service = app.state.auth_service
        service.clock = lambda: current[0]
        authenticated = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert authenticated.status_code == 200
        current[0] += timedelta(seconds=settings.admin_session_ttl_seconds + 1)
        assert client.get("/api/v1/auth/session").status_code == 401
