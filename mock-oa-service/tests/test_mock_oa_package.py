import mock_oa_service


def test_package_exposes_version() -> None:
    assert mock_oa_service.__version__ == "0.1.0"
