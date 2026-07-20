import workhub


def test_package_exposes_version() -> None:
    assert workhub.__version__ == "0.1.0"
