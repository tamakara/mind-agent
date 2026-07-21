import os
import stat
from pathlib import Path

from mock_oa_service.storage import initialize_mock_oa_data_layout

from workhub.storage import initialize_workhub_data_layout


def test_data_layouts_are_independent_and_idempotent(tmp_path: Path) -> None:
    workhub = initialize_workhub_data_layout(tmp_path / "workhub")
    mock_oa = initialize_mock_oa_data_layout(tmp_path / "mock-oa")

    assert workhub.database.is_file()
    assert workhub.knowledge_originals.is_dir()
    assert workhub.knowledge_staging.is_dir()
    assert workhub.knowledge_index.is_dir()
    assert workhub.logs.is_dir()
    assert mock_oa.database.is_file()
    assert workhub.root != mock_oa.root

    workhub.database.write_bytes(b"existing")
    initialize_workhub_data_layout(workhub.root)
    assert workhub.database.read_bytes() == b"existing"

    if os.name != "nt":
        assert stat.S_IMODE(workhub.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(workhub.database.stat().st_mode) == 0o600
        assert stat.S_IMODE(mock_oa.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(mock_oa.database.stat().st_mode) == 0o600
