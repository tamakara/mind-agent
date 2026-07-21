from pathlib import Path

from fastapi.testclient import TestClient
from mock_oa_service.app import create_app as create_mock_oa_app
from mock_oa_service.config import MockOASettings

from workhub.app import create_app as create_workhub_app
from workhub.config import WorkHubSettings


def test_workhub_health_readiness_and_error_contract(tmp_path: Path) -> None:
    app = create_workhub_app(
        WorkHubSettings(
            data_dir=tmp_path / "workhub",
            static_dir=tmp_path / "frontend-not-built",
        )
    )

    with TestClient(app) as client:
        health = client.get("/healthz", headers={"X-Request-ID": "test-request-1"})
        readiness = client.get("/readyz")
        missing = client.get("/api/unknown")

        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert health.headers["X-Request-ID"] == "test-request-1"
        assert readiness.status_code == 200
        assert readiness.json() == {"status": "ready"}
        assert missing.status_code == 404
        assert missing.json()["error"] == {"code": "http_error", "message": "Not Found"}
        assert missing.json()["request_id"] == missing.headers["X-Request-ID"]

    assert app.state.ready is False


def test_workhub_serves_frontend_and_preserves_api_boundary(tmp_path: Path) -> None:
    static_dir = tmp_path / "frontend"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><head><title>WorkHub</title></head><body>admin</body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('workhub')", encoding="utf-8")
    app = create_workhub_app(WorkHubSettings(data_dir=tmp_path / "workhub", static_dir=static_dir))

    with TestClient(app) as client:
        root = client.get("/")
        asset = client.get("/assets/app.js")
        frontend_route = client.get("/employees/active")
        missing_asset = client.get("/assets/missing.js")
        missing_api = client.get("/api/v1/missing")

        assert root.status_code == 200
        assert root.headers["content-type"].startswith("text/html")
        assert "WorkHub" in root.text
        assert asset.status_code == 200
        assert "console.log" in asset.text
        assert frontend_route.status_code == 200
        assert frontend_route.text == root.text
        assert missing_asset.status_code == 404
        assert missing_asset.headers["content-type"].startswith("application/json")
        assert missing_api.status_code == 401
        assert missing_api.headers["content-type"].startswith("application/json")
        assert missing_api.json()["error"]["code"] == "authentication_required"


def test_mock_oa_health_and_readiness(tmp_path: Path) -> None:
    app = create_mock_oa_app(MockOASettings(data_dir=tmp_path / "mock-oa"))

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}

    assert app.state.ready is False
