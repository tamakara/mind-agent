from pathlib import Path

import pytest
from mock_oa_service.config import MockOASettings

from workhub.config import WorkHubSettings


def test_settings_use_independent_environment_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workhub_dir = tmp_path / "workhub"
    mock_oa_dir = tmp_path / "mock-oa"
    monkeypatch.setenv("WORKHUB_DATA_DIR", str(workhub_dir))
    monkeypatch.setenv("MOCK_OA_DATA_DIR", str(mock_oa_dir))
    monkeypatch.chdir(tmp_path)

    workhub_settings = WorkHubSettings()
    mock_oa_settings = MockOASettings()

    assert workhub_settings.data_dir == workhub_dir
    assert mock_oa_settings.data_dir == mock_oa_dir
