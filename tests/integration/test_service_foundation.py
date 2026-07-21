from pathlib import Path

from fastapi.testclient import TestClient
from mock_oa_service.app import create_app as create_mock_oa_app
from mock_oa_service.config import MockOASettings

from workhub.app import create_app as create_workhub_app
from workhub.config import WorkHubSettings


def test_workhub_health_readiness_and_error_contract(tmp_path: Path) -> None:
    app = create_workhub_app(WorkHubSettings(data_dir=tmp_path / "workhub"))

    with TestClient(app) as client:
        health = client.get("/healthz", headers={"X-Request-ID": "test-request-1"})
        readiness = client.get("/readyz")
        missing = client.get("/missing")

        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert health.headers["X-Request-ID"] == "test-request-1"
        assert readiness.status_code == 200
        assert readiness.json() == {"status": "ready"}
        assert missing.status_code == 404
        assert missing.json()["error"] == {"code": "http_error", "message": "Not Found"}
        assert missing.json()["request_id"] == missing.headers["X-Request-ID"]

    assert app.state.ready is False


def test_mock_oa_health_and_readiness(tmp_path: Path) -> None:
    app = create_mock_oa_app(MockOASettings(data_dir=tmp_path / "mock-oa"))

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}

    assert app.state.ready is False
