from pathlib import Path

import pytest
from mock_oa_service.config import MockOASettings

from workhub.config import WorkHubSettings


def test_settings_use_independent_environment_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workhub_dir = tmp_path / "workhub"
    static_dir = tmp_path / "frontend"
    mock_oa_dir = tmp_path / "mock-oa"
    monkeypatch.setenv("WORKHUB_DATA_DIR", str(workhub_dir))
    monkeypatch.setenv("WORKHUB_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("MOCK_OA_DATA_DIR", str(mock_oa_dir))
    monkeypatch.setenv("WORKHUB_MOCK_OA_MCP_URL", "http://mock-oa:8001/mcp")
    monkeypatch.setenv("WORKHUB_MOCK_OA_SHARED_SECRET", "workhub-secret")
    monkeypatch.setenv("MOCK_OA_WORKHUB_SHARED_SECRET", "workhub-secret")
    monkeypatch.setenv("MOCK_OA_ADMIN_TOKEN", "admin-secret")
    monkeypatch.chdir(tmp_path)

    workhub_settings = WorkHubSettings()
    mock_oa_settings = MockOASettings()

    assert workhub_settings.data_dir == workhub_dir
    assert workhub_settings.static_dir == static_dir
    assert mock_oa_settings.data_dir == mock_oa_dir
    assert workhub_settings.mock_oa_mcp_url == "http://mock-oa:8001/mcp"
    assert workhub_settings.mock_oa_shared_secret is not None
    assert workhub_settings.mock_oa_shared_secret.get_secret_value() == "workhub-secret"
    assert mock_oa_settings.workhub_shared_secret is not None
    assert mock_oa_settings.workhub_shared_secret.get_secret_value() == "workhub-secret"
    assert mock_oa_settings.admin_token is not None
    assert mock_oa_settings.admin_token.get_secret_value() == "admin-secret"
