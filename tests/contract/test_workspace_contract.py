import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

REPOSITORY_ROOT = Path(__file__).parents[2]


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def test_mock_oa_is_an_independent_workspace_package() -> None:
    workhub_config = load_toml(REPOSITORY_ROOT / "pyproject.toml")
    mock_oa_config = load_toml(REPOSITORY_ROOT / "mock-oa-service" / "pyproject.toml")

    assert workhub_config["tool"]["uv"]["workspace"]["members"] == ["mock-oa-service"]
    assert mock_oa_config["project"]["name"] == "mock-oa-service"
    assert mock_oa_config["project"]["requires-python"] == ">=3.12,<3.13"


def test_environment_example_covers_bootstrap_contract() -> None:
    entries = {
        key: value
        for line in (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", maxsplit=1)]
    }

    assert {
        "WORKHUB_BOOTSTRAP_ADMIN_USERNAME",
        "WORKHUB_BOOTSTRAP_ADMIN_PASSWORD",
        "WORKHUB_SESSION_SECRET",
    } <= entries.keys()
